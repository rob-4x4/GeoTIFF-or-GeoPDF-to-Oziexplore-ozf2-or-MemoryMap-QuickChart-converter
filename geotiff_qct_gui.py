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
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from tkinter import BooleanVar, IntVar, StringVar, Tk, filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from geotiff_to_qct import (convert_geopdf, convert_geotiff,
                            write_projected_grid_validation)


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
        self.original_path = os.environ.get("PATH", "")
        self.gdal_bin_directory = StringVar()
        self.gdal_status_text = StringVar(value="Checking GDAL…")
        self.gdal_ready = False
        self.gdal_status: GdalStatus | None = None
        self.output_directory = StringVar()
        self.image_frame_gpx = BooleanVar(value=True)
        self.image_frame_grid = IntVar(value=3)
        self.projected_grid_gpx = BooleanVar(value=False)
        self.projected_grid_spacing = IntVar(value=50000)
        self.pdf_dpi = IntVar(value=300)
        self.pdf_page = IntVar(value=1)
        self.allow_overwrite = BooleanVar(value=False)
        self._build()
        self._refresh_gdal()
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
        ttk.Label(gdal, textvariable=self.gdal_status_text).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Entry(gdal, textvariable=self.gdal_bin_directory).grid(row=1, column=0, sticky="ew", pady=(7, 0))
        ttk.Button(gdal, text="Choose GDAL bin folder…", command=self._choose_gdal_folder).grid(
            row=1, column=1, padx=(7, 0), pady=(7, 0))
        ttk.Button(gdal, text="Refresh", command=self._refresh_gdal).grid(
            row=1, column=2, padx=(7, 0), pady=(7, 0))

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
        ttk.Checkbutton(destination, text="Allow replacement of existing QCT and GPX files",
                        variable=self.allow_overwrite).grid(row=1, column=0, columnspan=2, sticky="w", pady=(7, 0))

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
        self.progress = ttk.Progressbar(action, mode="determinate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=(12, 0))
        action.columnconfigure(1, weight=1)

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
        if any(source.suffix.lower() == ".pdf" for source in self.files) and not self.gdal_status.geopdf:
            messagebox.showerror(APPLICATION_TITLE,
                                 "GeoPDF input was selected, but the installed GDAL PDF driver is unavailable.")
            return
        if not self.output_directory.get().strip():
            messagebox.showerror(APPLICATION_TITLE, "Choose an output folder.")
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
        self._set_busy(True)
        self.progress.configure(maximum=len(self.files), value=0)
        self._log(f"Starting {len(self.files)} map conversion(s) into: {output}")
        settings = (output, self.image_frame_gpx.get(), image_grid, self.projected_grid_gpx.get(),
                    projected_spacing, self.allow_overwrite.get(), pdf_dpi, pdf_page)
        threading.Thread(target=self._convert_all, args=(list(self.files), settings), daemon=True).start()

    def _convert_all(self, sources: list[Path],
                     settings: tuple[Path, bool, int, bool, int, bool, int, int]) -> None:
        output, use_frame_gpx, frame_grid, use_projected_gpx, spacing, overwrite, pdf_dpi, pdf_page = settings
        planned = []
        for source in sources:
            destination = output / f"{source.stem}.qct"
            planned.append(destination)
            if use_frame_gpx:
                planned.append(output / f"{source.stem}-checks.gpx")
            if use_projected_gpx:
                planned.append(output / f"{source.stem}-projected-grid-checks.gpx")
        existing = [path for path in planned if path.exists()]
        if existing and not overwrite:
            self.events.put(("preflight_failure", existing))
            self.events.put(("finished", 0))
            return
        output.mkdir(parents=True, exist_ok=True)
        for index, source in enumerate(sources, start=1):
            try:
                destination = output / f"{source.stem}.qct"
                frame_gpx = output / f"{source.stem}-checks.gpx" if use_frame_gpx else None
                projected_gpx = output / f"{source.stem}-projected-grid-checks.gpx" if use_projected_gpx else None
                if source.suffix.lower() == ".pdf":
                    report = convert_geopdf(source, destination, dpi=pdf_dpi, page=pdf_page,
                                            overwrite=overwrite, validation_gpx=frame_gpx,
                                            validation_grid=frame_grid, projected_grid_gpx=projected_gpx,
                                            projected_grid_spacing=spacing)
                    projected_count = report.get("projected_grid_checkpoints")
                else:
                    report = convert_geotiff(source, destination, overwrite=overwrite,
                                             validation_gpx=frame_gpx, validation_grid=frame_grid)
                    projected_count = None
                    if projected_gpx is not None:
                        projected_count = write_projected_grid_validation(source, projected_gpx, spacing)
                        report["projected_grid_gpx"] = str(projected_gpx)
                        report["projected_grid_checkpoints"] = projected_count
                        destination.with_suffix(destination.suffix + ".validation.json").write_text(
                            json.dumps(report, indent=2) + "\n", encoding="utf-8")
                result = {"source": str(source), "qct": str(destination),
                          "input_type": "GeoPDF" if source.suffix.lower() == ".pdf" else "GeoTIFF",
                          "frame_gpx": str(frame_gpx) if frame_gpx else None,
                          "projected_grid_gpx": str(projected_gpx) if projected_gpx else None,
                          "projected_grid_checkpoints": projected_count,
                          "source_preserved": report["source_preserved"],
                          "qct_palette_entries": report["qct_palette_entries"],
                          "geopdf": report.get("geopdf")}
                self.events.put(("success", (index, result)))
            except Exception as error:  # Surface one map failure without abandoning a batch.
                self.events.put(("failure", (index, source, str(error), traceback.format_exc())))
        self.events.put(("finished", len(sources)))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "success":
                    index, result = payload  # type: ignore[misc]
                    self.progress.configure(value=index)
                    self._log("SUCCESS\n" + json.dumps(result, indent=2))
                elif kind == "failure":
                    index, source, message, details = payload  # type: ignore[misc]
                    self.progress.configure(value=index)
                    self._log(f"FAILED: {source}\n{message}\n{details}")
                elif kind == "preflight_failure":
                    existing = "\n".join(str(path) for path in payload)  # type: ignore[union-attr]
                    self._log("NO FILES WERE CONVERTED. Existing output is protected:\n" + existing)
                elif kind == "finished":
                    self._set_busy(False)
                    self._log(f"Finished {payload} map conversion(s). Review the results above before import.")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for widget in (self.add_button, self.remove_button, self.clear_button, self.convert_button):
            widget.configure(state=state)

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
