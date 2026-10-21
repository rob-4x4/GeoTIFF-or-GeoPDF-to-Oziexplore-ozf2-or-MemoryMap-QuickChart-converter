# GeoTIFF Test Fixtures and Android Acceptance Test

> **AI-generated code notice:** The converter used in this procedure contains
> AI-generated code. Review, test, and validate it before use.

The repository maintainer has supplied the GeoTIFF files in `fixtures/` as
public-domain test data. They are committed so that a clone can reproduce the
conversion tests without downloading map data. Do not replace or add data
unless its redistribution rights have been independently verified.

## Fixtures

| File | Geographic/projection coverage | SHA-256 |
| --- | --- | --- |
| `2231CD_2018_ED4_GEO.cog.tif` | Cloud-Optimized GeoTIFF; Web Mercator | `1530fda6d5ac53d1d6903c885a526c5673e7cad9024f823bb6eedfeda9315912` |
| `Argentina Range.tif` | Polar Stereographic | `decb307398aaa0fba0ae942b7886852a141d4c5a2e5c40ee1dab4c9a79f4b266` |
| `DuBois-PA-41078-A7-24k.tif` | Pennsylvania, USA; UTM zone 17N | `2dddbf814c00f69b719eb5f36cf00e4fdb72358c9c627e704068522c0cbfb189` |
| `Karthaus-PA-N4100-w7800-24k.tif` | Pennsylvania, USA; UTM zone 17N | `8cabe9f9797c8254b4af91c975f354cbdf520f8ee66ea574f31859c01381442b` |
| `Yarraloola-SF50-06-250k.tif` | Western Australia; GDA2020 / MGA zone 50 | `66dc89b9d38287d769257da39fc42a6baa1acf55304b1245fad639178363685f` |

The fixtures total about 52 MiB. Generated conversion artefacts are not
committed.

## Polar-projection regression case

`Argentina Range.tif` covers an Antarctic-area map in Polar Stereographic.
An earlier reprojection-based approach produced a trapezoidal valid image
footprint with surrounding no-data. The current converter instead retains the
native raster grid. Keep this fixture for regression coverage, and use DuBois
for the initial Android visual acceptance test described below.

## Automated checks

Run the converter's built-in unit and GDAL integration tests before using a
fixture:

```powershell
python .\geotiff_to_qct.py --test
```

The suite currently contains ten tests. These check RLE and pixel-packed tile
encoding, non-dithered cartographic quantization, exact preservation of a
small cartographic palette, the symmetric interpolation matrix, binary layout,
calibration coefficients, GPX checkpoint creation, and small GeoTIFF and
GeoPDF end-to-end conversions. The GDAL integration tests may be skipped if
required tools or drivers are unavailable; review the test output and do not
count skipped tests as passes. The GeoPDF test requires GDAL PDF support.

## One-fixture conversion proof

Start with the DuBois UTM fixture rather than converting every map. The
converter retains its native raster grid and uses QCT calibration polynomials,
so this is a useful test of text, contours, and other fine cartography. Palette
quantization may change colours, so visual inspection is still necessary. This
creates new outputs only; it never overwrites the
fixture.

```powershell
$fixture = '.\test-data\fixtures\DuBois-PA-41078-A7-24k.tif'
$proof = '.\test-output\dubois-pa'

New-Item -ItemType Directory -Path $proof -Force | Out-Null
$before = (Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash

python .\geotiff_to_qct.py `
  $fixture `
  (Join-Path $proof 'dubois-pa.qct') `
  --validation-gpx (Join-Path $proof 'dubois-pa-checks.gpx') `
  --validation-grid 3

$after = (Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash
$before -eq $after
Get-Content (Join-Path $proof 'dubois-pa.qct.validation.json') -Raw
```

Before continuing, the hash comparison must be `True`. The JSON must report
`source_preserved: true`, `pixel_round_trip: true`, and a zero (or negligible)
`calibration_max_abs_error_degrees`. It also records the source and QCT pixel
dimensions, the number of QCT palette entries, and
`calibration_fit_max_abs_error_degrees` and
`inverse_calibration_fit_max_abs_error_pixels`. The source and QCT dimensions
must match, and `raster_reprojection` must report that native source pixels
were retained.

## Android / Memory-Map acceptance test

1. In Memory-Map Android, sign in and make sure the Navigator / Import Maps
   feature is active; it is required for third-party QCT maps.
2. Start the app once, then close it. Connect the Android device to the PC.
3. Copy only `dubois-pa.qct` and `dubois-pa-checks.gpx` from the
   proof folder to `Download/Memory-Map/` on internal storage. Do not copy the
   source GeoTIFF or validation JSON to the device.
4. Fully close and reopen Memory-Map. Open **Maps**, then **Maps Everywhere**;
   enable all map types if the QCT is not visible.
5. Open/import `dubois-pa-checks.gpx` as the overlay. It contains nine
   WGS-84 waypoints named `QCT-check-r#-c#`.
6. Inspect every waypoint against its expected position in the image frame.
   These image-frame points are not necessarily printed map-grid intersections.
   For an independent printed-grid comparison, generate projected-grid GPX
   checkpoints as described in the main README and compare their easting and
   northing with the corresponding printed crossings. Pan and zoom through
   the full map; there must be no missing tiles, corrupt colours, coloured
   fringes around text/contours, loss of readable linework, or tile seams.
7. Keep a screenshot and the generated validation JSON as the acceptance
   evidence. Do not convert further maps until this one-map proof passes.
   Regenerate QCTs made by earlier versions of this prototype before comparing
   them: those earlier outputs used dithering and a placeholder scaled-view
   interpolation table.

Memory-Map's Android map-loading guidance is available at
<https://memory-map.com/mm-android-help3/Loading_Your_Existing_Maps.htm>.
