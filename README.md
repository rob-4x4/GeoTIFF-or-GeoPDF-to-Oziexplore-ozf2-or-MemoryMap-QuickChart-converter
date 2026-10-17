# GeoTIFF QuickChart Converter

> **AI-generated code notice:** This repository contains AI-generated code.
> Review, test, and validate it before using it with maps or other data.

This is an experimental, standalone GeoTIFF-to-Quick Chart converter for a GDAL
toolchain.  It preserves the input file and writes a newly generated,
unencrypted, legacy Quick Chart v2 (`.qct`) map.

It is not a replacement for Memory-Map Navigator and does not implement QC3,
licensing, DRM, encrypted maps, or existing-map conversion.  It calls GDAL to
reproject the input to WGS-84 and render RGB pixels.  It then writes legacy QCT
tiles itself, so it does not modify the installed GDAL binaries or plug-in folder.

## Requirements

- GDAL command-line tools on `PATH`: `gdalwarp`, `gdal_translate`, and
  `gdalinfo`.
- Python 3.10+ with Pillow.

Install the Python dependency on another machine with:

```powershell
python -m pip install -r requirements.txt
```

## Project status and publishing

This is a **prototype**. Its automated checks verify the writer's binary
layout, pixel round-trip, calibration coefficients, GPX waypoint generation,
and a GDAL-backed GeoTIFF conversion. They do not prove that Memory-Map will
import or render every generated QCT. Complete the one-map Navigator import
check before any bulk conversion.

The GitHub workflow runs the complete test suite (including its GDAL integration
test) on every push and pull request. Generated maps, GeoTIFFs, validation
logs, and GPX outputs are deliberately excluded from Git to avoid publishing
private/licensed maps or growing the repository with large binary outputs.

## Licence

The original code in this repository is licensed under the GNU General Public
License, version 3 only. See [`LICENSE`](LICENSE). Do not add map data or
third-party documents unless you have the right to redistribute them.

## One-map proof

Run the automated tests first:

```powershell
python .\geotiff_to_qct.py --test
```

Then convert exactly one input, writing to a new folder:

```powershell
python .\geotiff_to_qct.py `
  '<source-geotiff.tif>' '<output-directory>\map.qct' --validation-gpx
```

The converter refuses to overwrite an existing QCT unless `--overwrite` is
explicitly supplied.  It writes a neighbouring `.validation.json` log containing
source/output SHA-256 hashes, source-preservation confirmation, tile/pixel
round-trip validation, and calibration-coefficient validation.

`--validation-gpx` writes a sibling `input.validation.gpx` file by default. It
contains a 3 by 3 set of named WGS-84 waypoints covering the corners, centre,
and edge/interior grid locations. Load this GPX overlay with the newly converted
map in Memory-Map and compare the waypoint names and coordinates with the
matching printed latitude/longitude or map-grid intersections. Use a denser
grid only when the source map has enough labelled intersections:

```powershell
python .\geotiff_to_qct.py '<source-geotiff.tif>' '<output-directory>\map.qct' `
  --validation-gpx '<output-directory>\map-checks.gpx' --validation-grid 5
```

## Rebuild from scratch

1. Copy this folder to the machine that has GDAL and Python/Pillow.
2. Confirm `gdalwarp --version`, `gdal_translate --version`, and
   `gdalinfo --version` work in the intended shell.
3. Install Pillow into the Python environment if needed:
   `python -m pip install Pillow`.
4. Run `python geotiff_to_qct.py --test`.
5. Perform the one-map proof above.  Import that QCT into Memory-Map Navigator
   before converting further maps.

## Recovery and rollback

The tool does not change source GeoTIFFs, GDAL configuration, or Memory-Map
files.  Delete a newly generated QCT and its validation log if the import proof
fails; the source GeoTIFF and its SHA-256 remain intact.  Keep the JSON log with
any QCT accepted by Navigator as the conversion evidence.
