"""Colour preview of an index raster, for display on a web map.

The WPS returns the science product (a float GeoTIFF). A browser cannot show that on a map directly, so each run also
produces a small georeferenced PNG: the raster reprojected to latitude/longitude, stretched between its 2nd and 98th
percentile, coloured with a ramp, with no-data transparent. Plain numpy + zlib (no Pillow needed).

The stretch is per request, so the colours show *relative* contrast only; the legend carries the actual values.
"""
import struct
import zlib
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.transform import array_bounds
from rasterio.warp import Resampling, calculate_default_transform, reproject

RAMPS = {
    "ndvi": ["#a50026", "#f46d43", "#fee08b", "#d9ef8b", "#66bd63", "#006837"],   # brown -> yellow -> green
    "ndwi": ["#f7fbff", "#c6dbef", "#6baed6", "#2171b5", "#08306b"],              # white -> deep blue
    "mndwi": ["#f7fbff", "#c6dbef", "#6baed6", "#2171b5", "#08306b"],
    "ndbi": ["#ffffcc", "#fed976", "#fd8d3c", "#e31a1c", "#800026"],              # pale yellow -> dark red
}
MAX_SIDE = 1000  # the preview never exceeds this many pixels on its longer side


@dataclass(frozen=True)
class Preview:
    png: bytes
    bounds: tuple[float, float, float, float]  # south, west, north, east (degrees)
    vmin: float
    vmax: float
    ramp: list[str]


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def colorize(data: np.ndarray, vmin: float, vmax: float, ramp: list[str]) -> np.ndarray:
    """RGBA uint8 image: values stretched vmin..vmax along the ramp; NaN becomes fully transparent."""
    stops = np.linspace(0.0, 1.0, len(ramp))
    colors = np.array([_rgb(c) for c in ramp], dtype=np.float64)
    t = np.clip((data - vmin) / (vmax - vmin), 0.0, 1.0) if vmax > vmin else np.zeros_like(data)
    t = np.nan_to_num(t, nan=0.0)
    rgba = np.zeros(data.shape + (4,), dtype=np.uint8)
    for channel in range(3):
        rgba[..., channel] = np.interp(t, stops, colors[:, channel]).round().astype(np.uint8)
    rgba[..., 3] = np.where(np.isfinite(data), 255, 0).astype(np.uint8)
    return rgba


def png_bytes(rgba: np.ndarray) -> bytes:
    """Encode an (h, w, 4) uint8 array as a PNG."""
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[row].tobytes() for row in range(h))  # filter type 0 on every row

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


SCALES = ("stretch", "absolute")
ABSOLUTE_RANGE = (-1.0, 1.0)  # normalised-difference indices lie in [-1, 1]


def render_preview(data: np.ndarray, transform, crs, index_name: str, colour_scale: str = "stretch") -> Preview:
    """Reproject `data` (NaN = no data) to EPSG:4326, colour it, and return the PNG with its map bounds."""
    height, width = data.shape
    left, bottom, right, top = array_bounds(height, width, transform)
    dst_transform, dst_w, dst_h = calculate_default_transform(crs, "EPSG:4326", width, height, left, bottom, right, top)
    scale = max(dst_w, dst_h) / MAX_SIDE
    if scale > 1:
        dst_w, dst_h = max(1, int(dst_w / scale)), max(1, int(dst_h / scale))
        dst_transform, dst_w, dst_h = calculate_default_transform(
            crs, "EPSG:4326", width, height, left, bottom, right, top, dst_width=dst_w, dst_height=dst_h)

    out = np.full((dst_h, dst_w), np.nan, dtype=np.float32)
    reproject(source=data.astype(np.float32), destination=out, src_transform=transform, src_crs=crs,
              dst_transform=dst_transform, dst_crs="EPSG:4326", resampling=Resampling.nearest,
              src_nodata=np.nan, dst_nodata=np.nan)

    finite = out[np.isfinite(out)]
    if colour_scale == "absolute":
        vmin, vmax = ABSOLUTE_RANGE  # the same colours mean the same values in every request
    elif finite.size:
        vmin, vmax = (float(v) for v in np.percentile(finite, [2, 98]))
        if vmax - vmin < 1e-6:  # a flat image: widen so the ramp is defined
            vmin, vmax = vmin - 0.05, vmax + 0.05
    else:
        vmin = vmax = 0.0

    ramp = RAMPS.get(index_name, RAMPS["ndvi"])
    west, north = dst_transform.c, dst_transform.f
    east, south = west + dst_w * dst_transform.a, north + dst_h * dst_transform.e
    return Preview(png_bytes(colorize(out, vmin, vmax, ramp)), (south, west, north, east), vmin, vmax, list(ramp))
