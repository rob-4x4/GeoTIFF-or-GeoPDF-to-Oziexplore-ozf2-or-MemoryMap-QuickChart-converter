"""Headless checks for the GUI's batch output safety."""

import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from geotiff_qct_gui import ConverterWindow


class GuiOutputTests(unittest.TestCase):
    def test_selected_source_is_never_overwritten_even_if_replacement_enabled(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / "map.pdf"
            tif = root / "map.tif"
            pdf.write_bytes(b"pdf")
            tif.write_bytes(b"original")
            window = object.__new__(ConverterWindow)
            window.events = queue.Queue()
            window.stop_event = threading.Event()
            settings = (root, False, 3, False, 50000, True, 150, 1, True, "Windows GDAL", "")
            with patch("geotiff_qct_gui.convert_geopdf") as convert:
                window._convert_all([pdf, tif], settings)
                convert.assert_not_called()
            self.assertEqual(window.events.get()[0], "preflight_failure")
            self.assertEqual(tif.read_bytes(), b"original")

    def test_existing_tiff_blocks_batch_when_replacement_is_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / "map.pdf"
            tif = root / "map.tif"
            pdf.write_bytes(b"pdf")
            tif.write_bytes(b"original")
            window = object.__new__(ConverterWindow)
            window.events = queue.Queue()
            window.stop_event = threading.Event()
            settings = (root, False, 3, False, 50000, False, 150, 1, True, "Windows GDAL", "")
            with patch("geotiff_qct_gui.convert_geopdf") as convert:
                window._convert_all([pdf], settings)
                convert.assert_not_called()
            self.assertEqual(window.events.get()[0], "preflight_failure")
            self.assertEqual(tif.read_bytes(), b"original")

    def test_stop_request_finishes_active_map_and_does_not_start_next(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            maps = [root / "first.tif", root / "second.tif"]
            for path in maps:
                path.write_bytes(b"source")
            window = object.__new__(ConverterWindow)
            window.events = queue.Queue()
            window.stop_event = threading.Event()
            settings = (root, False, 3, False, 50000, False, 150, 1, False, "Windows GDAL", "")

            def finish_first_map(*args, **kwargs):
                window.stop_event.set()
                return {"source_preserved": True, "qct_palette_entries": 128}

            with patch("geotiff_qct_gui.convert_geotiff", side_effect=finish_first_map) as convert:
                window._convert_all(maps, settings)

            self.assertEqual(convert.call_count, 1)
            events = []
            while not window.events.empty():
                events.append(window.events.get())
            self.assertEqual(events[-1], ("finished", (1, 2, True)))

    def test_pdfium_session_is_shared_across_pdf_maps_in_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            maps = [root / "first.pdf", root / "second.pdf"]
            for path in maps:
                path.write_bytes(b"pdf")
            window = object.__new__(ConverterWindow)
            window.events = queue.Queue()
            window.stop_event = threading.Event()
            settings = (root, False, 3, False, 5000, False, 150, 1, False,
                        "Container PDFium", "example/gdal-pdfium:latest")
            report = {"source_preserved": True, "qct_palette_entries": 128, "geopdf": {}}

            with patch("geotiff_qct_gui.ContainerPdfiumSession") as session_type, \
                    patch("geotiff_qct_gui.convert_geopdf", return_value=report) as convert:
                session = session_type.return_value
                window._convert_all(maps, settings)

            session_type.assert_called_once()
            session.__enter__.assert_called_once()
            session.__exit__.assert_called_once_with(None, None, None)
            self.assertEqual(convert.call_count, 2)
            self.assertIs(convert.call_args_list[0].kwargs["pdfium_session"], session)
            self.assertIs(convert.call_args_list[1].kwargs["pdfium_session"], session)

    def test_missing_projected_grid_crossing_is_warning_not_batch_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "map.tif"
            source.write_bytes(b"source")
            window = object.__new__(ConverterWindow)
            window.events = queue.Queue()
            window.stop_event = threading.Event()
            settings = (root, False, 3, True, 5000, False, 150, 1, False,
                        "Windows GDAL", "")
            report = {"source_preserved": True, "qct_palette_entries": 128}

            with patch("geotiff_qct_gui.convert_geotiff", return_value=report), \
                    patch("geotiff_qct_gui.write_projected_grid_validation", return_value=0):
                window._convert_all([source], settings)

            success = window.events.get_nowait()
            self.assertEqual(success[0], "success")
            result = success[1][1]
            self.assertIsNone(result["projected_grid_gpx"])
            self.assertEqual(result["projected_grid_checkpoints"], 0)
            self.assertIn("No projected-grid intersections", result["projected_grid_warning"])
            self.assertEqual(window.events.get_nowait(), ("finished", (1, 1, False)))

    def test_ozi_ozf2_options_are_forwarded_and_outputs_are_preflight_protected(self):
        with tempfile.TemporaryDirectory(dir=".") as folder:
            root = Path(folder)
            source = root / "map.tif"
            source.write_bytes(b"source")
            ozi = root / "ozi"
            executable = root / "mapc2mapc64.exe"
            executable.write_bytes(b"stub")
            window = object.__new__(ConverterWindow)
            window.events = queue.Queue()
            window.stop_event = threading.Event()
            settings = (root / "qct", False, 3, False, 50000, False, 150, 1, False,
                        "Windows GDAL", "", True, ozi, executable)
            (ozi).mkdir()
            (ozi / "map.ozf2").write_bytes(b"existing")

            with patch("geotiff_qct_gui.convert_geotiff") as convert:
                window._convert_all([source], settings)
                convert.assert_not_called()
            self.assertEqual(window.events.get_nowait()[0], "preflight_failure")
            self.assertEqual(window.events.get_nowait()[0], "finished")

            (ozi / "map.ozf2").unlink()
            with patch("geotiff_qct_gui.convert_geotiff", return_value={
                    "source_preserved": True, "qct_palette_entries": 11,
                    "ozi": {"map": str(ozi / "map.map"), "ozf2": str(ozi / "map.ozf2")},
                    }) as convert:
                window._convert_all([source], settings)
            self.assertEqual(convert.call_args.kwargs["ozi_output_directory"], ozi)
            self.assertEqual(convert.call_args.kwargs["mapc2mapc_executable"], executable)
            self.assertEqual(window.events.get_nowait()[0], "success")

    def test_grid_only_generation_skips_existing_and_never_runs_qct_conversion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tiff = root / "existing-map.tif"
            pdf = root / "missing-grid-map.pdf"
            tiff.write_bytes(b"source tif")
            pdf.write_bytes(b"source pdf")
            existing_gpx = root / "existing-map-projected-grid-checks.gpx"
            existing_gpx.write_bytes(b"keep this existing checkpoint file")
            window = object.__new__(ConverterWindow)
            window.events = queue.Queue()
            window.stop_event = threading.Event()
            settings = (root, 5000, 150, 1, False, "Windows GDAL", "")

            with patch("geotiff_qct_gui.write_projected_grid_validation", return_value=0) as tif_grid, \
                    patch("geotiff_qct_gui.write_projected_grid_validation_from_geopdf",
                          return_value=3) as pdf_grid, \
                    patch("geotiff_qct_gui.convert_geotiff") as convert_tif, \
                    patch("geotiff_qct_gui.convert_geopdf") as convert_pdf:
                window._create_projected_grid_all([tiff, pdf], settings)

            tif_grid.assert_not_called()
            pdf_grid.assert_called_once()
            convert_tif.assert_not_called()
            convert_pdf.assert_not_called()
            self.assertEqual(existing_gpx.read_bytes(), b"keep this existing checkpoint file")
            events = []
            while not window.events.empty():
                events.append(window.events.get())
            self.assertEqual(events[0][0], "grid_skipped")
            self.assertEqual(events[1][0], "grid_success")
            self.assertEqual(events[-1], ("grid_finished", (2, 2, False, 1)))

    def test_grid_only_overwrite_removes_stale_gpx_when_no_crossing_exists(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "map.tif"
            source.write_bytes(b"source tif")
            old_gpx = root / "map-projected-grid-checks.gpx"
            old_gpx.write_bytes(b"old spacing")
            window = object.__new__(ConverterWindow)
            window.events = queue.Queue()
            window.stop_event = threading.Event()
            settings = (root, 5000, 150, 1, True, "Windows GDAL", "")

            with patch("geotiff_qct_gui.write_projected_grid_validation", return_value=0):
                window._create_projected_grid_all([source], settings)

            self.assertFalse(old_gpx.exists())
            result = window.events.get_nowait()
            self.assertEqual(result[0], "grid_success")
            self.assertIn("old GPX was removed", result[1][1]["warning"])

    def test_tiff_backfill_skips_existing_tiff_and_never_touches_qct_or_gpx(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / "map.pdf"
            pdf.write_bytes(b"source pdf")
            output = root / "output"
            output.mkdir()
            tiff = output / "map.tif"
            qct = output / "map.qct"
            gpx = output / "map-checks.gpx"
            tiff.write_bytes(b"keep tiff")
            qct.write_bytes(b"keep qct")
            gpx.write_bytes(b"keep gpx")
            window = object.__new__(ConverterWindow)
            window.events = queue.Queue()
            window.stop_event = threading.Event()
            settings = (output, 150, 1, False, "Windows GDAL", "")

            with patch("geotiff_qct_gui.render_geopdf_to_geotiff") as render:
                window._backfill_geotiffs_all([pdf], settings)

            render.assert_not_called()
            self.assertEqual(tiff.read_bytes(), b"keep tiff")
            self.assertEqual(qct.read_bytes(), b"keep qct")
            self.assertEqual(gpx.read_bytes(), b"keep gpx")
            self.assertEqual(window.events.get_nowait()[0], "tiff_skipped")
            self.assertEqual(window.events.get_nowait(), ("tiff_finished", (1, 1, False, 1)))

    def test_tiff_backfill_overwrite_replaces_only_tiff(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / "map.pdf"
            pdf.write_bytes(b"source pdf")
            output = root / "output"
            output.mkdir()
            tiff = output / "map.tif"
            qct = output / "map.qct"
            gpx = output / "map-checks.gpx"
            tiff.write_bytes(b"old tiff")
            qct.write_bytes(b"keep qct")
            gpx.write_bytes(b"keep gpx")
            window = object.__new__(ConverterWindow)
            window.events = queue.Queue()
            window.stop_event = threading.Event()
            settings = (output, 150, 1, True, "Windows GDAL", "")
            report = {"saved_geotiff": str(tiff), "source_preserved": True}

            def render(*args, **kwargs):
                tiff.write_bytes(b"new tiff")
                return report

            with patch("geotiff_qct_gui.render_geopdf_to_geotiff", side_effect=render) as renderer:
                window._backfill_geotiffs_all([pdf], settings)

            renderer.assert_called_once()
            self.assertEqual(tiff.read_bytes(), b"new tiff")
            self.assertEqual(qct.read_bytes(), b"keep qct")
            self.assertEqual(gpx.read_bytes(), b"keep gpx")
            self.assertEqual(window.events.get_nowait()[0], "tiff_success")
            self.assertEqual(window.events.get_nowait(), ("tiff_finished", (1, 1, False, 0)))


if __name__ == "__main__":
    unittest.main()
