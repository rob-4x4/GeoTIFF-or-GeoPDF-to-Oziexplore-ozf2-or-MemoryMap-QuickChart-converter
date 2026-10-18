# GeoTIFF QuickChart Converter

> **AI-generated code notice:** This repository contains AI-generated code.
> Review, test, and validate it before using it with maps or other data.

This is an experimental, standalone GeoTIFF/GeoPDF-to-Quick Chart converter for
a GDAL toolchain. It preserves the input file and writes a newly generated,
unencrypted, legacy Quick Chart v2 (`.qct`) map.

It is not a replacement for Memory-Map Navigator and does not implement QC3,
licensing, DRM, encrypted maps, or existing-map conversion. It uses GDAL to
derive WGS-84 calibration polynomials while retaining the input raster grid,
then writes legacy QCT tiles itself. It does not modify installed GDAL binaries
or plug-in folders.

## Requirements

- GDAL command-line tools on `PATH`: `gdal_translate`, `gdaltransform`, and
  `gdalinfo`.
- For GeoPDF input, a GDAL PDF read backend such as Poppler or PDFium.
- Python 3.10+ with Pillow.

Install the Python dependency on another machine with:

```powershell
python -m pip install -r requirements.txt
```

## Project status and publishing

This is a **prototype**. Its automated checks verify the writer's binary
layout, pixel round-trip, calibration coefficients, GPX waypoint generation,
the 128-colour pixel-packing path, the interpolation matrix, and a GDAL-backed
GeoTIFF conversion. They do not prove that Memory-Map will import or render
every generated QCT. Complete the one-map Navigator import check before any
bulk conversion.

### Image-quality policy

The converter aims to retain the source map's legibility as closely as legacy
QCT permits: it retains the original raster dimensions and pixel glyphs,
uses QCT's third-order WGS-84 calibration polynomials, supports all 128 QCT
palette entries, preserves small source palettes exactly, uses no
error-diffusion dithering, and generates a symmetric interpolation matrix for
scaled views. No raster reprojection or resampling is performed.

Legacy QCT cannot reproduce every GeoTIFF compression scheme or every source
colour. Treat a map as accepted only when labels, contours, linework, colours,
tile boundaries, and the GPX checkpoints have passed an Android/Memory-Map
visual check. Regenerate any QCT created by an earlier version of this
prototype: it used dithering and a placeholder interpolation matrix, which can
cause coloured fringes around map features.

The GitHub workflow runs the complete test suite (including its GDAL integration
test) on every push and pull request. Generated maps, GeoTIFFs, validation
logs, and GPX outputs are deliberately excluded from Git to avoid publishing
private/licensed maps or growing the repository with large binary outputs.

## Licence

The original code in this repository is licensed under the GNU General Public
License, version 3 only. See [`LICENSE`](LICENSE). Do not add map data or
third-party documents unless you have the right to redistribute them.

## Included test data

The repository includes maintainer-declared public-domain GeoTIFF fixtures for
reproducible tests. See [`test-data/README.md`](test-data/README.md) for their
SHA-256 hashes, test coverage, conversion commands, and the Android/Memory-Map
acceptance procedure. Generated QCT, GPX, and JSON evidence remains ignored by
Git.

Use the DuBois Pennsylvania UTM fixture for the first Android visual acceptance
test. The included `Argentina Range.tif` polar-stereographic Antarctic fixture
is retained as a regression case, but its WGS-84 reprojection has a trapezoidal
valid-data footprint with surrounding no-data and is not suitable for judging
visual map quality.

## Windows GUI

For repeat or multi-map conversions on Windows, start the included graphical
front end from the repository folder:

```powershell
python .\geotiff_qct_gui.py
```

Choose one or more GeoTIFF or GeoPDF inputs and an output folder. The GUI writes one QCT
per source file, plus the same JSON validation report created by the command
line tool. It protects existing QCT and GPX files unless **Allow replacement**
is deliberately selected. Source GeoTIFFs are never modified.

