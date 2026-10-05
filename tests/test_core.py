import numpy as np
import pytest
import rasterio

from app import indices
from app.radiometry import dn_to_radiance, to_physical
from app.scene import DATA_DIR, find_scenes, write_cog

# ~10 km window over central Hyderabad (minx, miny, maxx, maxy), lon/lat
HYDERABAD = (78.40, 17.35, 78.55, 17.45)


def test_radiance_linear_and_background_masked():
    dn = np.array([0, 1, 512, 1023], dtype=np.uint16)
    out = dn_to_radiance(dn, lmin=0.0, lmax=47.0)
    assert np.isnan(out[0])
    assert out[3] == pytest.approx(47.0)
    assert out[2] == pytest.approx(512 * 47.0 / 1023.0, rel=1e-5)


def test_toa_mode_not_implemented_yet():
    with pytest.raises(NotImplementedError):
        to_physical(np.ones(2, dtype=np.uint16), {}, 3, mode="toa")


def test_ndvi_values_and_invalid():
    nir = np.array([0.8, 0.5, np.nan, 0.0], dtype=np.float32)
    red = np.array([0.2, 0.5, 0.3, 0.0], dtype=np.float32)
    out = indices.ndvi(nir, red)
    assert out[0] == pytest.approx(0.6)
    assert out[1] == pytest.approx(0.0)
    assert np.isnan(out[2]) and np.isnan(out[3])


needs_data = pytest.mark.skipif(not list(DATA_DIR.glob("*/BAND_META.txt")), reason="no scenes in data/")


@needs_data
def test_hyderabad_clip_ndvi_in_range(tmp_path):
    scenes = find_scenes(HYDERABAD)
    assert scenes, "no scene covers Hyderabad"
    # the 18 May scene's tilted edge cuts through this window; it must be rejected
    assert all(s.date.day != 18 for s in scenes)
    clip = scenes[0].ndvi(HYDERABAD)
    valid = clip.data[np.isfinite(clip.data)]
    assert clip.data.shape[0] > 300 and clip.data.shape[1] > 300  # ~10x15 km at 24 m
    assert valid.size > 0.9 * clip.data.size  # window is inside the image, not a wedge
    assert -1.0 <= valid.min() and valid.max() <= 1.0

    out = write_cog(clip, tmp_path / "ndvi.tif")
    with rasterio.open(out) as ds:
        assert ds.dtypes[0] == "float32" and ds.crs.to_epsg() == clip.crs.to_epsg()
        assert ds.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
