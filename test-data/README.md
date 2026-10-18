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
When GDAL reprojects its rectangular source grid into WGS-84 latitude/longitude,
the valid image footprint becomes trapezoidal inside a rectangular target extent.
The resulting QCT can therefore contain surrounding black/no-data pixels and is
visually poor for an Android map demonstration. Keep this fixture for regression
coverage, but **do not use it as the Android visual acceptance map**.

## Automated checks

Run the converter's built-in unit and GDAL integration tests before using a
fixture:

```powershell
python .\geotiff_to_qct.py --test
```

All eight tests must pass. These tests check RLE and pixel-packed tile
encoding, non-dithered cartographic quantization, exact preservation of a
small cartographic palette, the symmetric interpolation matrix, binary layout,
calibration coefficients, GPX checkpoint creation, and a small GeoTIFF
end-to-end conversion.

## One-fixture conversion proof

Start with the DuBois UTM fixture rather than converting every map. It is a
better visual acceptance map because its local-area UTM reprojection remains
close to rectangular. This creates new outputs only; it never overwrites the
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
dimensions, the number of QCT palette entries, and `reprojection_resampling`.
The default must be `near`; this avoids false blended colours around the
source's cartographic linework.

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
6. Inspect every waypoint. Corner and centre points must land on the expected
   map-grid intersections or other identifiable features. Pan and zoom through
   the full map; there must be no missing tiles, corrupt colours, coloured
   fringes around text/contours, loss of readable linework, or tile seams.
7. Keep a screenshot and the generated validation JSON as the acceptance
   evidence. Do not convert further maps until this one-map proof passes.
   Regenerate QCTs made by earlier versions of this prototype before comparing
   them: those earlier outputs used dithering and a placeholder scaled-view
   interpolation table.

Memory-Map's Android map-loading guidance is available at
<https://memory-map.com/mm-android-help3/Loading_Your_Existing_Maps.htm>.
