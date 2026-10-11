"""The colour preview PNG that the map page overlays."""
import numpy as np
import pytest
import rasterio

from app.preview import RAMPS, colorize, png_bytes, render_preview
from app.scene import Scene
from tests.live_helpers import make_scene

BBOX = (77.49, 17.49, 77.51, 17.51)


def decode(png: bytes):
    with rasterio.MemoryFile(png) as mf, mf.open() as ds:
        return ds.read()  # (bands, h, w)


def test_colorize_maps_the_ends_of_the_range_to_the_ends_of_the_ramp_and_hides_nodata():
    data = np.array([[0.0, 1.0, np.nan]], dtype=np.float32)
    rgba = colorize(data, 0.0, 1.0, ["#000000", "#ffffff"])
    assert rgba[0, 0].tolist() == [0, 0, 0, 255] and rgba[0, 1].tolist() == [255, 255, 255, 255]
    assert rgba[0, 2, 3] == 0  # NaN is transparent
    mid = colorize(np.array([[0.5]], dtype=np.float32), 0.0, 1.0, ["#000000", "#ffffff"])
    assert 127 <= mid[0, 0, 0] <= 128


def test_values_outside_the_stretch_are_clamped_not_wrapped():
    rgba = colorize(np.array([[-5.0, 5.0]], dtype=np.float32), 0.0, 1.0, ["#102030", "#a0b0c0"])
    assert rgba[0, 0, :3].tolist() == [0x10, 0x20, 0x30] and rgba[0, 1, :3].tolist() == [0xA0, 0xB0, 0xC0]


def test_png_roundtrips_through_a_real_decoder():
    rgba = np.zeros((3, 4, 4), dtype=np.uint8)
    rgba[1, 2] = [10, 20, 30, 255]
    png = png_bytes(rgba)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    bands = decode(png)
    assert bands.shape == (4, 3, 4) and bands[:, 1, 2].tolist() == [10, 20, 30, 255] and bands[3, 0, 0] == 0


def test_every_index_has_a_ramp():
    assert set(RAMPS) == {"ndvi", "ndwi", "mndwi", "ndbi"} and all(len(r) >= 5 for r in RAMPS.values())


def test_render_preview_is_georeferenced_in_degrees_and_covers_the_requested_box(tmp_path):
    scene = Scene(make_scene(tmp_path / "s", "R2323MAY2026999999999900060PSANSTUC00GTDF"))
    clip = scene.index(BBOX, "ndvi")
    # give the synthetic clip some contrast and a no-data corner
    data = np.linspace(-0.2, 0.4, clip.data.size, dtype=np.float32).reshape(clip.data.shape)
    data[:3, :3] = np.nan
    pv = render_preview(data, clip.transform, clip.crs, "ndvi")
    south, west, north, east = pv.bounds
    assert south < 17.49 + 0.002 and north > 17.51 - 0.002 and west < 77.49 + 0.002 and east > 77.51 - 0.002
    assert 0 < north - south < 0.05 and 0 < east - west < 0.05  # a couple of kilometres, in degrees
    bands = decode(pv.png)
    assert bands.shape[0] == 4 and (bands[3] == 0).any() and (bands[3] == 255).any()  # transparent corner + data
    assert pv.vmin < pv.vmax and len(pv.ramp) == len(RAMPS["ndvi"])


def test_a_flat_or_empty_raster_still_gives_a_valid_image(tmp_path):
    scene = Scene(make_scene(tmp_path / "s", "R2323MAY2026999999999900060PSANSTUC00GTDF"))
    clip = scene.index(BBOX, "ndvi")
    flat = render_preview(np.full(clip.data.shape, 0.3, dtype=np.float32), clip.transform, clip.crs, "ndvi")
    assert flat.vmin < 0.3 < flat.vmax
    empty = render_preview(np.full(clip.data.shape, np.nan, dtype=np.float32), clip.transform, clip.crs, "ndvi")
    assert (decode(empty.png)[3] == 0).all()


def test_big_clips_are_downsampled_to_a_bounded_preview(tmp_path):
    scene = Scene(make_scene(tmp_path / "s", "R2323MAY2026999999999900060PSANSTUC00GTDF"))
    clip = scene.index(BBOX, "ndvi")
    big = np.random.default_rng(1).random((3000, 2500), dtype=np.float32)
    pv = render_preview(big, clip.transform, clip.crs, "ndvi")
    h, w = decode(pv.png).shape[1:]
    assert max(h, w) <= 1000
