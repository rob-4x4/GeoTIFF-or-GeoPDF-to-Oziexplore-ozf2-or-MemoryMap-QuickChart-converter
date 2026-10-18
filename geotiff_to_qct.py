#!/usr/bin/env python3
"""Convert a GeoTIFF or GeoPDF into an unencrypted legacy Memory-Map Quick Chart (.qct).

This is intentionally a standalone tool.  It calls GDAL to read source
georeferencing and render a PNG while retaining the source raster grid; QCT
writing and validation are implemented here.  It does not read, decrypt,
modify, or overwrite existing QCT/QC3 maps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
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
# Legacy QCT has 128 usable palette entries (indices 0 through 127).  A tile
# containing all 128 must use pixel packing, rather than run-length coding.
MAX_COLOURS = 128
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


POLY_TERMS = ((0, 0), (1, 0), (0, 1), (2, 0), (1, 1), (0, 2),
              (3, 0), (2, 1), (1, 2), (0, 3))
INVERSE_HEADER_ORDER = (0, 2, 1, 5, 4, 3, 9, 8, 7, 6)


def evaluate_polynomial(coefficients: tuple[float, ...], x: float, y: float) -> float:
    return sum(coefficient * x ** power_x * y ** power_y
               for coefficient, (power_x, power_y) in zip(coefficients, POLY_TERMS, strict=True))


@dataclass(frozen=True)
class QctCalibration:
    """Forward and reverse QCT third-order WGS-84 calibration polynomials."""

    longitude: tuple[float, ...]
    latitude: tuple[float, ...]
    east: tuple[float, ...]
    north: tuple[float, ...]

    @classmethod
    def from_affine(cls, transform: GeoTransform) -> "QctCalibration":
        a, b, c, d, e, f = transform.values
        determinant = b * f - c * e
        if abs(determinant) < 1e-30:
            raise ValueError("WGS-84 affine transform is singular")
        zero = (0.0,) * 7
        return cls(
            (a, b, c, *zero), (d, e, f, *zero),
            ((-f * a + c * d) / determinant, f / determinant, -c / determinant, *zero),
            ((e * a - b * d) / determinant, -e / determinant, b / determinant, *zero),
        )

    def xy_to_lon_lat(self, x: float, y: float) -> tuple[float, float]:
        return evaluate_polynomial(self.longitude, x, y), evaluate_polynomial(self.latitude, x, y)

    def lon_lat_to_xy(self, longitude: float, latitude: float) -> tuple[float, float]:
        return (evaluate_polynomial(self.east, longitude, latitude),
                evaluate_polynomial(self.north, longitude, latitude))


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


def transform_points(source_srs: str, target_srs: str,
                     points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Transform a batch of XY points through GDAL without raster resampling."""
    command = [require_program("gdaltransform"), "-s_srs", source_srs,
               "-t_srs", target_srs]
    input_text = "".join(f"{x:.15g} {y:.15g}\n" for x, y in points)
    completed = subprocess.run(command, input=input_text, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "gdaltransform failed")
    transformed = []
    for line in completed.stdout.splitlines():
        values = line.split()
        if len(values) < 2:
            raise ValueError(f"unexpected gdaltransform output: {line!r}")
        transformed.append((float(values[0]), float(values[1])))
    if len(transformed) != len(points):
        raise ValueError("gdaltransform did not return one result per calibration point")
    return transformed


