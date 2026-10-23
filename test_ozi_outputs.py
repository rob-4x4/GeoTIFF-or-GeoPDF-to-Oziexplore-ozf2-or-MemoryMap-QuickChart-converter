"""Checks for OziExplorer calibration and MAPC2MAPC OZF2 handoff."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from geotiff_to_qct import convert_ozi_ozf2, write_ozi_map


class OziOutputTests(unittest.TestCase):
    def _info(self):
        return {
            "size": [400, 300],
            "geoTransform": [500000, 10, 0, 5800000, 0, -10],
            "coordinateSystem": {"wkt": "fixture-crs"},
        }

    def test_map_contains_25_georeferenced_calibration_points_and_four_frame_points(self):
        with tempfile.TemporaryDirectory(dir=".") as folder:
            root = Path(folder)
            source = root / "source.tif"
            source.write_bytes(b"fixture")
            target = root / "source.map"
            lon_lat = [(140 + index / 100000, -37 - index / 100000) for index in range(25)]
            with patch("geotiff_to_qct.transform_points", return_value=lon_lat):
                write_ozi_map(source, target, self._info(), "Fixture map")

            lines = target.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "OziExplorer Map Data File Version 2.1")
            self.assertEqual(lines[2], str(source.resolve()))
            self.assertEqual(sum(line.startswith("Point") for line in lines), 30)
            self.assertIn("Point01,xy,0,0,in,deg,37,0.00000000,S,140,0.00000000,E,grid,,,,S", lines)
            self.assertEqual([line for line in lines if line.startswith("MMPXY,")], [
                "MMPXY,1,0,0", "MMPXY,2,399,0", "MMPXY,3,0,299", "MMPXY,4,399,299",
            ])
            self.assertIn("IWH,Map Image Width/Height,400,300", lines)

    def test_mapc2mapc_outputs_are_published_as_pair_and_map_link_is_portable(self):
        with tempfile.TemporaryDirectory(dir=".") as folder:
            root = Path(folder)
            source = root / "source.tif"
            source.write_bytes(b"fixture")
            executable = root / "mapc2mapc64.exe"
            executable.write_bytes(b"stub")
            output = root / "Ozi"

            def fake_run(command, **kwargs):
                work = Path(kwargs["cwd"])
                image = work / source.name
                input_map = Path(command[1])
                self.assertTrue(image.is_file())
                self.assertEqual(input_map.read_text(encoding="utf-8").splitlines()[2], source.name)
                generated = work / "output"
                generated.mkdir()
                (generated / "source.ozf2").write_bytes(b"ozf2 fixture")
                (generated / "source.map").write_text(
                    "OziExplorer Map Data File Version 2.1\nSource\nold-temp.ozf2\n",
                    encoding="utf-8")
                class Result:
                    returncode = 0
                    stdout = ""
                    stderr = ""
                return Result()

            with patch("geotiff_to_qct.read_info", return_value=self._info()), \
                    patch("geotiff_to_qct.transform_points",
                          return_value=[(140 + index / 100000, -37 - index / 100000)
                                        for index in range(25)]), \
                    patch("geotiff_to_qct.subprocess.run", side_effect=fake_run):
                report = convert_ozi_ozf2(source, output, executable)

            self.assertEqual(Path(report["map"]).read_text(encoding="utf-8").splitlines()[2],
                             "source.ozf2")
            self.assertEqual(Path(report["ozf2"]).read_bytes(), b"ozf2 fixture")
            self.assertTrue(Path(report["map"]).is_file())


if __name__ == "__main__":
    unittest.main()