At startup the **GDAL readiness** panel checks that `gdalinfo`,
`gdal_translate`, and `gdaltransform` can run, that the GeoTIFF driver is
available, and whether the GeoPDF driver is present for future GeoPDF support.
If GDAL is not on `PATH`, use **Choose GDAL bin folder…** and select the folder
containing those programs. This setting applies only to the open GUI session;
it does not edit Windows environment variables, the registry, or GDAL
configuration files.

By default it also writes 3 by 3 image-frame validation GPX files. Enable
**projected-grid GPX checkpoints** only for projected, metre-based source maps
such as MGA or UTM; choose 50,000 metres for a manageable Australian 1:250,000
test overlay. The GUI shows individual success/failure reports so one unsuitable
map does not hide the results for the rest of a batch. Inputs with identical
file names must be sent to separate output folders to avoid QCT name collisions.

The GUI is a thin interface over `geotiff_to_qct.py`; it does not contain a
separate writer or change GDAL configuration. The same GDAL and Pillow
requirements apply.

### GeoPDF inputs

The GUI also accepts `.pdf` files that GDAL identifies as georeferenced PDFs.
Choose the applicable **GeoPDF page** and **rendering DPI** (300 is the default
starting point). A GeoPDF is rasterised by GDAL into a temporary georeferenced
GeoTIFF at that DPI, then processed by the normal tested QCT path. The temporary
TIFF is deleted after conversion unless the command-line `--keep-work` option
is used; the original PDF is never modified.

The validation JSON records the original PDF hash, chosen page and DPI, the
temporary raster dimensions and hash, and normal QCT validation results. PDF
pages without GDAL-readable georeferencing, password-protected PDFs, or maps
with unsuitable page/inset georeferencing are rejected rather than silently
creating an uncalibrated QCT. Complete the same one-map Android GPX/grid proof
before batch-converting a new publisher's PDFs.

From the command line, for example:

```powershell
python .\geotiff_to_qct.py '<source-map.pdf>' '<output-directory>\map.qct' `
  --pdf-page 1 --pdf-dpi 300 --validation-gpx `
  --projected-grid-gpx '<output-directory>\map-projected-grid-checks.gpx' `
  --projected-grid-metres 50000
```

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
round-trip validation, calibration-coefficient validation, the source/QCT
pixel dimensions, palette-entry count, and polynomial-fit error.

`--validation-gpx` writes a sibling `input.validation.gpx` file by default. It
contains a 3 by 3 set of named WGS-84 waypoints covering the corners, centre,
and edge/interior locations. Load this GPX overlay with the newly converted map
in Memory-Map to confirm that the QCT and GPX share the same image-frame
calibration. Use a denser grid only when the source map has enough detail:

```powershell
python .\geotiff_to_qct.py '<source-geotiff.tif>' '<output-directory>\map.qct' `
  --validation-gpx '<output-directory>\map-checks.gpx' --validation-grid 5
```

For the stronger, independent printed-grid check on a projected GeoTIFF, write
waypoints at exact source grid intersections without recreating or changing the
QCT. The Australian 1:250,000 maps use MGA metres; a 50 km spacing keeps the
overlay manageable:

```powershell
python .\geotiff_to_qct.py '<source-geotiff.tif>' `
  --source-grid-gpx '<output-directory>\map-projected-grid-checks.gpx' `
  --projected-grid-metres 50000
```

Load this GPX with the existing QCT, then select several waypoints. Each
`QCT-grid-E...-N...` waypoint must sit exactly on its matching printed easting /
northing grid crossing. This option is for projected source coordinate systems
whose units are metres, such as MGA and UTM.

## Rebuild from scratch

1. Copy this folder to the machine that has GDAL (including its GeoPDF backend
   when PDFs are needed) and Python/Pillow.
2. Confirm `gdaltransform --version`, `gdal_translate --version`, and
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
