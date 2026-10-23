#!/usr/bin/env python3
"""Windows desktop front end for the GeoTIFF QuickChart Converter.

This UI deliberately calls the same conversion functions as the command-line
tool.  It does not contain a second QCT writer and it never alters a source
GeoTIFF.  Run it with ``python geotiff_qct_gui.py``.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from tkinter import BooleanVar, IntVar, StringVar, Tk, filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from geotiff_to_qct import (convert_geopdf, convert_geotiff,
                            DEFAULT_PDFIUM_CONTAINER_IMAGE, ContainerPdfiumSession,
                            container_runtime_status, probe_pdfium_container,
                            render_geopdf_to_geotiff,
                            write_projected_grid_validation,
                            write_projected_grid_validation_from_geopdf)


APPLICATION_TITLE = "GeoTIFF QuickChart Converter"


@dataclass(frozen=True)
class GdalStatus:
    """Results of a non-mutating GDAL capability check."""

    ready: bool
    version: str
    geotiff: bool
    geopdf: bool
    message: str


def probe_gdal(bin_directory: Path | None = None) -> GdalStatus:
    """Check the GDAL programs and drivers required by this application."""
    suffix = ".exe" if os.name == "nt" else ""

    def find(program: str) -> str | None:
        if bin_directory is not None:
            candidate = bin_directory / f"{program}{suffix}"
            return str(candidate) if candidate.is_file() else None
        return shutil.which(program)

    programs = {name: find(name) for name in ("gdalinfo", "gdal_translate", "gdaltransform")}
    missing = [name for name, location in programs.items() if location is None]
    if missing:
        return GdalStatus(False, "", False, False,
                          "Missing: " + ", ".join(missing) + ".")
    try:
        version_result = subprocess.run([programs["gdalinfo"], "--version"], text=True,
                                        capture_output=True, check=True)
        formats_result = subprocess.run([programs["gdalinfo"], "--formats"], text=True,
                                        capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        return GdalStatus(False, "", False, False, f"GDAL could not be run: {error}")
    version = version_result.stdout.strip() or version_result.stderr.strip()
    formats = formats_result.stdout
    geotiff = bool(re.search(r"^\s*GTiff\b", formats, re.MULTILINE))
    geopdf = bool(re.search(r"^\s*PDF\b", formats, re.MULTILINE))
    if not geotiff:
        return GdalStatus(False, version, False, geopdf, "GeoTIFF (GTiff) driver is not available.")
    pdf_status = "detected" if geopdf else "not detected"
    return GdalStatus(True, version, True, geopdf,
                      f"GDAL ready - {version}. GeoTIFF: available. GeoPDF driver: {pdf_status}.")


class ConverterWindow:
    """A safe, small batch-conversion UI around the tested CLI implementation."""

    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(APPLICATION_TITLE)
        self.root.minsize(800, 600)
        self.files: list[Path] = []
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.original_path = os.environ.get("PATH", "")
        self.gdal_bin_directory = StringVar()
        self.gdal_status_text = StringVar(value="Checking GDAL…")
        self.gdal_ready = False
        self.gdal_status: GdalStatus | None = None
        self.pdf_renderer = StringVar(value="Windows GDAL")
        self.pdfium_image = StringVar(value=DEFAULT_PDFIUM_CONTAINER_IMAGE)
        self.pdfium_status_text = StringVar(value="Optional - not checked")
        self.output_directory = StringVar()
        self.image_frame_gpx = BooleanVar(value=True)
        self.image_frame_grid = IntVar(value=3)
        self.projected_grid_gpx = BooleanVar(value=False)
        self.projected_grid_spacing = IntVar(value=50000)
        self.pdf_dpi = IntVar(value=300)
        self.pdf_page = IntVar(value=1)
        self.save_geotiff = BooleanVar(value=False)
        self.save_ozi_ozf2 = BooleanVar(value=False)
        self.ozi_output_directory = StringVar()
        self.mapc2mapc_executable = StringVar()
        self.allow_overwrite = BooleanVar(value=False)
        self._build()
        self._refresh_gdal()
        self.container_available, container_message = container_runtime_status()
        self.container_status_text.set(container_message)
        self.root.after(100, self._poll_events)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)
        outer.rowconfigure(7, weight=1)

        gdal = ttk.LabelFrame(outer, text="GDAL readiness", padding=8)
        gdal.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        gdal.columnconfigure(0, weight=1)
        gdal.columnconfigure(1, weight=2)
        ttk.Label(gdal, textvariable=self.gdal_status_text).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Entry(gdal, textvariable=self.gdal_bin_directory).grid(row=1, column=0, sticky="ew", pady=(7, 0))
        ttk.Button(gdal, text="Choose GDAL bin folder…", command=self._choose_gdal_folder).grid(
            row=1, column=1, padx=(7, 0), pady=(7, 0))
        ttk.Button(gdal, text="Refresh", command=self._refresh_gdal).grid(
            row=1, column=2, padx=(7, 0), pady=(7, 0))
        ttk.Label(gdal, text="GeoPDF renderer:").grid(row=2, column=0, sticky="w", pady=(7, 0))
        self.pdf_renderer_choice = ttk.Combobox(
            gdal, textvariable=self.pdf_renderer, values=("Windows GDAL", "Container PDFium"),
            state="readonly", width=18)
        self.pdf_renderer_choice.grid(row=3, column=0, sticky="ew", pady=(3, 0))
        self.pdfium_image_entry = ttk.Entry(gdal, textvariable=self.pdfium_image)
        self.pdfium_image_entry.grid(row=3, column=1, sticky="ew", padx=(7, 0), pady=(3, 0))
        self.pdfium_check_button = ttk.Button(gdal, text="Check image", command=self._check_pdfium_image)
        self.pdfium_check_button.grid(row=3, column=2, padx=(7, 0), pady=(3, 0))
        ttk.Label(gdal, textvariable=self.pdfium_status_text).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.container_status_text = StringVar(value="Checking optional Docker/Podman availability…")
        ttk.Label(gdal, textvariable=self.container_status_text).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(2, 0))

        ttk.Label(outer, text="Select GeoTIFF or GeoPDF source maps", font=("Segoe UI", 11, "bold")).grid(
            row=1, column=0, sticky="w")
        files_frame = ttk.Frame(outer)
        files_frame.grid(row=2, column=0, sticky="nsew", pady=(4, 10))
        files_frame.columnconfigure(0, weight=1)
        files_frame.rowconfigure(0, weight=1)
        self.file_list = ttk.Treeview(files_frame, columns=("file",), show="headings", height=8,
                                      selectmode="extended")
        self.file_list.heading("file", text="GeoTIFF or GeoPDF source file")
        self.file_list.column("file", width=680, stretch=True)
        self.file_list.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(files_frame, orient="vertical", command=self.file_list.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.file_list.configure(yscrollcommand=scrollbar.set)
        button_frame = ttk.Frame(files_frame)
        button_frame.grid(row=1, column=0, sticky="w", pady=(7, 0))
        self.add_button = ttk.Button(button_frame, text="Add map files…", command=self._add_files)
        self.add_button.grid(row=0, column=0, padx=(0, 6))
        self.remove_button = ttk.Button(button_frame, text="Remove selected", command=self._remove_selected)
        self.remove_button.grid(row=0, column=1, padx=(0, 6))
        self.clear_button = ttk.Button(button_frame, text="Clear list", command=self._clear_files)
        self.clear_button.grid(row=0, column=2)

        destination = ttk.LabelFrame(outer, text="Output", padding=8)
        destination.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        destination.columnconfigure(0, weight=1)
        ttk.Entry(destination, textvariable=self.output_directory).grid(row=0, column=0, sticky="ew")
        ttk.Button(destination, text="Choose output folder…", command=self._choose_output).grid(
            row=0, column=1, padx=(7, 0))
        ttk.Checkbutton(destination, text="Allow replacement of existing output files",
                        variable=self.allow_overwrite).grid(row=1, column=0, columnspan=2, sticky="w", pady=(7, 0))
        ttk.Checkbutton(destination, text="Also save GeoPDF pages as GeoTIFF (.tif)",
                        variable=self.save_geotiff).grid(row=2, column=0, columnspan=2, sticky="w", pady=(7, 0))
        self.ozi_output_check = ttk.Checkbutton(
            destination, text="Also create OziExplorer OZF2 + .map (requires MAPC2MAPC)",
            variable=self.save_ozi_ozf2)
        self.ozi_output_check.grid(row=3, column=0, columnspan=2, sticky="w", pady=(7, 0))
        ttk.Label(destination, text="OziExplorer output folder:").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.ozi_output_entry = ttk.Entry(destination, textvariable=self.ozi_output_directory)
        self.ozi_output_entry.grid(row=5, column=0, sticky="ew", pady=(3, 0))
        self.ozi_output_button = ttk.Button(destination, text="Choose Ozi folder…",
                                            command=self._choose_ozi_output)
        self.ozi_output_button.grid(row=5, column=1, padx=(7, 0), pady=(3, 0))
        ttk.Label(destination, text="MAPC2MAPC executable:").grid(row=6, column=0, sticky="w", pady=(6, 0))
        self.mapc2mapc_entry = ttk.Entry(destination, textvariable=self.mapc2mapc_executable)
        self.mapc2mapc_entry.grid(row=7, column=0, sticky="ew", pady=(3, 0))
        self.mapc2mapc_button = ttk.Button(destination, text="Choose MAPC2MAPC…",
                                           command=self._choose_mapc2mapc)
        self.mapc2mapc_button.grid(row=7, column=1, padx=(7, 0), pady=(3, 0))

        validation = ttk.LabelFrame(outer, text="Validation outputs", padding=8)
        validation.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        validation.columnconfigure(3, weight=1)
        ttk.Checkbutton(validation, text="Create image-frame GPX checkpoints", variable=self.image_frame_gpx).grid(
            row=0, column=0, sticky="w")
        ttk.Label(validation, text="points per side:").grid(row=0, column=1, sticky="e", padx=(16, 4))
        ttk.Spinbox(validation, from_=2, to=9, width=4, textvariable=self.image_frame_grid).grid(
            row=0, column=2, sticky="w")
        ttk.Checkbutton(validation, text="Create projected-grid GPX checkpoints (MGA / UTM only)",
                        variable=self.projected_grid_gpx).grid(row=1, column=0, sticky="w", pady=(7, 0))
        ttk.Label(validation, text="spacing, metres:").grid(row=1, column=1, sticky="e", padx=(16, 4), pady=(7, 0))
        ttk.Spinbox(validation, from_=1000, to=1000000, increment=1000, width=8,
                    textvariable=self.projected_grid_spacing).grid(row=1, column=2, sticky="w", pady=(7, 0))
        ttk.Label(validation, text="Enable projected-grid checks only for projected metre-based maps such as MGA or UTM.").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(7, 0))
        ttk.Label(validation, text="GeoPDF page:").grid(row=3, column=0, sticky="w", pady=(7, 0))
        ttk.Spinbox(validation, from_=1, to=999, width=5, textvariable=self.pdf_page).grid(
            row=3, column=1, sticky="w", pady=(7, 0))
        ttk.Label(validation, text="rendering DPI:").grid(row=3, column=2, sticky="e", padx=(16, 4), pady=(7, 0))
        ttk.Spinbox(validation, from_=72, to=1200, increment=25, width=6, textvariable=self.pdf_dpi).grid(
            row=3, column=3, sticky="w", pady=(7, 0))

        action = ttk.Frame(outer)
        action.grid(row=5, column=0, sticky="ew")
        self.convert_button = ttk.Button(action, text="Convert selected maps", command=self._start_conversion)
        self.convert_button.grid(row=0, column=0, sticky="w")
        self.grid_only_button = ttk.Button(action, text="Create grid GPX only",
                                           command=self._start_grid_generation)
        self.grid_only_button.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.tiff_only_button = ttk.Button(action, text="GeoTIFF backfill only",
                                           command=self._start_tiff_backfill)
        self.tiff_only_button.grid(row=0, column=2, sticky="w", padx=(8, 0))
        self.stop_button = ttk.Button(action, text="Stop after current map", command=self._request_stop,
                                      state="disabled")
        self.stop_button.grid(row=0, column=3, sticky="w", padx=(8, 0))
        self.progress = ttk.Progressbar(action, mode="determinate")
        self.progress.grid(row=0, column=4, sticky="ew", padx=(12, 0))
        action.columnconfigure(4, weight=1)

        ttk.Label(outer, text="Results", font=("Segoe UI", 11, "bold")).grid(row=6, column=0, sticky="w", pady=(12, 0))
        self.results = ScrolledText(outer, height=11, wrap="word", state="disabled")
        self.results.grid(row=7, column=0, sticky="nsew", pady=(4, 0))
        self._log("Ready. Source GeoTIFFs are never modified. Existing output is protected unless overwrite is enabled.")

    def _add_files(self) -> None:
        selected = filedialog.askopenfilenames(
            title="Select GeoTIFF or GeoPDF files",
            filetypes=[("Map files", "*.tif *.tiff *.pdf"), ("GeoTIFF", "*.tif *.tiff"),
                       ("GeoPDF", "*.pdf"), ("All files", "*.*")])
        known = {path.resolve() for path in self.files}
        for name in selected:
            path = Path(name).resolve()
            if path not in known:
                self.files.append(path)
                self.file_list.insert("", "end", iid=str(path), values=(str(path),))
                known.add(path)

    def _remove_selected(self) -> None:
        for item in self.file_list.selection():
            path = Path(item)
            self.files = [candidate for candidate in self.files if candidate != path]
            self.file_list.delete(item)

    def _clear_files(self) -> None:
        self.files.clear()
        for item in self.file_list.get_children():
            self.file_list.delete(item)

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(title="Choose a new or existing output folder")
        if selected:
            self.output_directory.set(selected)

    def _choose_ozi_output(self) -> None:
        selected = filedialog.askdirectory(title="Choose a folder for OZF2 and MAP outputs")
        if selected:
            self.ozi_output_directory.set(selected)

    def _choose_mapc2mapc(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select the MAPC2MAPC 64-bit executable",
            filetypes=[("Windows programs", "*.exe"), ("All files", "*.*")])
        if selected:
            self.mapc2mapc_executable.set(selected)

    def _choose_gdal_folder(self) -> None:
        selected = filedialog.askdirectory(title="Choose the folder containing GDAL programs")
        if selected:
            self.gdal_bin_directory.set(selected)
            self._refresh_gdal()

    def _refresh_gdal(self) -> None:
        entered = self.gdal_bin_directory.get().strip()
        folder = Path(entered).expanduser().resolve() if entered else None
        # This only changes the environment of this running GUI process. It does
        # not modify Windows PATH, the registry, GDAL configuration, or any files.
        os.environ["PATH"] = (str(folder) + os.pathsep if folder else "") + self.original_path
        status = probe_gdal(folder)
        self.gdal_status = status
        self.gdal_ready = status.ready
        self.gdal_status_text.set(status.message)

    def _check_pdfium_image(self) -> None:
        status = probe_pdfium_container(self.pdfium_image.get())
        self.pdfium_status_text.set(status.message)
        if not status.ready:
            messagebox.showerror(APPLICATION_TITLE, status.message)

    def _start_conversion(self) -> None:
        self._refresh_gdal()
        if not self.gdal_ready:
            messagebox.showerror(APPLICATION_TITLE,
                                 "GDAL is not ready. Select the folder containing gdalinfo, "
                                 "gdal_translate, and gdaltransform, then click Refresh.")
            return
        if not self.files:
            messagebox.showerror(APPLICATION_TITLE, "Add at least one GeoTIFF or GeoPDF source map.")
            return
        has_pdf = any(source.suffix.lower() == ".pdf" for source in self.files)
        renderer = self.pdf_renderer.get()
        pdfium_image = self.pdfium_image.get().strip()
        if has_pdf and renderer == "Windows GDAL" and not self.gdal_status.geopdf:
            messagebox.showerror(APPLICATION_TITLE,
                                 "GeoPDF input was selected, but the installed GDAL PDF driver is unavailable.")
            return
        if has_pdf and renderer == "Container PDFium":
            status = probe_pdfium_container(pdfium_image)
            self.pdfium_status_text.set(status.message)
            if not status.ready:
                messagebox.showerror(APPLICATION_TITLE, status.message)
                return
        if not self.output_directory.get().strip():
            messagebox.showerror(APPLICATION_TITLE, "Choose an output folder.")
            return
        if self.save_ozi_ozf2.get():
            if not self.ozi_output_directory.get().strip():
                messagebox.showerror(APPLICATION_TITLE, "Choose an OziExplorer output folder.")
                return
            executable = Path(self.mapc2mapc_executable.get().strip()).expanduser()
            if not executable.is_file():
                messagebox.showerror(APPLICATION_TITLE,
                                     "Select the MAPC2MAPC executable. QuickChart does not install or bundle it.")
                return
        if not self.image_frame_gpx.get() and not self.projected_grid_gpx.get():
            if not messagebox.askyesno(APPLICATION_TITLE,
                                       "No validation GPX files are selected. Continue anyway?"):
                return
        try:
            image_grid = int(self.image_frame_grid.get())
            projected_spacing = int(self.projected_grid_spacing.get())
            pdf_dpi = int(self.pdf_dpi.get())
            pdf_page = int(self.pdf_page.get())
            if not 2 <= image_grid <= 9:
                raise ValueError("Image-frame checkpoints must be between 2 and 9 per side.")
            if projected_spacing <= 0:
                raise ValueError("Projected-grid spacing must be a positive number of metres.")
            if not 72 <= pdf_dpi <= 1200:
                raise ValueError("GeoPDF rendering DPI must be between 72 and 1200.")
            if pdf_page < 1:
                raise ValueError("GeoPDF page must be 1 or greater.")
        except (TypeError, ValueError) as error:
            messagebox.showerror(APPLICATION_TITLE, str(error))
            return
        output = Path(self.output_directory.get()).expanduser().resolve()
        duplicate_stems = {source.stem for source in self.files if
                           sum(candidate.stem == source.stem for candidate in self.files) > 1}
        if duplicate_stems:
            names = ", ".join(sorted(duplicate_stems))
            messagebox.showerror(APPLICATION_TITLE,
                                 f"These sources have the same output file name: {names}\n"
                                 "Convert them to separate output folders.")
            return
        self.stop_event.clear()
        self._set_busy(True)
        self.progress.configure(maximum=len(self.files), value=0)
        self._log(f"Starting {len(self.files)} map conversion(s) into: {output}")
        settings = (output, self.image_frame_gpx.get(), image_grid, self.projected_grid_gpx.get(),
                    projected_spacing, self.allow_overwrite.get(), pdf_dpi, pdf_page,
                    self.save_geotiff.get(), renderer, pdfium_image,
                    self.save_ozi_ozf2.get(),
                    Path(self.ozi_output_directory.get()).expanduser().resolve()
                    if self.save_ozi_ozf2.get() else None,
                    Path(self.mapc2mapc_executable.get()).expanduser().resolve()
                    if self.save_ozi_ozf2.get() else None)
        threading.Thread(target=self._convert_all, args=(list(self.files), settings), daemon=True).start()

    def _start_grid_generation(self) -> None:
        self._refresh_gdal()
        if not self.gdal_ready:
            messagebox.showerror(APPLICATION_TITLE,
                                 "GDAL is not ready. Select the folder containing gdalinfo, "
                                 "gdal_translate, and gdaltransform, then click Refresh.")
            return
        if not self.files:
            messagebox.showerror(APPLICATION_TITLE, "Add the original GeoTIFF or GeoPDF source maps first.")
            return
        pdf_sources = [source for source in self.files if source.suffix.lower() == ".pdf"]
        other_sources = [source for source in self.files
                         if source.suffix.lower() not in (".pdf", ".tif", ".tiff")]
        if other_sources:
            messagebox.showerror(APPLICATION_TITLE,
                                 "Checkpoint-only generation accepts GeoTIFFs and GeoPDFs, not QCT files. "
                                 "For maps without a saved GeoTIFF, select the original GeoPDF.")
            return
        renderer = self.pdf_renderer.get()
        pdfium_image = self.pdfium_image.get().strip()
        if pdf_sources and renderer == "Windows GDAL" and not self.gdal_status.geopdf:
            messagebox.showerror(APPLICATION_TITLE,
                                 "GeoPDF input was selected, but the installed GDAL PDF driver is unavailable.")
            return
        if pdf_sources and renderer == "Container PDFium":
            status = probe_pdfium_container(pdfium_image)
            self.pdfium_status_text.set(status.message)
            if not status.ready:
                messagebox.showerror(APPLICATION_TITLE, status.message)
                return
        if not self.output_directory.get().strip():
            messagebox.showerror(APPLICATION_TITLE, "Choose an output folder for the projected-grid GPX files.")
            return
        try:
            spacing = int(self.projected_grid_spacing.get())
            pdf_dpi = int(self.pdf_dpi.get())
            pdf_page = int(self.pdf_page.get())
            if spacing <= 0:
                raise ValueError("Projected-grid spacing must be a positive number of metres.")
            if not 72 <= pdf_dpi <= 1200:
                raise ValueError("GeoPDF rendering DPI must be between 72 and 1200.")
            if pdf_page < 1:
                raise ValueError("GeoPDF page must be 1 or greater.")
        except (TypeError, ValueError) as error:
            messagebox.showerror(APPLICATION_TITLE, str(error))
            return
        duplicate_stems = {source.stem for source in self.files if
                           sum(candidate.stem == source.stem for candidate in self.files) > 1}
        if duplicate_stems:
            names = ", ".join(sorted(duplicate_stems))
            messagebox.showerror(APPLICATION_TITLE,
                                 f"These sources have the same output file name: {names}\n"
                                 "Choose one source per map or use separate output folders.")
            return
        output = Path(self.output_directory.get()).expanduser().resolve()
        existing = [output / f"{source.stem}-projected-grid-checks.gpx" for source in self.files
                    if (output / f"{source.stem}-projected-grid-checks.gpx").exists()]
        if self.allow_overwrite.get() and existing:
            if not messagebox.askyesno(
                    APPLICATION_TITLE,
                    f"Replace {len(existing)} existing projected-grid GPX file(s)?\n\n"
                    "Only these derived GPX files can change; QCTs, GeoTIFFs, and source maps are untouched. "
                    "If a selected map has no crossing at the chosen spacing, its old GPX will be removed."):
                return
        self.stop_event.clear()
        self._set_busy(True)
        self.progress.configure(maximum=len(self.files), value=0)
        self._log(f"Starting projected-grid GPX-only generation for {len(self.files)} map(s) into: {output}")
        settings = (output, spacing, pdf_dpi, pdf_page, self.allow_overwrite.get(), renderer, pdfium_image)
        threading.Thread(target=self._create_projected_grid_all,
                         args=(list(self.files), settings), daemon=True).start()

    def _start_tiff_backfill(self) -> None:
        """Render selected GeoPDFs as TIFFs only, without touching QCT or GPX files."""
        self._refresh_gdal()
        if not self.gdal_ready:
            messagebox.showerror(APPLICATION_TITLE,
                                 "GDAL is not ready. Select the folder containing gdalinfo, "
                                 "gdal_translate, and gdaltransform, then click Refresh.")
            return
        sources = [source for source in self.files if source.suffix.lower() == ".pdf"]
        if not sources:
            messagebox.showerror(APPLICATION_TITLE,
                                 "Select the original GeoPDF files for GeoTIFF backfill.")
            return
        if len(sources) != len(self.files):
            messagebox.showerror(APPLICATION_TITLE,
                                 "GeoTIFF backfill accepts GeoPDF files only. Remove any GeoTIFF inputs.")
            return
        renderer = self.pdf_renderer.get()
        pdfium_image = self.pdfium_image.get().strip()
        if renderer == "Windows GDAL" and not self.gdal_status.geopdf:
            messagebox.showerror(APPLICATION_TITLE,
                                 "GeoPDF input was selected, but the installed GDAL PDF driver is unavailable.")
            return
        if renderer == "Container PDFium":
            status = probe_pdfium_container(pdfium_image)
            self.pdfium_status_text.set(status.message)
            if not status.ready:
                messagebox.showerror(APPLICATION_TITLE, status.message)
                return
        if not self.output_directory.get().strip():
            messagebox.showerror(APPLICATION_TITLE, "Choose the folder for the GeoTIFF files.")
            return
        try:
            dpi = int(self.pdf_dpi.get())
            page = int(self.pdf_page.get())
            if not 72 <= dpi <= 1200:
                raise ValueError("GeoPDF rendering DPI must be between 72 and 1200.")
            if page < 1:
                raise ValueError("GeoPDF page must be 1 or greater.")
        except (TypeError, ValueError) as error:
            messagebox.showerror(APPLICATION_TITLE, str(error))
            return
        duplicate_stems = {source.stem for source in sources
                           if sum(candidate.stem == source.stem for candidate in sources) > 1}
        if duplicate_stems:
            messagebox.showerror(APPLICATION_TITLE,
                                 "Selected PDFs have duplicate names: " + ", ".join(sorted(duplicate_stems)) +
                                 ". Use separate output folders.")
            return
        output = Path(self.output_directory.get()).expanduser().resolve()
        existing = [output / f"{source.stem}.tif" for source in sources
                    if (output / f"{source.stem}.tif").exists()]
        overwrite = self.allow_overwrite.get()
        if overwrite and existing and not messagebox.askyesno(
                APPLICATION_TITLE,
                f"Replace {len(existing)} existing GeoTIFF file(s)?\n\n"
                "This backfill action changes only the selected .tif files. QCTs and GPX files are untouched."):
            return
        self.stop_event.clear()
        self._set_busy(True)
        self.progress.configure(maximum=len(sources), value=0)
        disposition = "replacing selected TIFFs when present" if overwrite else "filling missing TIFFs only"
        self._log(f"Starting GeoTIFF-only backfill for {len(sources)} GeoPDF(s) into: {output}; {disposition}.")
        settings = (output, dpi, page, overwrite, renderer, pdfium_image)
        threading.Thread(target=self._backfill_geotiffs_all,
                         args=(sources, settings), daemon=True).start()

    def _backfill_geotiffs_all(self, sources: list[Path],
                               settings: tuple[Path, int, int, bool, str, str]) -> None:
        output, dpi, page, overwrite, renderer, pdfium_image = settings
        planned = [output / f"{source.stem}.tif" for source in sources]
        source_paths = {source.resolve() for source in sources}
        conflicts = [path for path in planned if path.resolve() in source_paths]
        if conflicts:
            self.events.put(("preflight_failure", conflicts))
            self.events.put(("tiff_finished", (0, len(sources), False, 0)))
            return
        output.mkdir(parents=True, exist_ok=True)
        pdfium_session = None
        pdfium_work_context = None
        if renderer == "Container PDFium":
            try:
                pdfium_work_context = tempfile.TemporaryDirectory(prefix="quickchart-tiff-pdfium-batch-")
                pdfium_session = ContainerPdfiumSession(
                    pdfium_image, sources, Path(pdfium_work_context.name))
                pdfium_session.__enter__()
            except Exception as error:
                if pdfium_session is not None and pdfium_session.started:
                    try:
                        pdfium_session.__exit__(type(error), error, error.__traceback__)
                    except Exception:
                        pass
                if pdfium_work_context is not None:
                    pdfium_work_context.cleanup()
                self.events.put(("batch_failure", f"Could not start the shared PDFium container: {error}"))
                self.events.put(("tiff_finished", (0, len(sources), False, 0)))
                return

        processed = 0
        skipped = 0
        for index, source in enumerate(sources, start=1):
            if self.stop_event.is_set():
                break
            target = output / f"{source.stem}.tif"
            if target.exists() and not overwrite:
                skipped += 1
                processed += 1
                self.events.put(("tiff_skipped", (index, target)))
                continue
            try:
                report = render_geopdf_to_geotiff(
                    source, target, dpi=dpi, page=page, overwrite=overwrite,
                    pdfium_container_image=(pdfium_image if renderer == "Container PDFium"
                                            and pdfium_session is None else None),
                    pdfium_session=pdfium_session)
                self.events.put(("tiff_success", (index, report)))
            except Exception as error:
                self.events.put(("failure", (index, source, str(error), traceback.format_exc())))
            processed += 1

        if pdfium_session is not None:
            try:
                pdfium_session.__exit__(None, None, None)
            except Exception as error:
                self.events.put(("batch_failure", f"Could not clean up the shared PDFium container: {error}"))
        if pdfium_work_context is not None:
            pdfium_work_context.cleanup()
        self.events.put(("tiff_finished", (processed, len(sources), self.stop_event.is_set(), skipped)))

    def _create_projected_grid_all(self, sources: list[Path],
                                   settings: tuple[Path, int, int, int, bool, str, str]) -> None:
        output, spacing, pdf_dpi, pdf_page, overwrite, renderer, pdfium_image = settings
        planned = [output / f"{source.stem}-projected-grid-checks.gpx" for source in sources]
        source_paths = {path.resolve() for path in sources}
        conflicts = [path for path in planned if path.resolve() in source_paths]
        if conflicts:
            self.events.put(("preflight_failure", conflicts))
            self.events.put(("grid_finished", (0, len(sources), False, 0)))
            return
        output.mkdir(parents=True, exist_ok=True)
        pdf_sources = [source for source in sources if source.suffix.lower() == ".pdf"]
        pdfium_session = None
        pdfium_work_context = None
        if renderer == "Container PDFium" and pdf_sources:
            try:
                pdfium_work_context = tempfile.TemporaryDirectory(prefix="quickchart-grid-pdfium-batch-")
                pdfium_session = ContainerPdfiumSession(
                    pdfium_image, pdf_sources, Path(pdfium_work_context.name))
                pdfium_session.__enter__()
            except Exception as error:
                if pdfium_session is not None and pdfium_session.started:
                    try:
                        pdfium_session.__exit__(type(error), error, error.__traceback__)
                    except Exception:
                        pass
                if pdfium_work_context is not None:
                    pdfium_work_context.cleanup()
                self.events.put(("batch_failure", f"Could not start the shared PDFium container: {error}"))
                self.events.put(("grid_finished", (0, len(sources), False, 0)))
                return

        processed = 0
        skipped = 0
        for index, source in enumerate(sources, start=1):
            if self.stop_event.is_set():
                break
            target = output / f"{source.stem}-projected-grid-checks.gpx"
            if target.exists() and not overwrite:
                skipped += 1
                processed += 1
                self.events.put(("grid_skipped", (index, target)))
                continue
            try:
                if source.suffix.lower() == ".pdf":
                    count = write_projected_grid_validation_from_geopdf(
                        source, target, spacing, dpi=pdf_dpi, page=pdf_page,
                        pdfium_container_image=(pdfium_image if renderer == "Container PDFium"
                                                and pdfium_session is None else None),
                        pdfium_session=pdfium_session)
                else:
                    count = write_projected_grid_validation(source, target, spacing)
                warning = None
                if count == 0:
                    warning = (f"No projected-grid intersections were found at {spacing} m spacing; "
                               "no new GPX was written.")
                    if target.exists():
                        if overwrite:
                            target.unlink()
                            warning += " The old GPX was removed to avoid leaving stale checkpoints."
                        else:
                            warning += " The existing GPX was left unchanged."
                self.events.put(("grid_success", (index, {
                    "source": str(source),
                    "projected_grid_gpx": str(target) if count else None,
                    "projected_grid_checkpoints": count,
                    "spacing_metres": spacing,
                    "warning": warning,
                })))
            except Exception as error:
                self.events.put(("failure", (index, source, str(error), traceback.format_exc())))
            processed += 1

        if pdfium_session is not None:
            try:
                pdfium_session.__exit__(None, None, None)
            except Exception as error:
                self.events.put(("batch_failure", f"Could not clean up the shared PDFium container: {error}"))
        if pdfium_work_context is not None:
            pdfium_work_context.cleanup()
        self.events.put(("grid_finished", (processed, len(sources), self.stop_event.is_set(), skipped)))

    def _convert_all(self, sources: list[Path], settings: tuple) -> None:
        (output, use_frame_gpx, frame_grid, use_projected_gpx, spacing, overwrite,
         pdf_dpi, pdf_page, save_tiff, renderer, pdfium_image, *ozi_settings) = settings
        save_ozi = ozi_settings[0] if len(ozi_settings) > 0 else False
        ozi_output = ozi_settings[1] if len(ozi_settings) > 1 else None
        mapc2mapc = ozi_settings[2] if len(ozi_settings) > 2 else None
        planned = []
        for source in sources:
            destination = output / f"{source.stem}.qct"
            planned.append(destination)
            if save_tiff and source.suffix.lower() == ".pdf":
                planned.append(output / f"{source.stem}.tif")
            if save_ozi and ozi_output is not None:
                planned.extend((ozi_output / f"{source.stem}.map",
                                ozi_output / f"{source.stem}.ozf2"))
            if use_frame_gpx:
                planned.append(output / f"{source.stem}-checks.gpx")
            if use_projected_gpx:
                planned.append(output / f"{source.stem}-projected-grid-checks.gpx")
        source_paths = {path.resolve() for path in sources}
        conflicts = [path for path in planned if path.resolve() in source_paths]
        if conflicts:
            self.events.put(("preflight_failure", conflicts))
            self.events.put(("finished", (0, len(sources), False)))
            return
        existing = [path for path in planned if path.exists()]
        if existing and not overwrite:
            self.events.put(("preflight_failure", existing))
            self.events.put(("finished", (0, len(sources), False)))
            return
        output.mkdir(parents=True, exist_ok=True)
        pdfium_session = None
        pdfium_work_context = None
        pdf_sources = [source for source in sources if source.suffix.lower() == ".pdf"]
        if renderer == "Container PDFium" and pdf_sources:
            try:
                pdfium_work_context = tempfile.TemporaryDirectory(prefix="quickchart-pdfium-batch-")
                pdfium_session = ContainerPdfiumSession(
                    pdfium_image, pdf_sources, Path(pdfium_work_context.name))
                pdfium_session.__enter__()
            except Exception as error:
                if pdfium_session is not None and pdfium_session.started:
                    try:
                        pdfium_session.__exit__(type(error), error, error.__traceback__)
                    except Exception:
                        pass
                if pdfium_work_context is not None:
                    pdfium_work_context.cleanup()
                self.events.put(("batch_failure", f"Could not start the shared PDFium container: {error}"))
                self.events.put(("finished", (0, len(sources), False)))
                return
        processed = 0
        for index, source in enumerate(sources, start=1):
            if self.stop_event.is_set():
                break
            try:
                destination = output / f"{source.stem}.qct"
                frame_gpx = output / f"{source.stem}-checks.gpx" if use_frame_gpx else None
                projected_gpx = output / f"{source.stem}-projected-grid-checks.gpx" if use_projected_gpx else None
                if source.suffix.lower() == ".pdf":
                    report = convert_geopdf(source, destination, dpi=pdf_dpi, page=pdf_page,
                                            overwrite=overwrite, validation_gpx=frame_gpx,
                                            validation_grid=frame_grid, projected_grid_gpx=projected_gpx,
                                            projected_grid_spacing=spacing,
                                            save_geotiff=output / f"{source.stem}.tif" if save_tiff else None,
                                            ozi_output_directory=ozi_output if save_ozi else None,
                                            mapc2mapc_executable=mapc2mapc if save_ozi else None,
                                            pdfium_container_image=(pdfium_image if renderer == "Container PDFium"
                                                                    and pdfium_session is None else None),
                                            pdfium_session=pdfium_session)
                    projected_count = report.get("projected_grid_checkpoints")
                else:
                    report = convert_geotiff(source, destination, overwrite=overwrite,
                                             validation_gpx=frame_gpx, validation_grid=frame_grid,
                                             ozi_output_directory=ozi_output if save_ozi else None,
                                             mapc2mapc_executable=mapc2mapc if save_ozi else None)
                    projected_count = None
                    if projected_gpx is not None:
                        projected_count = write_projected_grid_validation(source, projected_gpx, spacing)
                        report["projected_grid_gpx"] = str(projected_gpx) if projected_count else None
                        report["projected_grid_checkpoints"] = projected_count
                        if projected_count == 0:
                            report["projected_grid_warning"] = (
                                f"No projected-grid intersections were found at {spacing} m spacing; "
                                "the map was converted without a projected-grid GPX."
                            )
                        destination.with_suffix(destination.suffix + ".validation.json").write_text(
                            json.dumps(report, indent=2) + "\n", encoding="utf-8")
                result = {"source": str(source), "qct": str(destination),
                          "input_type": "GeoPDF" if source.suffix.lower() == ".pdf" else "GeoTIFF",
                          "frame_gpx": str(frame_gpx) if frame_gpx else None,
                          "projected_grid_gpx": report.get("projected_grid_gpx"),
                          "projected_grid_checkpoints": projected_count,
                          "projected_grid_warning": report.get("projected_grid_warning"),
                          "source_preserved": report["source_preserved"],
                          "qct_palette_entries": report["qct_palette_entries"],
                          "saved_geotiff": report.get("geopdf", {}).get("saved_geotiff"),
                          "ozi": report.get("ozi"),
                          "ozi_error": report.get("ozi_error"),
                          "geopdf": report.get("geopdf")}
                self.events.put(("success", (index, result)))
            except Exception as error:  # Surface one map failure without abandoning a batch.
                self.events.put(("failure", (index, source, str(error), traceback.format_exc())))
            processed += 1
        if pdfium_session is not None:
            try:
                pdfium_session.__exit__(None, None, None)
            except Exception as error:
                self.events.put(("batch_failure", f"Could not clean up the shared PDFium container: {error}"))
        if pdfium_work_context is not None:
            pdfium_work_context.cleanup()
        self.events.put(("finished", (processed, len(sources), self.stop_event.is_set())))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "success":
                    index, result = payload  # type: ignore[misc]
                    self.progress.configure(value=index)
                    if result.get("projected_grid_warning"):
                        self._log("WARNING: " + result["projected_grid_warning"])
                    if result.get("ozi_error"):
                        self._log("WARNING: OziExplorer OZF2 output was not created: " + result["ozi_error"])
                    self._log("SUCCESS\n" + json.dumps(result, indent=2))
                elif kind == "grid_success":
                    index, result = payload  # type: ignore[misc]
                    self.progress.configure(value=index)
                    if result.get("warning"):
                        self._log("WARNING: " + result["warning"])
                    self._log("GRID GPX RESULT\n" + json.dumps(result, indent=2))
                elif kind == "grid_skipped":
                    index, path = payload  # type: ignore[misc]
                    self.progress.configure(value=index)
                    self._log(f"SKIPPED (existing GPX protected): {path}")
                elif kind == "tiff_success":
                    index, result = payload  # type: ignore[misc]
                    self.progress.configure(value=index)
                    self._log("GEOTIFF BACKFILL SUCCESS\n" + json.dumps(result, indent=2))
                elif kind == "tiff_skipped":
                    index, path = payload  # type: ignore[misc]
                    self.progress.configure(value=index)
                    self._log(f"SKIPPED (existing GeoTIFF protected): {path}")
                elif kind == "failure":
                    index, source, message, details = payload  # type: ignore[misc]
                    self.progress.configure(value=index)
                    self._log(f"FAILED: {source}\n{message}\n{details}")
                elif kind == "preflight_failure":
                    existing = "\n".join(str(path) for path in payload)  # type: ignore[union-attr]
                    self._log("NO FILES WERE CONVERTED. Existing output is protected:\n" + existing)
                elif kind == "batch_failure":
                    self._log(str(payload))
                elif kind == "finished":
                    self._set_busy(False)
                    processed, total, stopped = payload  # type: ignore[misc]
                    if stopped:
                        self._log(f"Stopped after the current map. Processed {processed} of {total}; "
                                  "remaining maps were not started.")
                    else:
                        self._log(f"Finished {processed} of {total} map conversion(s). "
                                  "Review the results above before import.")
                elif kind == "grid_finished":
                    self._set_busy(False)
                    processed, total, stopped, skipped = payload  # type: ignore[misc]
                    if stopped:
                        self._log(f"Stopped after the current map. Processed {processed} of {total}; "
                                  "remaining maps were not started.")
                    else:
                        self._log(f"Finished projected-grid GPX generation for {processed} of {total} map(s). "
                                  f"Skipped {skipped} existing GPX file(s).")
                elif kind == "tiff_finished":
                    self._set_busy(False)
                    processed, total, stopped, skipped = payload  # type: ignore[misc]
                    if stopped:
                        self._log(f"Stopped after the current map. Processed {processed} of {total}; "
                                  "remaining maps were not started.")
                    else:
                        self._log(f"Finished GeoTIFF-only backfill for {processed} of {total} GeoPDF(s). "
                                  f"Skipped {skipped} existing TIFF file(s). QCT and GPX outputs were not changed.")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for widget in (self.add_button, self.remove_button, self.clear_button, self.convert_button,
                       self.grid_only_button, self.tiff_only_button,
                       self.pdfium_image_entry, self.pdfium_check_button, self.ozi_output_check,
                       self.ozi_output_entry, self.ozi_output_button,
                       self.mapc2mapc_entry, self.mapc2mapc_button):
            widget.configure(state=state)
        self.stop_button.configure(state="normal" if busy and not self.stop_event.is_set() else "disabled")
        self.pdf_renderer_choice.configure(state="disabled" if busy else "readonly")

    def _request_stop(self) -> None:
        if not self.stop_event.is_set():
            self.stop_event.set()
            self.stop_button.configure(state="disabled")
            self._log("Stop requested. The current map will finish safely; no additional maps will start.")

    def _log(self, message: str) -> None:
        self.results.configure(state="normal")
        self.results.insert("end", message.rstrip() + "\n\n")
        self.results.see("end")
        self.results.configure(state="disabled")


def main() -> None:
    root = Tk()
    ConverterWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
