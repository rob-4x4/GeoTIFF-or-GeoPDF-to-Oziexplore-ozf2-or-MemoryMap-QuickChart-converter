# One-map QCT proof

This is the isolated proof conversion made from the existing
`100k-proof-default-profile.tif` GeoTIFF. The original remained outside this
folder and its SHA-256 was unchanged before and after conversion.

## Files

- `100k-proof-default-profile.qct` is the experimental legacy QCT v2 map.
- `100k-proof-default-profile.validation.gpx` is a 3 by 3 WGS-84 checkpoint
  overlay for visual georeferencing checks in Memory-Map.
- `100k-proof-default-profile.qct.validation.json` records the evidence:
  source/output hashes, source preservation, decoded tile dimensions, exact
  pixel round-trip result, and calibration-coefficient result.

The converted map is 48,136,826 bytes and internally validates as an
unencrypted legacy QCT v2 map. Its QCT decoder reconstructed the exact
palette-indexed pixels and matched the WGS-84 affine calibration with a maximum
measured error of 0.0 degrees.

## Memory-Map acceptance test

1. Use a separate test map location in Memory-Map; do not replace an existing
   map or map folder.
2. Add `100k-proof-default-profile.qct` to that test location.
3. Import or load `100k-proof-default-profile.validation.gpx` as an overlay.
4. At each `QCT-check-r#-c#` waypoint, compare the GPX marker with the matching
   labelled latitude/longitude or printed grid intersection on the map.
5. Record whether the map loads and whether all nine points are visually aligned.

Do not batch-convert maps until that acceptance test passes.
