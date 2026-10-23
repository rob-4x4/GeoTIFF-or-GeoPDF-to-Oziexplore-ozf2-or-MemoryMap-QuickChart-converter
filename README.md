# GeoTIFF/GeoPDF to Memory-Map QuickChart and OziExplorer OZF2 Converter

> **AI-generated code notice:** This repository contains AI-generated code.
> Review, test, and validate it before using it with maps or other data.

This is an experimental Windows application that converts GeoTIFF and GeoPDF
maps into Memory-Map QuickChart files. It always creates a newly generated,
unencrypted, legacy Quick Chart v2 (`.qct`) map and can optionally create an
OziExplorer OZF2 (`.ozf2`) map with its matching calibrated `.map` file. It
preserves the input file.

It is not a replacement for Memory-Map Navigator and does not implement QC3,
licensing, DRM, encrypted maps, or existing-map conversion. It uses GDAL to
derive WGS-84 calibration polynomials while retaining the source raster grid,
then writes legacy QCT tiles itself. GeoPDF pages are first rasterised at the
chosen DPI. It does not modify installed GDAL binaries
or plug-in folders.

## Requirements

- GDAL command-line tools on `PATH`: `gdal_translate`, `gdaltransform`, and
  `gdalinfo`.
- For GeoPDF input, a GDAL PDF read backend such as Poppler or PDFium.
- Python 3.10+ with Pillow.
- Optional OziExplorer OZF2 output: a separately installed MAPC2MAPC program
  with its documented `-az` batch export. Download MAPC2MAPC from its
  [official download page](https://www.the-thorns.org.uk/mapping/down.html).
  QuickChart does not download, install, or bundle MAPC2MAPC; select its
  executable in the GUI when enabling the OZF2 option. The 64-bit executable
  is recommended for 64-bit Windows. The unregistered version is a trial and
  places red X marks on generated maps; see the official site for registration
  details.
- Optional for the GUI's container renderer: Docker Desktop or Podman Desktop
  with its engine running and the selected PDFium image already pulled locally.

Install the Python dependency on another machine with:

```powershell
python -m pip install -r requirements.txt
```

## Project status and publishing

This is a **prototype**. Its automated checks verify the writer's binary
layout, pixel round-trip, calibration coefficients, GPX waypoint generation,
the 128-colour pixel-packing path, the interpolation matrix, and GDAL-backed
GeoTIFF and GeoPDF conversions when the required GDAL drivers are available.
Skipped tests are not passes. These checks do not prove that Memory-Map will import or render
every generated QCT. Complete the one-map Navigator import check before any
bulk conversion.

The Yarraloola GeoTIFF QCT and checkpoints have been visually checked in
Memory-Map on Android. A two-page GeoPDF has also been converted successfully
using page 1 at 300 DPI; its Memory-Map import and alignment have not yet been
confirmed. These results do not establish compatibility with every source map.

### Image-quality policy

The converter aims to retain the source map's legibility as closely as legacy
QCT permits: it retains the GeoTIFF raster dimensions, uses QCT's third-order
WGS-84 calibration polynomials, supports all 128 QCT
palette entries, preserves small source palettes exactly, uses no
error-diffusion dithering, and generates a symmetric interpolation matrix for
scaled views. GeoPDF pages are rendered at the selected DPI before conversion;
the resulting raster is not reprojected or resampled by the QCT conversion.
Maps with more than 128 colours are quantized, so individual colours may change.

Legacy QCT cannot reproduce every GeoTIFF compression scheme or every source
colour. Treat a map as accepted only when labels, contours, linework, colours,
tile boundaries, and the GPX checkpoints have passed an Android/Memory-Map
visual check. Regenerate any QCT created by an earlier version of this
prototype: it used dithering and a placeholder interpolation matrix, which can
cause coloured fringes around map features.

The GitHub workflow runs the test suite on every push and pull request; GDAL
integration tests can be skipped if their required tools or drivers are missing.
Generated maps, GeoTIFFs, validation
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
is retained as a regression case; an earlier reprojection-based approach gave
it a trapezoidal footprint with surrounding no-data. The current converter
retains the native raster grid instead.

## Windows GUI: Memory-Map QuickChart and OziExplorer OZF2

For repeat or multi-map conversions on Windows, start the included graphical
front end from the repository folder:

```powershell
python .\geotiff_qct_gui.py
```

Or double-click **Start GeoTIFF QuickChart.cmd** in the project folder. It
prefers the app's isolated environment if one was previously set up, then
checks installed Python/Pillow and GDAL command-line tools. If prerequisites
are missing, it offers to install an isolated environment under
`%LOCALAPPDATA%\GeoTIFFQuickChart`; this does not alter system PATH or another
Python/Conda installation. Setup downloads the official micromamba Windows
package and installs Python 3.12, Pillow, GDAL, and the GDAL PDF plugin from
conda-forge. It requires an internet connection and can take several minutes.
Choosing **No** lets the GUI open with any usable existing Python, but
conversions remain unavailable until GDAL is installed or selected in the GUI.
If you keep Python somewhere else, set the `QUICKCHART_PYTHON` environment
variable to its full `python.exe` path. The command window stays available
while the GUI runs and pauses if startup fails so the error can be read.

Choose one or more GeoTIFF or GeoPDF inputs and an output folder. The GUI writes one QCT
per source file, plus the same JSON validation report created by the command
line tool. It protects existing QCT and GPX files unless **Allow replacement**
is deliberately selected. Source GeoTIFFs and GeoPDFs are never modified.
During a batch, **Stop after current map** lets the active map finish safely
and prevents the remaining maps from starting. Any completed output is kept;
unstarted maps can be run later by selecting them and starting another batch.
Select **Also save GeoPDF pages as GeoTIFF (.tif)** to keep the rendered,
georeferenced page alongside each QCT, at the selected page and DPI. Existing
GeoTIFF outputs are protected unless replacement is enabled. The option applies
to PDF inputs only; existing GeoTIFF inputs are not duplicated. These TIFFs may
be large and should be tested in the intended application before batch use.

Select **Also create OziExplorer OZF2 + .map** to create a calibrated Ozi map
pair in its separately selected folder. This uses an existing MAPC2MAPC
installation in its documented command-line batch mode (`-az`); QuickChart
does not install or bundle MAPC2MAPC. The app writes a temporary Ozi MAP 2.1
calibration linked to the georeferenced raster, asks MAPC2MAPC to create the
OZF2 pair, then publishes the completed `.ozf2` and matching `.map` together.
For this handoff, the app makes a temporary raster copy beside the temporary
MAP file and references it by filename; this avoids MAPC2MAPC's image-location
prompt and ensures the selected source is never exposed to modification.
MAPC2MAPC must be installed separately and its executable selected in the
**MAPC2MAPC executable** field; conversion is unavailable if that file is not
selected or cannot be run. Use **Find on C: / D:** to check common install
folders on those drives (including Program Files); it does not scan entire
drives. If the executable is stored elsewhere, use **Choose MAPC2MAPC…** to
select it manually. The 64-bit executable is preferred when both are present.
For GeoPDF inputs the selected page is rendered with the chosen renderer and DPI
first. Existing OZF2/MAP outputs are protected unless replacement is enabled.
OZF2 is supported by OziExplorer for Windows and Android; Img2Ozf is not needed
for this option. Check one output pair in both intended OziExplorer versions,
including alignment against known grid crossings, before using it for a batch.
MAPC2MAPC's documented `-az` output is described as an Androzic/Ozi-compatible
OZF2 set, while the current official Ozi Img2Ozf utility creates OZF4 rather
than OZF2.

At startup the **GDAL readiness** panel checks that `gdalinfo`,
`gdal_translate`, and `gdaltransform` can run, that the GeoTIFF driver is
available, and whether the GeoPDF driver is present for GeoPDF conversion.
If GDAL is not on `PATH`, use **Choose GDAL bin folder…** and select the folder
containing those programs. This setting applies only to the open GUI session;
it does not edit Windows environment variables, the registry, or GDAL
configuration files.

### Docker or Podman PDFium rendering (optional GUI mode)

The GUI can use a Linux GDAL/PDFium image in Docker or Podman for GeoPDF page
discovery and rasterisation. Choose **Container PDFium** under **GeoPDF renderer** and leave
the default image as `docker.io/maptiler/gdal:3.11-pdfium-py3.13` for the image
used in the verified colour comparison. Click **Check image** before
conversion. Docker or Podman must be on `PATH`, its engine must be running, and
the image must already exist locally. The app checks the image without pulling
or changing it. Docker is preferred when both engines have the image; otherwise
the app tries Podman. If neither runtime is available, the readiness panel
warns that container PDFium is unavailable. QuickChart never installs Docker
or Podman. Pull the image manually using whichever engine you want:

```powershell
docker pull docker.io/maptiler/gdal:3.11-pdfium-py3.13
# Or, if using Podman:
podman pull docker.io/maptiler/gdal:3.11-pdfium-py3.13
```

For a GUI batch, the app starts one temporary, network-disabled container with
the selected engine and reuses it for all selected GeoPDFs, then removes it when the batch ends. Each
distinct source folder is mounted read-only; a temporary work folder is mounted
writable. The container runs only `gdalinfo` and `gdal_translate` for the PDFs.
The rendered georeferenced TIFFs are processed by the existing Windows GDAL
and QuickChart code; GeoTIFF inputs continue to use the Windows GDAL toolchain.
The source PDFs are never modified. Each validation JSON records the selected
renderer, image reference, and local image ID. The image ID helps identify the
exact build used even if the tag later changes.

Docker Desktop file sharing or Podman path sharing must allow access to the selected PDF folder and the
temporary work folder. If container startup or mounting fails, the map is
reported as failed and no alternate renderer is silently substituted. Start
with one PDF and a new output folder, inspect the saved GeoTIFF/QCT and
calibration checkpoints, then proceed with a batch only after the result is
accepted in the intended Android/Memory-Map app.

By default it also writes 3 by 3 image-frame validation GPX files. Enable
**projected-grid GPX checkpoints** only for projected, metre-based source maps
such as MGA or UTM; choose 50,000 metres for a manageable Australian 1:250,000
test overlay. The GUI shows individual success/failure reports so one unsuitable
map does not hide the results for the rest of a batch. Inputs with identical
file names must be sent to separate output folders to avoid QCT name collisions.
If a map frame contains no exact crossing at the selected projected-grid interval,
the converter still completes the QCT and other requested outputs, reports a
warning with zero projected-grid checkpoints, and does not create an empty GPX.
Use **Create grid GPX only** to generate or refresh projected-grid checkpoints
for already converted maps without rebuilding their QCTs. Select the original
GeoPDFs or GeoTIFFs (a QCT by itself does not retain the source projected CRS
needed for this check); GeoPDFs are temporarily rendered with the selected
renderer, page, and DPI. Existing GPX files are skipped unless replacement is
enabled, so the action can safely fill in missing checkpoints in a prior batch.

Use **GeoTIFF backfill only** when QCTs already exist but some rendered GeoTIFFs
are missing. Select the original GeoPDFs and choose the same output folder;
the app renders the selected page at the chosen DPI and writes only the `.tif`
files. Existing TIFFs are skipped by default, so this is suitable for filling
gaps in a large batch. If **Allow replacement of existing output files** is
enabled, the app asks for confirmation and replaces only the selected TIFFs.
This action never creates or changes QCTs, validation JSON, or GPX files. The
normal **Convert selected maps** action remains available when you want to
write QCTs and choose either or both GPX checkpoint types; its overwrite
setting applies to those selected outputs (and any optional GeoTIFF outputs).

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
pages without usable GDAL-readable georeferencing or PDFs GDAL cannot open
cannot be converted. A successful conversion does not establish that the chosen
page, map frame, inset or source georeferencing is correct. Check the selected
page and independently compare projected-grid checkpoints with printed grid
crossings where applicable. Complete the one-map Android GPX/grid proof before
batch-converting a new publisher's PDFs.

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
python -m unittest test_gui_outputs
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

`--validation-gpx` writes a sibling named after the output QCT, for example
`map.validation.gpx` for `map.qct`, by default. It
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

The tool does not change source GeoTIFFs or GeoPDFs, GDAL configuration, or
Memory-Map files. Delete a newly generated QCT and its validation log if the import proof
fails; the source file remains intact. Keep the JSON log with
any QCT accepted by Navigator as the conversion evidence.