def solve_linear_system(matrix: list[list[float]], values: list[float]) -> list[float]:
    """Solve a small well-conditioned square system by pivoted elimination."""
    size = len(values)
    augmented = [row[:] + [value] for row, value in zip(matrix, values, strict=True)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-20:
            raise ValueError("singular calibration fit")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        augmented[column] = [value / scale for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            scale = augmented[row][column]
            if scale:
                augmented[row] = [left - scale * right
                                  for left, right in zip(augmented[row], augmented[column], strict=True)]
    return [row[-1] for row in augmented]


def fit_polynomial(inputs: list[tuple[float, float]], outputs: list[float]) -> tuple[float, ...]:
    """Fit an order-three polynomial and expand it into QCT's raw coordinates."""
    if len(inputs) != len(outputs) or len(inputs) < len(POLY_TERMS):
        raise ValueError("insufficient calibration points")
    xs, ys = zip(*inputs, strict=True)
    centre_x, centre_y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    scale_x, scale_y = (max(xs) - min(xs)) / 2, (max(ys) - min(ys)) / 2
    if not scale_x or not scale_y:
        raise ValueError("degenerate calibration extent")
    normal = [[0.0] * len(POLY_TERMS) for _ in POLY_TERMS]
    right = [0.0] * len(POLY_TERMS)
    for (x, y), output in zip(inputs, outputs, strict=True):
        u, v = (x - centre_x) / scale_x, (y - centre_y) / scale_y
        row = [u ** power_x * v ** power_y for power_x, power_y in POLY_TERMS]
        for left in range(len(POLY_TERMS)):
            right[left] += row[left] * output
            for column in range(len(POLY_TERMS)):
                normal[left][column] += row[left] * row[column]
    normalised = solve_linear_system(normal, right)
    expanded = {(power_x, power_y): 0.0 for power_x, power_y in POLY_TERMS}
    for coefficient, (power_u, power_v) in zip(normalised, POLY_TERMS, strict=True):
        for power_x in range(power_u + 1):
            factor_x = (math.comb(power_u, power_x) * (-centre_x) ** (power_u - power_x)
                        / scale_x ** power_u)
            for power_y in range(power_v + 1):
                factor_y = (math.comb(power_v, power_y) * (-centre_y) ** (power_v - power_y)
                            / scale_y ** power_v)
                expanded[(power_x, power_y)] += coefficient * factor_x * factor_y
    return tuple(expanded[term] for term in POLY_TERMS)


def native_qct_calibration(source: Path, info: dict) -> tuple[QctCalibration, float, float]:
    """Fit QCT polynomials to the native source raster, retaining every source pixel."""
    size = info.get("size")
    geotransform = info.get("geoTransform")
    wkt = (info.get("coordinateSystem") or {}).get("wkt")
    if (not isinstance(size, list) or len(size) != 2 or not isinstance(geotransform, list)
            or len(geotransform) != 6 or not isinstance(wkt, str)):
        raise ValueError("source needs an affine geotransform and a defined coordinate system")
    width, height = (int(size[0]), int(size[1]))
    a, b, c, d, e, f = (float(value) for value in geotransform)
    samples = [(width * column / 6, height * row / 6)
               for row in range(7) for column in range(7)]
    source_coordinates = [(a + b * x + c * y, d + e * x + f * y) for x, y in samples]
    lon_lat = transform_points(wkt, "EPSG:4326", source_coordinates)
    longitude = fit_polynomial(samples, [point[0] for point in lon_lat])
    latitude = fit_polynomial(samples, [point[1] for point in lon_lat])
    east = fit_polynomial(lon_lat, [point[0] for point in samples])
    north = fit_polynomial(lon_lat, [point[1] for point in samples])
    calibration = QctCalibration(longitude, latitude, east, north)
    forward_error = max(abs(actual - expected)
                        for pixel, expected_point in zip(samples, lon_lat, strict=True)
                        for actual, expected in zip(calibration.xy_to_lon_lat(*pixel), expected_point,
                                                    strict=True))
    inverse_error = max(abs(actual - expected)
                        for lon_lat_point, expected_point in zip(lon_lat, samples, strict=True)
                        for actual, expected in zip(calibration.lon_lat_to_xy(*lon_lat_point), expected_point,
                                                    strict=True))
    return calibration, forward_error, inverse_error


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
        raise ValueError("QCT supports 1 to 128 palette entries")
    return max(0, math.ceil(math.log2(colour_count)))


def subpalette_for(indices: bytes) -> tuple[list[int], dict[int, int]]:
    """Return the tile-local palette in first-use order and its reverse lookup."""
    subpalette: list[int] = []
    lookup: dict[int, int] = {}
    for colour in indices:
        if colour >= MAX_COLOURS:
            raise ValueError("QCT pixel references a palette entry outside 0..127")
        if colour not in lookup:
            lookup[colour] = len(subpalette)
            subpalette.append(colour)
    return subpalette, lookup


def encode_rle(indices: bytes) -> bytes:
    """Encode exactly one interlaced 64x64 tile using legacy QCT RLE."""
    subpalette, lookup = subpalette_for(indices)
    if len(subpalette) > 127:
        raise ValueError("QCT RLE supports at most 127 palette entries per tile")
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


def encode_pixel_packed(indices: bytes) -> bytes:
    """Encode a tile with QCT's 32-bit-block pixel-packing representation."""
    subpalette, lookup = subpalette_for(indices)
    if not 2 <= len(subpalette) <= MAX_COLOURS:
        raise ValueError("QCT pixel packing supports 2 to 128 palette entries per tile")
    bits = palette_bits(len(subpalette))
    pixels_per_word = 32 // bits
    result = bytearray([256 - len(subpalette), *subpalette])
    for start in range(0, len(indices), pixels_per_word):
        word = 0
        for offset, colour in enumerate(indices[start : start + pixels_per_word]):
            word |= lookup[colour] << (offset * bits)
        result.extend(struct.pack("<I", word))
    return bytes(result)


def decode_rle(encoded: bytes) -> bytes:
    if not encoded or encoded[0] == 0 or encoded[0] > 127:
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


def decode_pixel_packed(encoded: bytes) -> bytes:
    if not encoded or not 128 <= encoded[0] <= 254:
        raise ValueError("not a legacy QCT pixel-packed tile")
    palette_count = 256 - encoded[0]
    if len(encoded) < palette_count + 1:
        raise ValueError("truncated QCT pixel-packed sub-palette")
    subpalette = encoded[1 : 1 + palette_count]
    bits = palette_bits(palette_count)
    pixels_per_word = 32 // bits
    packed = encoded[1 + palette_count :]
    words = math.ceil(TILE * TILE / pixels_per_word)
    if len(packed) != words * 4:
        raise ValueError("invalid QCT pixel-packed tile size")
    mask = (1 << bits) - 1
    decoded = bytearray()
    for start in range(0, len(packed), 4):
        word = struct.unpack_from("<I", packed, start)[0]
        for offset in range(pixels_per_word):
            subpalette_index = (word >> (offset * bits)) & mask
            if subpalette_index >= palette_count:
                raise ValueError("invalid QCT pixel-packed sub-palette index")
            decoded.append(subpalette[subpalette_index])
            if len(decoded) == TILE * TILE:
                return bytes(decoded)
    raise ValueError("QCT pixel-packed tile does not expand to 4096 pixels")


def encode_tile(indices: bytes) -> bytes:
    """Use the smaller documented non-Huffman encoding for one QCT tile."""
    subpalette, _ = subpalette_for(indices)
    candidates = [encode_pixel_packed(indices)] if len(subpalette) >= 2 else []
    if len(subpalette) <= 127:
        candidates.append(encode_rle(indices))
    return min(candidates, key=len)


def decode_tile(encoded: bytes) -> bytes:
    if not encoded:
        raise ValueError("empty QCT tile")
    if 1 <= encoded[0] <= 127:
        return decode_rle(encoded)
    if 128 <= encoded[0] <= 254:
        return decode_pixel_packed(encoded)
    raise ValueError("Huffman-compressed QCT tiles are not supported by this validator")


def quantize(image: Image.Image) -> Image.Image:
    """Make the 128-colour QCT palette without spatial colour dithering.

    Dithering is desirable for photographs but creates false coloured fringes around
    cartographic lines and text.  QCT maps are normally viewed at close range, so
    preserving solid map-feature colours is more important than simulated shades.
    MAXCOVERAGE also preserves a small existing cartographic palette faithfully;
    MEDIANCUT can substitute a source palette colour even when fewer than 128
    colours are present.
    """
    rgb = image.convert("RGB")
    return rgb.quantize(colors=MAX_COLOURS, method=Image.Quantize.MAXCOVERAGE,
                        dither=Image.Dither.NONE)


def interpolation_matrix(indexed: Image.Image) -> bytes:
    """Pre-calculate the documented 128 x 128 QCT palette blend matrix."""
    if indexed.mode != "P":
        raise ValueError("QCT interpolation requires a paletted image")
    raw_palette = (indexed.getpalette() or []) + [0] * 768
    palette = [tuple(raw_palette[index * 3 : index * 3 + 3])
               for index in range(MAX_COLOURS)]
    used = max(indexed.tobytes(), default=0) + 1
    matrix = bytearray(MAX_COLOURS * MAX_COLOURS)
    for row in range(MAX_COLOURS):
        for column in range(MAX_COLOURS):
            if row >= used or column >= used:
                choice = 0
            elif row == column:
                choice = row
            else:
                left, right = palette[row], palette[column]
                target = tuple(left[channel] + right[channel] for channel in range(3))
                choice = min(
                    range(used),
                    key=lambda index: sum(
                        (2 * palette[index][channel] - target[channel]) ** 2
                        for channel in range(3)),
                )
            matrix[row * MAX_COLOURS + column] = choice
    return bytes(matrix)


def write_coefficients(buffer: bytearray, base: int, coefficients: tuple[float, ...],
                       *, inverse: bool = False) -> None:
    order = INVERSE_HEADER_ORDER if inverse else range(len(POLY_TERMS))
    for offset, coefficient_index in enumerate(order):
        to_little_f64(buffer, base + 8 * offset, coefficients[coefficient_index])


def read_coefficients(data: bytes, base: int, *, inverse: bool = False) -> tuple[float, ...]:
    values = [0.0] * len(POLY_TERMS)
    order = INVERSE_HEADER_ORDER if inverse else range(len(POLY_TERMS))
    for offset, coefficient_index in enumerate(order):
        values[coefficient_index] = read_f64(data, base + 8 * offset)
    return tuple(values)


def qct_bytes(indexed: Image.Image, transform: GeoTransform | QctCalibration, title: str) -> bytes:
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

    calibration = (QctCalibration.from_affine(transform)
                   if isinstance(transform, GeoTransform) else transform)
    # QCT stores inverse eas/nor polynomials, then forward lat/lon polynomials.
    write_coefficients(result, 0x60, calibration.east, inverse=True)
    write_coefficients(result, 0xB0, calibration.north, inverse=True)
    write_coefficients(result, 0x100, calibration.latitude)
    write_coefficients(result, 0x150, calibration.longitude)

    raw_palette = (indexed.getpalette() or []) + [0] * 768
    for index in range(MAX_COLOURS):
        red, green, blue = raw_palette[index * 3 : index * 3 + 3]
        offset = 0x1A0 + index * 4
        result[offset : offset + 4] = bytes((blue, green, red, 0))
    result[0x5A0 : 0x5A0 + MAX_COLOURS * MAX_COLOURS] = interpolation_matrix(indexed)

    outline = len(result)
    result.extend(b"\0" * (4 * 16))
    padded_width, padded_height = tiles_wide * TILE, tiles_high * TILE
    for index, (x, y) in enumerate(((0, 0), (padded_width, 0),
                                    (padded_width, padded_height), (0, padded_height))):
        longitude, latitude = calibration.xy_to_lon_lat(x, y)
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
            encoded = encode_tile(bytes(interlaced))
            pointer_offset = HEADER_SIZE + 4 * (tile_y * tiles_wide + tile_x)
            to_little_u32(result, pointer_offset, len(result))
            result.extend(encoded)
    return bytes(result)


def read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def read_f64(data: bytes, offset: int) -> float:
    return struct.unpack_from("<d", data, offset)[0]


def decode_qct(data: bytes) -> tuple[Image.Image, QctCalibration]:
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
        interlaced = decode_tile(data[start:next_start])
        tile_x, tile_y = tile_number % tiles_wide, tile_number // tiles_wide
        for stored_row in range(TILE):
            destination_row = tile_y * TILE + reverse_six_bits(stored_row)
            destination_offset = destination_row * output.size[0] + tile_x * TILE
            source_offset = stored_row * TILE
            destination[destination_offset : destination_offset + TILE] = \
                interlaced[source_offset : source_offset + TILE]
    output.frombytes(bytes(destination))
    calibration = QctCalibration(read_coefficients(data, 0x150), read_coefficients(data, 0x100),
                                 read_coefficients(data, 0x60, inverse=True),
                                 read_coefficients(data, 0xB0, inverse=True))
    return output, calibration


def validate_qct(qct: Path, expected: Image.Image | None = None,
                 expected_transform: GeoTransform | QctCalibration | None = None) -> dict:
    data = qct.read_bytes()
    decoded, transform = decode_qct(data)
    result: dict[str, object] = {
        "qct_sha256": hashlib.sha256(data).hexdigest(),
        "qct_bytes": len(data),
        "decoded_size": list(decoded.size),
        "format": "legacy QCT v2, unencrypted, RLE/pixel-packed tiles",
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


def write_validation_gpx(path: Path, transform: GeoTransform | QctCalibration, width: int, height: int,
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


def projected_grid_pixels(transform: GeoTransform, width: int, height: int,
                          spacing_metres: int) -> list[tuple[int, int, float, float]]:
    """Return source-pixel positions for whole projected-coordinate grid intersections."""
    if spacing_metres <= 0:
        raise ValueError("projected grid spacing must be positive")
    a, b, c, d, e, f = transform.values
    determinant = b * f - c * e
    if abs(determinant) < 1e-30:
        raise ValueError("source affine transform is singular")
    corners = [(a + b * x + c * y, d + e * x + f * y)
               for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))]
    eastings = [point[0] for point in corners]
    northings = [point[1] for point in corners]
    first_easting = math.ceil(min(eastings) / spacing_metres) * spacing_metres
    last_easting = math.floor(max(eastings) / spacing_metres) * spacing_metres
    first_northing = math.ceil(min(northings) / spacing_metres) * spacing_metres
    last_northing = math.floor(max(northings) / spacing_metres) * spacing_metres
    points: list[tuple[int, int, float, float]] = []
    for easting in range(first_easting, last_easting + 1, spacing_metres):
        for northing in range(first_northing, last_northing + 1, spacing_metres):
            delta_easting, delta_northing = easting - a, northing - d
            x = (f * delta_easting - c * delta_northing) / determinant
            y = (-e * delta_easting + b * delta_northing) / determinant
            if 0 <= x < width and 0 <= y < height:
                points.append((easting, northing, x, y))
    return points


def write_projected_grid_gpx(path: Path, source_wkt: str, source_transform: GeoTransform,
                             width: int, height: int, spacing_metres: int) -> int:
    """Write GPX waypoints at known projected-map grid intersections."""
    points = projected_grid_pixels(source_transform, width, height, spacing_metres)
    if not points:
        raise ValueError("no projected grid intersections fall within the source raster")
    namespace = "http://www.topografix.com/GPX/1/1"
    ET.register_namespace("", namespace)
    root = ET.Element(f"{{{namespace}}}gpx", {"version": "1.1", "creator": "GeoTIFF QuickChart Converter"})
    metadata = ET.SubElement(root, f"{{{namespace}}}metadata")
    ET.SubElement(metadata, f"{{{namespace}}}name").text = "QCT projected-grid validation checkpoints"
    ET.SubElement(metadata, f"{{{namespace}}}desc").text = (
        "Waypoints are placed on exact source projected-coordinate grid intersections. "
        "Load with the converted QCT and confirm each waypoint lies on the matching printed grid crossing."
    )
    longitude_latitude = transform_points(source_wkt, "EPSG:4326",
                                          [(easting, northing) for easting, northing, _, _ in points])
    for (easting, northing, x, y), (longitude, latitude) in zip(points, longitude_latitude, strict=True):
        waypoint = ET.SubElement(root, f"{{{namespace}}}wpt", {
            "lat": f"{latitude:.10f}", "lon": f"{longitude:.10f}",
        })
        label = f"QCT-grid-E{easting}-N{northing}"
        ET.SubElement(waypoint, f"{{{namespace}}}name").text = label
        ET.SubElement(waypoint, f"{{{namespace}}}desc").text = (
            f"Source projected grid E={easting} m, N={northing} m; source pixel x={x:.3f}, y={y:.3f}. "
            "It should be exactly on this printed map-grid intersection."
        )
        ET.SubElement(waypoint, f"{{{namespace}}}sym").text = "Waypoint"
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return len(points)


def write_projected_grid_validation(source: Path, path: Path, spacing_metres: int) -> int:
    """Create projected-grid checkpoints for a GDAL dataset with metre-based coordinates."""
    info = read_info(source)
    source_wkt = (info.get("coordinateSystem") or {}).get("wkt")
    if not isinstance(source_wkt, str):
        raise ValueError("source needs a defined coordinate system for projected-grid validation")
    transform = GeoTransform(tuple(float(value) for value in info["geoTransform"]))
    width, height = (int(value) for value in info["size"])
    calibration, _, _ = native_qct_calibration(source, info)
    return write_projected_grid_gpx(path, source_wkt, transform, width, height, spacing_metres)


def pdf_page_datasets(source: Path) -> list[str]:
    """Return GDAL dataset names for PDF pages, preserving GDAL's own naming."""
    info = read_info(source)
    subdatasets = (info.get("metadata") or {}).get("SUBDATASETS") or {}
    names = [(key, value) for key, value in subdatasets.items()
             if key.endswith("_NAME") and isinstance(value, str)]
    if not names:
        return [str(source)]
    def page_key(item: tuple[str, str]) -> int:
        match = re.search(r"SUBDATASET_(\d+)_NAME", item[0])
        return int(match.group(1)) if match else 0
    return [value for _, value in sorted(names, key=page_key)]


def convert_geopdf(source: Path, destination: Path, *, dpi: int = 300, page: int = 1,
                   overwrite: bool = False, keep_work: bool = False,
                   validation_gpx: Path | None = None, validation_grid: int = 3,
                   projected_grid_gpx: Path | None = None,
                   projected_grid_spacing: int = 50000) -> dict:
    """Render one georeferenced PDF page to a temporary GeoTIFF, then create a QCT."""
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.lower() != ".pdf":
        raise ValueError("GeoPDF conversion expects a .pdf input")
    if dpi < 72 or dpi > 1200:
        raise ValueError("GeoPDF DPI must be between 72 and 1200")
    pages = pdf_page_datasets(source)
    if not 1 <= page <= len(pages):
        raise ValueError(f"GeoPDF page must be between 1 and {len(pages)}")
    if projected_grid_gpx is not None and projected_grid_gpx.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing GPX: {projected_grid_gpx}")
    pdf_hash = sha256(source)
    work_context = tempfile.TemporaryDirectory(prefix="geopdf-qct-")
    work = Path(work_context.name)
    try:
        staged = work / "rendered-page.tif"
        # Rasterise at an explicit DPI and retain GDAL's georeferencing in the staged TIFF.
        run([require_program("gdal_translate"), "-oo", f"DPI={dpi}", "-of", "GTiff",
             "-co", "TILED=YES", "-co", "COMPRESS=DEFLATE", pages[page - 1], str(staged)])
        report = convert_geotiff(staged, destination, overwrite=overwrite, keep_work=keep_work,
                                 validation_gpx=validation_gpx, validation_grid=validation_grid,
                                 title=source.name)
        staged_hash = sha256(staged)
        projected_count: int | None = None
        if projected_grid_gpx is not None:
            projected_grid_gpx.parent.mkdir(parents=True, exist_ok=True)
            projected_count = write_projected_grid_validation(staged, projected_grid_gpx,
                                                               projected_grid_spacing)
        report["source"] = str(source)
        report["source_sha256_before"] = pdf_hash
        report["source_sha256_after"] = sha256(source)
        report["source_preserved"] = pdf_hash == sha256(source)
        report["geopdf"] = {
            "page": page,
            "page_count": len(pages),
            "dpi": dpi,
            "staged_geotiff_sha256": staged_hash,
            "staged_geotiff_pixel_dimensions": report["source_pixel_dimensions"],
        }
        report["projected_grid_gpx"] = str(projected_grid_gpx) if projected_grid_gpx else None
        report["projected_grid_checkpoints"] = projected_count
        destination.with_suffix(destination.suffix + ".validation.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report
    finally:
        if keep_work:
            print(f"Retained GeoPDF staging directory: {work}")
            work_context.cleanup = lambda: None  # type: ignore[method-assign]
        else:
            work_context.cleanup()


def convert_geotiff(source: Path, destination: Path, *, overwrite: bool = False,
                    keep_work: bool = False, validation_gpx: Path | None = None,
                    validation_grid: int = 3, title: str | None = None) -> dict:
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
    source_info = read_info(source)
    calibration, calibration_fit_error, inverse_calibration_fit_error = native_qct_calibration(source, source_info)
    work_context = tempfile.TemporaryDirectory(prefix="geotiff-qct-")
    work = Path(work_context.name)
    try:
        png = work / "source.png"
        # Keep the original raster grid.  QCT's polynomials carry WGS-84
        # calibration, so GDAL need not resample text, contours, or linework.
        run([require_program("gdal_translate"), "-of", "PNG", "-ot", "Byte",
             str(source), str(png)])
        with Image.open(png) as opened:
            indexed = quantize(opened)
        staged = destination.with_suffix(destination.suffix + ".tmp")
        staged.write_bytes(qct_bytes(indexed, calibration, title or source.name))
        validation = validate_qct(staged, indexed, calibration)
        staged.replace(destination)
        if validation_gpx is not None:
            validation_gpx.parent.mkdir(parents=True, exist_ok=True)
            write_validation_gpx(validation_gpx, calibration, indexed.width, indexed.height,
                                 validation_grid)
        validation.update({
            "source": str(source),
            "source_sha256_before": source_hash,
            "source_sha256_after": sha256(source),
            "source_preserved": source_hash == sha256(source),
            "source_pixel_dimensions": source_info.get("size"),
            "qct_pixel_dimensions": list(indexed.size),
            "qct_palette_entries": max(indexed.tobytes(), default=0) + 1,
            "raster_reprojection": "none; native source pixels retained",
            "calibration_fit_max_abs_error_degrees": calibration_fit_error,
            "inverse_calibration_fit_max_abs_error_pixels": inverse_calibration_fit_error,
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

    def test_pixel_packing_round_trip_including_128_colours(self) -> None:
        for colours in (2, 3, 17, 64, 127, 128):
            source = bytes(index % colours for index in range(TILE * TILE))
            encoded = encode_pixel_packed(source)
            self.assertGreater(encoded[0], 127)
            self.assertEqual(decode_pixel_packed(encoded), source)
            self.assertEqual(decode_tile(encode_tile(source)), source)

    def test_cartographic_quantization_has_no_dither_pattern(self) -> None:
        # Every row describes the same grayscale edge.  Error diffusion would make
        # the palette index pattern vary by row; map linework must remain solid.
        source = Image.new("RGB", (256, 4))
        source.putdata([(value, value, value)
                        for _ in range(source.height) for value in range(source.width)])
        indexed = quantize(source)
        pixels = indexed.tobytes()
        first_row = pixels[:source.width]
        for row in range(1, source.height):
            self.assertEqual(pixels[row * source.width : (row + 1) * source.width], first_row)
        self.assertLessEqual(max(pixels) + 1, MAX_COLOURS)

    def test_cartographic_palette_colours_are_preserved(self) -> None:
        # A paletted USGS-style map with fewer than 128 colours must not have
        # its white paper colour (or any other source colour) substituted.
        colours = [(0, 0, 0), (255, 255, 255), (0, 151, 164), (203, 0, 23),
                   (131, 66, 37), (201, 234, 157), (137, 51, 128),
                   (167, 226, 226), (255, 184, 184), (218, 179, 214),
                   (207, 164, 142)]
        source = Image.new("RGB", (len(colours), 1))
        source.putdata(colours)
        indexed = quantize(source)
        rendered = indexed.convert("RGB")
        self.assertEqual([rendered.getpixel((x, 0)) for x in range(rendered.width)], colours)

    def test_interpolation_matrix_is_symmetric_and_preserves_identity(self) -> None:
        image = Image.new("RGB", (4, 1))
        image.putdata([(0, 0, 0), (64, 64, 64), (160, 160, 160), (255, 255, 255)])
        indexed = quantize(image)
        matrix = interpolation_matrix(indexed)
        used = max(indexed.tobytes()) + 1
        for index in range(used):
            self.assertEqual(matrix[index * MAX_COLOURS + index], index)
        for row in range(used):
            for column in range(used):
                self.assertEqual(matrix[row * MAX_COLOURS + column],
                                 matrix[column * MAX_COLOURS + row])

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

    def test_projected_grid_points_are_inside_source(self) -> None:
        transform = GeoTransform((1000, 10, 0, 5000, 0, -10))
        points = projected_grid_pixels(transform, 100, 80, 250)
        self.assertEqual(len(points), 16)
        self.assertIn((1500, 4500, 50.0, 50.0), points)
        self.assertTrue(all(0 <= x < 100 and 0 <= y < 80 for _, _, x, y in points))

    def test_gdal_geotiff_end_to_end_when_available(self) -> None:
        if not all(shutil.which(tool) for tool in ("gdal_translate", "gdaltransform", "gdalinfo")):
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
            self.assertEqual(report["source_pixel_dimensions"], report["qct_pixel_dimensions"])
            self.assertEqual(report["raster_reprojection"], "none; native source pixels retained")
            self.assertLess(float(report["calibration_fit_max_abs_error_degrees"]), 1e-8)
            self.assertLess(float(report["inverse_calibration_fit_max_abs_error_pixels"]), 0.01)
            self.assertTrue(qct.is_file())

    def test_geopdf_end_to_end_when_available(self) -> None:
        if not all(shutil.which(tool) for tool in ("gdal_translate", "gdaltransform", "gdalinfo")):
            self.skipTest("GDAL command-line programs are unavailable")
        formats = subprocess.run([require_program("gdalinfo"), "--formats"], text=True,
                                 capture_output=True, check=True).stdout
        if not re.search(r"^\s*PDF\b", formats, re.MULTILINE):
            self.skipTest("GDAL PDF driver is unavailable")
        with tempfile.TemporaryDirectory() as folder:
            folder_path = Path(folder)
            ppm = folder_path / "source.ppm"
            tif = folder_path / "source.tif"
            pdf = folder_path / "source.pdf"
            qct = folder_path / "result.qct"
            grid = folder_path / "grid.gpx"
            Image.new("RGB", (20, 18), (50, 100, 150)).save(ppm)
            run([require_program("gdal_translate"), "-of", "GTiff", "-a_srs", "EPSG:32617",
                 "-a_ullr", "500000", "4500000", "520000", "4482000", str(ppm), str(tif)])
            run([require_program("gdal_translate"), "-of", "PDF", str(tif), str(pdf)])
            before = sha256(pdf)
            report = convert_geopdf(pdf, qct, dpi=72, projected_grid_gpx=grid,
                                    projected_grid_spacing=10000)
            self.assertTrue(report["source_preserved"])
            self.assertEqual(before, sha256(pdf))
            self.assertEqual(report["geopdf"]["dpi"], 72)
            self.assertGreater(int(report["projected_grid_checkpoints"]), 0)
            self.assertTrue(grid.is_file())
            self.assertTrue(qct.is_file())


def main(arguments: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, help="input GeoTIFF or GeoPDF")
    parser.add_argument("destination", nargs="?", type=Path, help="new QCT output")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing output QCT")
    parser.add_argument("--keep-work", action="store_true", help="retain temporary source render files")
    parser.add_argument("--validation-gpx", nargs="?", const=Path("AUTO"), type=Path,
                        help="write named GPX georeferencing checkpoints; omit its path to use a sibling file")
    parser.add_argument("--validation-grid", type=int, default=3,
                        help="checkpoints per side for --validation-gpx (2 to 9; default: 3)")
    parser.add_argument("--source-grid-gpx", type=Path,
                        help="write read-only checkpoints at source projected-grid intersections")
    parser.add_argument("--projected-grid-metres", type=int, default=50000,
                        help="metre spacing for --source-grid-gpx (default: 50000)")
    parser.add_argument("--projected-grid-gpx", type=Path,
                        help="write projected-grid checkpoints while converting")
    parser.add_argument("--pdf-dpi", type=int, default=300,
                        help="GeoPDF rasterisation DPI (72 to 1200; default: 300)")
    parser.add_argument("--pdf-page", type=int, default=1,
                        help="one-based GeoPDF page number (default: 1)")
    parser.add_argument("--test", action="store_true", help="run automated tests")
    args = parser.parse_args(arguments)
    if args.test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(QuickChartTests)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if args.source is None:
        parser.error("source is required unless --test is used")
    if args.source_grid_gpx is not None and args.destination is None:
        source = args.source.resolve()
        if source.suffix.lower() == ".pdf":
            parser.error("GeoPDF grid validation requires a conversion destination; use --projected-grid-gpx")
        target = args.source_grid_gpx.resolve()
        if target.exists() and not args.overwrite:
            raise FileExistsError(f"Refusing to overwrite existing GPX: {target}")
        info = read_info(source)
        _calibration, forward_error, inverse_error = native_qct_calibration(source, info)
        transform = GeoTransform(tuple(float(value) for value in info["geoTransform"]))
        width, height = (int(value) for value in info["size"])
        source_wkt = (info.get("coordinateSystem") or {}).get("wkt")
        if not isinstance(source_wkt, str):
            raise ValueError("source needs a defined coordinate system")
        target.parent.mkdir(parents=True, exist_ok=True)
        count = write_projected_grid_gpx(target, source_wkt, transform, width, height,
                                         args.projected_grid_metres)
        print(json.dumps({"validation_gpx": str(target), "checkpoints": count,
                          "calibration_fit_max_abs_error_degrees": forward_error,
                          "inverse_calibration_fit_max_abs_error_pixels": inverse_error}, indent=2))
        return 0
    if args.destination is None:
        parser.error("destination is required unless --test or --source-grid-gpx is used")
    gpx_path = args.validation_gpx
    if gpx_path == Path("AUTO"):
        gpx_path = args.destination.with_suffix(".validation.gpx")
    projected_path = args.projected_grid_gpx
    if projected_path is not None and projected_path.exists() and not args.overwrite:
        raise FileExistsError(f"Refusing to overwrite existing GPX: {projected_path}")
    if args.source.suffix.lower() == ".pdf":
        report = convert_geopdf(args.source, args.destination, dpi=args.pdf_dpi, page=args.pdf_page,
                                overwrite=args.overwrite, keep_work=args.keep_work,
                                validation_gpx=gpx_path, validation_grid=args.validation_grid,
                                projected_grid_gpx=projected_path,
                                projected_grid_spacing=args.projected_grid_metres)
    else:
        report = convert_geotiff(args.source, args.destination, overwrite=args.overwrite,
                                 keep_work=args.keep_work, validation_gpx=gpx_path,
                                 validation_grid=args.validation_grid)
        if projected_path is not None:
            projected_path.parent.mkdir(parents=True, exist_ok=True)
            report["projected_grid_gpx"] = str(projected_path)
            report["projected_grid_checkpoints"] = write_projected_grid_validation(
                args.source, projected_path, args.projected_grid_metres)
            args.destination.with_suffix(args.destination.suffix + ".validation.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
