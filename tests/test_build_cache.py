"""build_cache.py must produce a scene folder that gives the same indices as the full scene."""
import importlib.util
import shutil
from pathlib import Path

import numpy as np
import pytest
import rasterio

from app.indices import INDICES
from app.scene import DATA_DIR, Scene, find_scenes

needs_data = pytest.mark.skipif(not list(DATA_DIR.glob("*/BAND_META.txt")), reason="no scenes in data/")
HYDERABAD = (78.40, 17.35, 78.55, 17.45)
AOI = (78.30, 17.30, 78.60, 17.50)  # a bit larger than the request window, much smaller than the default AOI


def load_build_cache():
    path = Path(__file__).resolve().parent.parent / "scripts" / "build_cache.py"
    spec = importlib.util.spec_from_file_location("build_cache", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@needs_data
def test_cache_gives_identical_indices_and_is_small(tmp_path):
    bc = load_build_cache()
    full = find_scenes(HYDERABAD)[0]
    out = tmp_path / full.scene_id
    out.mkdir()
    for band in ("green", "red", "nir", "swir"):
        bc.clip_band(full, band, AOI, out / full.band_path(band).name)
    shutil.copy(full.folder / "BAND_META.txt", out / "BAND_META.txt")

    cached = Scene(out)
    assert cached.scene_id == full.scene_id and cached.date == full.date
    for name in INDICES:
        a = full.index(HYDERABAD, name).data
        b = cached.index(HYDERABAD, name).data
        assert a.shape == b.shape and np.array_equal(a, b, equal_nan=True), name

    # clipped COGs hold the original raw DN values (uint16), compressed and much smaller than the source
    with rasterio.open(out / "BAND4.tif") as ds:
        assert ds.dtypes[0] == "uint16" and ds.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
    assert sum(f.stat().st_size for f in out.iterdir()) < 0.2 * sum(
        f.stat().st_size for f in full.folder.glob("BAND*.tif")
    )
