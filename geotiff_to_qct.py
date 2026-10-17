#!/usr/bin/env python3
"""Convert a GeoTIFF into an unencrypted legacy Memory-Map Quick Chart (.qct).

This is intentionally a standalone tool.  It calls GDAL only to reproject a
source raster to WGS-84 and render an RGB PNG; QCT writing and validation are
implemented here.  It does not read, decrypt, modify, or overwrite existing
QCT/QC3 maps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image


TILE = 64
MAX_COLOURS = 127  # 128 is reserved by the legacy on-disk RLE discriminator.
HEADER_SIZE = 0x45A0
MAGIC = 0x1423D5FF
VERSION = 2


@dataclass(frozen=True)
class GeoTransform:
    """GDAL affine transform in WGS-84 longitude/latitude coordinates."""

    values: tuple[float, float, float, float, float, float]

    def xy_to_lon_lat(self, x: float, y: float) -> tuple[float, float]:
        a, b, c, d, e, f = self.values
        return a + b * x + c * y, d + e * x + f * y


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_program(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"Required GDAL program not found on PATH: {name}")
    return found


def run(command: list[str]) -> None:
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise RuntimeError(f"GDAL command failed ({command[0]}): {message}")


def read_info(dataset: Path) -> dict:
    gdalinfo = require_program("gdalinfo")
    completed = subprocess.run([gdalinfo, "-json", str(dataset)], text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "gdalinfo failed")
    return json.loads(completed.stdout)


def to_little_u32(buffer: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<I", buffer, offset, value)


def to_little_f64(buffer: bytearray, offset: int, value: float) -> None:
    struct.pack_into("<d", buffer, offset, value)


def append_ascii(buffer: bytearray, value: str) -> int:
    offset = len(buffer)
    buffer.extend(value.encode("ascii", "replace"))
    buffer.append(0)
    return offset


def reverse_six_bits(value: int) -> int:
    result = 0
    for _ in range(6):
        result = (result << 1) | (value & 1)
        value >>= 1
    return result


def palette_bits(colour_count: int) -> int:
    if not 1 <= colour_count <= MAX_COLOURS:
        raise ValueError("QCT RLE supports 1 to 127 palette entries")
    return max(0, math.ceil(math.log2(colour_count)))


def encode_rle(indices: bytes) -> bytes:
    """Encode exactly one interlaced 64x64 tile using legacy QCT RLE."""
    subpalette: list[int] = []
    lookup: dict[int, int] = {}
    for colour in indices:
        if colour not in lookup:
            lookup[colour] = len(subpalette)
            subpalette.append(colour)
    bits = palette_bits(len(subpalette))
    repeat_bits = 8 - bits
    maximum_repeat = (1 << repeat_bits) - 1
    result = bytearray([len(subpalette), *subpalette])
    pos = 0
    while pos < len(indices):
        colour = indices[pos]
        end = pos + 1
        while end < len(indices) and indices[end] == colour:
            end += 1
        remaining = end - pos
        while remaining:
            length = min(remaining, maximum_repeat)
            result.append((length << bits) | lookup[colour])
            remaining -= length
        pos = end
    return bytes(result)


def decode_rle(encoded: bytes) -> bytes:
    if not encoded or encoded[0] == 0 or encoded[0] > MAX_COLOURS:
        raise ValueError("not a legacy QCT RLE tile")
    palette_count = encoded[0]
    if len(encoded) < palette_count + 1:
        raise ValueError("truncated QCT sub-palette")
    subpalette = encoded[1 : 1 + palette_count]
    bits = palette_bits(palette_count)
    mask = (1 << bits) - 1
    decoded = bytearray()
    for value in encoded[1 + palette_count :]:
        colour_index = value & mask
        repeat = value >> bits
        if colour_index >= palette_count or repeat == 0:
            raise ValueError("invalid QCT RLE item")
        decoded.extend([subpalette[colour_index]] * repeat)
        if len(decoded) > TILE * TILE:
            raise ValueError("QCT RLE tile expands past 4096 pixels")
    if len(decoded) != TILE * TILE:
        raise ValueError("QCT RLE tile does not expand to 4096 pixels")
    return bytes(decoded)


def quantize(image: Image.Image) -> Image.Image:
    rgb = image.convert("RGB")
    return rgb.quantize(colors=MAX_COLOURS, method=Image.Quantize.MEDIANCUT,
                        dither=Image.Dither.FLOYDSTEINBERG)


def qct_bytes(indexed: Image.Image, transform: GeoTransform, title: str) -> bytes:
    if indexed.mode != "P":
        raise ValueError("QCT writer expects a paletted Pillow image")
    width, height = indexed.size
    tiles_wide = math.ceil(width / TILE)
    tiles_high = math.ceil(height / TILE)
    index_size = 4 * tiles_wide * tiles_high
    result = bytearray(HEADER_SIZE + index_size)
    to_little_u32(result, 0x00, MAGIC)
    to_little_u32(result, 0x04, VERSION)
    to_little_u32(result, 0x08, tiles_wide)
    to_little_u32(result, 0x0C, tiles_high)

    basename = Path(title).stem or "gdal-qct-map"
    text = [
        title, basename, "GDAL-QCT", "1", "1", "generated,qct",
        "Generated by GeoTIFF QuickChart Converter", "", "WGS 84", "", "",
        "Latitude/Longitude",
    ]
    for item, offset in zip(text, range(0x10, 0x40, 4), strict=True):
        to_little_u32(result, offset, append_ascii(result, item))
    to_little_u32(result, 0x40, 0x00000002)  # allows calibration
    to_little_u32(result, 0x44, append_ascii(result, basename))

    extended = len(result)
    result.extend(b"\0" * 32)
    to_little_u32(result, extended, append_ascii(result, "Raster Map"))
    datum_shift = len(result)
    result.extend(b"\0" * 16)
    to_little_u32(result, extended + 4, datum_shift)
    to_little_u32(result, extended + 8, append_ascii(result, "GDAL"))
    to_little_u32(result, 0x54, extended)

    a, b, c, d, e, f = transform.values
    determinant = b * f - c * e
    if abs(determinant) < 1e-30:
        raise ValueError("WGS-84 affine transform is singular")
    # QCT stores inverse eas/nor polynomials, then forward lat/lon polynomials.
    to_little_f64(result, 0x60, (-f * a + c * d) / determinant)
    to_little_f64(result, 0x68, -c / determinant)  # easY (latitude)
    to_little_f64(result, 0x70, f / determinant)   # easX (longitude)
    to_little_f64(result, 0xB0, (e * a - b * d) / determinant)
    to_little_f64(result, 0xB8, b / determinant)   # norY (latitude)
    to_little_f64(result, 0xC0, -e / determinant)  # norX (longitude)
    to_little_f64(result, 0x100, d)  # lat
    to_little_f64(result, 0x108, e)  # latX
    to_little_f64(result, 0x110, f)  # latY
    to_little_f64(result, 0x150, a)  # lon
    to_little_f64(result, 0x158, b)  # lonX
    to_little_f64(result, 0x160, c)  # lonY

    raw_palette = (indexed.getpalette() or []) + [0] * 768
    for index in range(MAX_COLOURS):
        red, green, blue = raw_palette[index * 3 : index * 3 + 3]
        offset = 0x1A0 + index * 4
        result[offset : offset + 4] = bytes((blue, green, red, 0))
    # A valid, neutral interpolation matrix: retain the second colour's index.
    for row in range(128):
        for column in range(128):
            result[0x5A0 + row * 128 + column] = column

    outline = len(result)
    result.extend(b"\0" * (4 * 16))
    padded_width, padded_height = tiles_wide * TILE, tiles_high * TILE
    for index, (x, y) in enumerate(((0, 0), (padded_width, 0),
                                    (padded_width, padded_height), (0, padded_height))):
        longitude, latitude = transform.xy_to_lon_lat(x, y)
        to_little_f64(result, outline + index * 16, latitude)
        to_little_f64(result, outline + index * 16 + 8, longitude)
    to_little_u32(result, 0x58, 4)
    to_little_u32(result, 0x5C, outline)

    pixels = indexed.tobytes()
    for tile_y in range(tiles_high):
        for tile_x in range(tiles_wide):
            tile = bytearray(TILE * TILE)
            for row in range(TILE):
                source_y = tile_y * TILE + row
                if source_y >= height:
                    continue
                source_x = tile_x * TILE
                count = min(TILE, width - source_x)
                start = source_y * width + source_x
                tile[row * TILE : row * TILE + count] = pixels[start : start + count]
            interlaced = bytearray(TILE * TILE)
            for stored_row in range(TILE):
                original_row = reverse_six_bits(stored_row)
                interlaced[stored_row * TILE : (stored_row + 1) * TILE] = \
                    tile[original_row * TILE : (original_row + 1) * TILE]
            encoded = encode_rle(bytes(interlaced))
            pointer_offset = HEADER_SIZE + 4 * (tile_y * tiles_wide + tile_x)
            to_little_u32(result, pointer_offset, len(result))
            result.extend(encoded)
    return bytes(result)


def read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def read_f64(data: bytes, offset: int) -> float:
    return struct.unpack_from("<d", data, offset)[0]


def decode_qct(data: bytes) -> tuple[Image.Image, GeoTransform]:
    if len(data) < HEADER_SIZE or read_u32(data, 0) != MAGIC or read_u32(data, 4) != VERSION:
        raise ValueError("not an unencrypted legacy QCT v2 map")
    tiles_wide, tiles_high = read_u32(data, 8), read_u32(data, 12)
    if not tiles_wide or not tiles_high:
        raise ValueError("invalid QCT tile dimensions")
    total_tiles = tiles_wide * tiles_high
    if HEADER_SIZE + total_tiles * 4 > len(data):
        raise ValueError("truncated QCT image index")
    palette = []
    for index in range(256):
        blue, green, red, _ = data[0x1A0 + index * 4 : 0x1A0 + index * 4 + 4]
        palette.extend((red, green, blue))
    output = Image.new("P", (tiles_wide * TILE, tiles_high * TILE))
    output.putpalette(palette)
    destination = bytearray(output.size[0] * output.size[1])
    for tile_number in range(total_tiles):
        start = read_u32(data, HEADER_SIZE + 4 * tile_number)
        next_start = (read_u32(data, HEADER_SIZE + 4 * (tile_number + 1))
                      if tile_number + 1 < total_tiles else len(data))
        if not HEADER_SIZE + total_tiles * 4 <= start < next_start <= len(data):
            raise ValueError("QCT image index contains invalid tile pointer")
        interlaced = decode_rle(data[start:next_start])
        tile_x, tile_y = tile_number % tiles_wide, tile_number // tiles_wide
        for stored_row in range(TILE):
            destination_row = tile_y * TILE + reverse_six_bits(stored_row)
            destination_offset = destination_row * output.size[0] + tile_x * TILE
            source_offset = stored_row * TILE
            destination[destination_offset : destination_offset + TILE] = \
                interlaced[source_offset : source_offset + TILE]
    output.frombytes(bytes(destination))
    transform = GeoTransform((read_f64(data, 0x150), read_f64(data, 0x158),
                              read_f64(data, 0x160), read_f64(data, 0x100),
                              read_f64(data, 0x108), read_f64(data, 0x110)))
    return output, transform


def validate_qct(qct: Path, expected: Image.Image | None = None,
                 expected_transform: GeoTransform | None = None) -> dict:
    data = qct.read_bytes()
    decoded, transform = decode_qct(data)
    result: dict[str, object] = {
        "qct_sha256": hashlib.sha256(data).hexdigest(),
        "qct_bytes": len(data),
        "decoded_size": list(decoded.size),
        "format": "legacy QCT v2, unencrypted, RLE",
        "pixel_round_trip": None,
        "calibration_max_abs_error_degrees": None,
    }
    if expected is not None:
        crop = decoded.crop((0, 0, expected.width, expected.height))
        result["pixel_round_trip"] = crop.tobytes() == expected.tobytes()
        if not result["pixel_round_trip"]:
            raise ValueError("QCT pixel round-trip validation failed")
    if expected_transform is not None:
        errors = []
        for x, y in ((0, 0), (1, 0), (0, 1), (173.25, 91.5)):
            actual = transform.xy_to_lon_lat(x, y)
            expected_xy = expected_transform.xy_to_lon_lat(x, y)
            errors.extend(abs(left - right) for left, right in zip(actual, expected_xy, strict=True))
        maximum_error = max(errors)
        result["calibration_max_abs_error_degrees"] = maximum_error
        if maximum_error > 1e-12:
            raise ValueError("QCT calibration coefficient validation failed")
    return result


def write_validation_gpx(path: Path, transform: GeoTransform, width: int, height: int,
                         grid: int) -> None:
    """Write named WGS-84 checks against printed grid intersections on the map."""
    if not 2 <= grid <= 9:
        raise ValueError("validation grid must be between 2 and 9")
    namespace = "http://www.topografix.com/GPX/1/1"
    ET.register_namespace("", namespace)
    root = ET.Element(f"{{{namespace}}}gpx", {"version": "1.1", "creator": "GeoTIFF QuickChart Converter"})
    metadata = ET.SubElement(root, f"{{{namespace}}}metadata")
    ET.SubElement(metadata, f"{{{namespace}}}name").text = "QCT georeferencing validation checkpoints"
    ET.SubElement(metadata, f"{{{namespace}}}desc").text = (
        "Load with the converted QCT in Memory-Map. Compare each named coordinate "
        "with the matching printed latitude/longitude or map-grid intersection."
    )
    for row in range(grid):
        y = round((height - 1) * row / (grid - 1))
        for column in range(grid):
            x = round((width - 1) * column / (grid - 1))
            longitude, latitude = transform.xy_to_lon_lat(x, y)
            waypoint = ET.SubElement(root, f"{{{namespace}}}wpt", {
                "lat": f"{latitude:.10f}", "lon": f"{longitude:.10f}",
            })
            label = f"QCT-check-r{row + 1}-c{column + 1}"
            ET.SubElement(waypoint, f"{{{namespace}}}name").text = label
            ET.SubElement(waypoint, f"{{{namespace}}}desc").text = (
                f"Expected map pixel x={x}, y={y}; WGS-84 lat={latitude:.10f}, "
                f"lon={longitude:.10f}. Visually compare with the matching printed grid intersection."
            )
            ET.SubElement(waypoint, f"{{{namespace}}}sym").text = "Waypoint"
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def convert_geotiff(source: Path, destination: Path, *, overwrite: bool = False,
                    keep_work: bool = False, validation_gpx: Path | None = None,
                    validation_grid: int = 3) -> dict:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing output: {destination}")
    if validation_gpx is not None:
        validation_gpx = validation_gpx.resolve()
        if validation_gpx.exists() and not overwrite:
            raise FileExistsError(f"Refusing to overwrite existing GPX: {validation_gpx}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_hash = sha256(source)
    work_context = tempfile.TemporaryDirectory(prefix="geotiff-qct-")
    work = Path(work_context.name)
    try:
        warped = work / "wgs84.vrt"
        png = work / "wgs84.png"
        run([require_program("gdalwarp"), "-of", "VRT", "-t_srs", "EPSG:4326",
             "-r", "bilinear", str(source), str(warped)])
        info = read_info(warped)
        values = info.get("geoTransform")
        if not isinstance(values, list) or len(values) != 6:
            raise ValueError("GDAL did not return an affine transform for the WGS-84 raster")
        transform = GeoTransform(tuple(float(value) for value in values))
        run([require_program("gdal_translate"), "-of", "PNG", "-ot", "Byte",
             str(warped), str(png)])
        with Image.open(png) as opened:
            indexed = quantize(opened)
        staged = destination.with_suffix(destination.suffix + ".tmp")
        staged.write_bytes(qct_bytes(indexed, transform, source.name))
        validation = validate_qct(staged, indexed, transform)
        staged.replace(destination)
        if validation_gpx is not None:
            validation_gpx.parent.mkdir(parents=True, exist_ok=True)
            write_validation_gpx(validation_gpx, transform, indexed.width, indexed.height,
                                 validation_grid)
        validation.update({
            "source": str(source),
            "source_sha256_before": source_hash,
            "source_sha256_after": sha256(source),
            "source_preserved": source_hash == sha256(source),
            "wgs84_geotransform": list(transform.values),
            "output": str(destination),
            "validation_gpx": str(validation_gpx) if validation_gpx else None,
        })
        if not validation["source_preserved"]:
            raise RuntimeError("source hash changed unexpectedly")
        log = destination.with_suffix(destination.suffix + ".validation.json")
        log.write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
        return validation
    finally:
        if keep_work:
            print(f"Retained intermediate work folder: {work}", file=sys.stderr)
            work_context.cleanup = lambda: None  # type: ignore[method-assign]
        else:
            work_context.cleanup()


class QuickChartTests(unittest.TestCase):
    def test_rle_round_trip_for_all_palette_widths(self) -> None:
        for colours in (1, 2, 3, 4, 17, 64, 127):
            source = bytes(index % colours for index in range(TILE * TILE))
            self.assertEqual(decode_rle(encode_rle(source)), source)

    def test_qct_binary_layout_pixels_and_calibration(self) -> None:
        image = Image.new("RGB", (73, 69))
        image.putdata([((x * 19) % 256, (y * 23) % 256, ((x + y) * 11) % 256)
                       for y in range(image.height) for x in range(image.width)])
        indexed = quantize(image)
        transform = GeoTransform((145.0, 0.0001, 0.0, -37.0, 0.0, -0.0001))
        with tempfile.TemporaryDirectory() as folder:
            qct = Path(folder) / "fixture.qct"
            qct.write_bytes(qct_bytes(indexed, transform, "fixture.tif"))
            report = validate_qct(qct, indexed, transform)
        self.assertTrue(report["pixel_round_trip"])
        self.assertLess(report["calibration_max_abs_error_degrees"], 1e-12)

    def test_validation_gpx_has_named_grid_points(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            gpx = Path(folder) / "validation.gpx"
            write_validation_gpx(gpx, GeoTransform((145, 0.01, 0, -37, 0, -0.01)),
                                 100, 80, 3)
            root = ET.parse(gpx).getroot()
        waypoints = root.findall("{http://www.topografix.com/GPX/1/1}wpt")
        self.assertEqual(len(waypoints), 9)
        self.assertEqual(waypoints[0].findtext("{http://www.topografix.com/GPX/1/1}name"),
                         "QCT-check-r1-c1")

    def test_gdal_geotiff_end_to_end_when_available(self) -> None:
        if not all(shutil.which(tool) for tool in ("gdal_translate", "gdalwarp", "gdalinfo")):
            self.skipTest("GDAL command-line programs are unavailable")
        with tempfile.TemporaryDirectory() as folder:
            folder_path = Path(folder)
            ppm = folder_path / "source.ppm"
            tif = folder_path / "source.tif"
            qct = folder_path / "result.qct"
            Image.new("RGB", (20, 18), (50, 100, 150)).save(ppm)
            run([require_program("gdal_translate"), "-of", "GTiff", "-a_srs", "EPSG:4326",
                 "-a_ullr", "145", "-37", "145.02", "-37.018", str(ppm), str(tif)])
            before = sha256(tif)
            report = convert_geotiff(tif, qct)
            self.assertTrue(report["source_preserved"])
            self.assertEqual(before, sha256(tif))
            self.assertTrue(qct.is_file())


def main(arguments: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, help="input GeoTIFF")
    parser.add_argument("destination", nargs="?", type=Path, help="new QCT output")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing output QCT")
    parser.add_argument("--keep-work", action="store_true", help="retain temporary WGS-84 render files")
    parser.add_argument("--validation-gpx", nargs="?", const=Path("AUTO"), type=Path,
                        help="write named GPX georeferencing checkpoints; omit its path to use a sibling file")
    parser.add_argument("--validation-grid", type=int, default=3,
                        help="checkpoints per side for --validation-gpx (2 to 9; default: 3)")
    parser.add_argument("--test", action="store_true", help="run automated tests")
    args = parser.parse_args(arguments)
    if args.test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(QuickChartTests)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if args.source is None or args.destination is None:
        parser.error("source and destination are required unless --test is used")
    gpx_path = args.validation_gpx
    if gpx_path == Path("AUTO"):
        gpx_path = args.destination.with_suffix(".validation.gpx")
    report = convert_geotiff(args.source, args.destination, overwrite=args.overwrite,
                             keep_work=args.keep_work, validation_gpx=gpx_path,
                             validation_grid=args.validation_grid)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
