"""Headless checks for the GUI's batch output safety."""

import queue
import tempfile
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
            settings = (root, False, 3, False, 50000, True, 150, 1, True)
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
            settings = (root, False, 3, False, 50000, False, 150, 1, True)
            with patch("geotiff_qct_gui.convert_geopdf") as convert:
                window._convert_all([pdf], settings)
                convert.assert_not_called()
            self.assertEqual(window.events.get()[0], "preflight_failure")
            self.assertEqual(tif.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
