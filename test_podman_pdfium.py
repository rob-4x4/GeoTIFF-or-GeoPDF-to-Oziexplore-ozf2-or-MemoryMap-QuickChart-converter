"""Unit tests for the optional Podman PDFium GeoPDF renderer."""

import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from geotiff_to_qct import (DEFAULT_PDFIUM_CONTAINER_IMAGE, PodmanPdfiumSession,
                            pdf_page_datasets, probe_podman_pdfium)


class PodmanPdfiumTests(unittest.TestCase):
    def test_probe_requires_local_image_and_pdfium_backend(self):
        completed = [
            CompletedProcess([], 0, "", ""),
            CompletedProcess([], 0, 'GDAL 3.11.4 "stable"', ""),
            CompletedProcess([], 0, "Driver: PDF/Geospatial PDF\nHAVE_PDFIUM=YES", ""),
        ]
        with patch("geotiff_to_qct.shutil.which", return_value="podman.exe"), \
                patch("geotiff_to_qct.subprocess.run", side_effect=completed) as run:
            status = probe_podman_pdfium(DEFAULT_PDFIUM_CONTAINER_IMAGE)
        self.assertTrue(status.ready)
        self.assertIn("3.11.4", status.version)
        self.assertEqual(run.call_args_list[0].args[0][1:4],
                         ["image", "exists", DEFAULT_PDFIUM_CONTAINER_IMAGE])
        for call in run.call_args_list[1:]:
            self.assertIn("--pull=never", call.args[0])

    def test_probe_does_not_pull_an_image_that_is_missing(self):
        with patch("geotiff_to_qct.shutil.which", return_value="podman.exe"), \
                patch("geotiff_to_qct.subprocess.run",
                      return_value=CompletedProcess([], 125, "", "not found")) as run:
            status = probe_podman_pdfium("missing/image:tag")
        self.assertFalse(status.ready)
        self.assertIn("not available locally", status.message)
        self.assertEqual(run.call_count, 1)

    def test_session_uses_read_only_input_and_writable_temp_mount(self):
        info = {
            "metadata": {"SUBDATASETS": {
                "SUBDATASET_1_NAME": "PDF:/quickchart/input/map.pdf:1",
                "SUBDATASET_1_DESC": "Page 1",
                "SUBDATASET_2_NAME": "PDF:/quickchart/input/map.pdf:2",
                "SUBDATASET_2_DESC": "Page 2",
            }}
        }
        completed = [
            CompletedProcess([], 0, "sha256:local-image-id", ""),
            CompletedProcess([], 0, "container-id", ""),
            CompletedProcess([], 0, json.dumps(info), ""),
            CompletedProcess([], 0, "", ""),
            CompletedProcess([], 0, "", ""),
        ]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "maps with spaces" / "map.pdf"
            source.parent.mkdir()
            source.write_bytes(b"pdf")
            work = root / "temporary work"
            work.mkdir()
            staged = work / "rendered-page.tif"
            with patch("geotiff_to_qct.shutil.which", return_value="podman.exe"), \
                    patch("geotiff_to_qct.subprocess.run", side_effect=completed) as run:
                with PodmanPdfiumSession(DEFAULT_PDFIUM_CONTAINER_IMAGE, source, work) as renderer:
                    pages = pdf_page_datasets(source, reader=renderer.read_info,
                                              dataset_name=renderer.dataset_path)
                    renderer.render_page(pages[1], staged, 150)
            self.assertEqual(pages, ["PDF:/quickchart/input/map.pdf:1",
                                     "PDF:/quickchart/input/map.pdf:2"])
            start_args = run.call_args_list[1].args[0]
            mounts = [start_args[index + 1] for index, item in enumerate(start_args[:-1])
                      if item == "--mount"]
            self.assertTrue(any("target=/quickchart/input" in mount and mount.endswith(",ro")
                                for mount in mounts))
            self.assertTrue(any("target=/quickchart/work" in mount and mount.endswith(",rw")
                                for mount in mounts))
            translate_args = run.call_args_list[3].args[0]
            self.assertIn("/quickchart/work/rendered-page.tif", translate_args)
            self.assertIn("DPI=150", translate_args)
            self.assertEqual(renderer.image_id, "sha256:local-image-id")
            self.assertEqual(run.call_args_list[-1].args[0][1:3], ["rm", "--force"])

    def test_session_mounts_multiple_source_folders_read_only(self):
        completed = [
            CompletedProcess([], 0, "sha256:local-image-id", ""),
            CompletedProcess([], 0, "container-id", ""),
            CompletedProcess([], 0, "", ""),
        ]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first_dir, second_dir, work = root / "first", root / "second", root / "work"
            for directory in (first_dir, second_dir, work):
                directory.mkdir()
            first_pdf, second_pdf = first_dir / "map.pdf", second_dir / "map.pdf"
            first_pdf.write_bytes(b"pdf1")
            second_pdf.write_bytes(b"pdf2")
            with patch("geotiff_to_qct.shutil.which", return_value="podman.exe"), \
                    patch("geotiff_to_qct.subprocess.run", side_effect=completed) as run:
                with PodmanPdfiumSession(DEFAULT_PDFIUM_CONTAINER_IMAGE,
                                         [first_pdf, second_pdf], work) as renderer:
                    self.assertEqual(renderer.dataset_path(first_pdf), "/quickchart/input-0/map.pdf")
                    self.assertEqual(renderer.dataset_path(second_pdf), "/quickchart/input-1/map.pdf")
            start_args = run.call_args_list[1].args[0]
            mounts = [start_args[index + 1] for index, item in enumerate(start_args[:-1])
                      if item == "--mount"]
            self.assertEqual(sum(mount.endswith(",ro") for mount in mounts), 2)


if __name__ == "__main__":
    unittest.main()
