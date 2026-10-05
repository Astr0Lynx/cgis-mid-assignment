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


# --- TOA reflectance -----------------------------------------------------------------------------
import json  # noqa: E402
import math  # noqa: E402

from app import radiometry  # noqa: E402

META = {"B3_Lmin": "0", "B3_Lmax": "47", "B4_Lmin": "0", "B4_Lmax": "31.5",
        "DateOfPass": "23-MAY-2026", "SunElevationAtCenter": "65.0"}


def test_earth_sun_distance_known_points():
    # aphelion ~ July 4 (1.0167 AU), perihelion ~ Jan 3 (0.9833 AU)
    assert radiometry.earth_sun_distance_au(185) == pytest.approx(1.0167, abs=5e-4)
    assert radiometry.earth_sun_distance_au(3) == pytest.approx(0.9833, abs=5e-4)


def test_radiance_to_toa_hand_computed():
    # sun overhead (elevation 90 -> cos(zenith) = 1), d = 1 AU: rho = pi * L / ESUN
    rho = radiometry.radiance_to_toa(np.array([10.0], dtype=np.float32), esun=100.0, d_au=1.0, sun_elevation_deg=90.0)
    assert rho[0] == pytest.approx(math.pi * 10 / 100, rel=1e-6)
    # 30 deg elevation halves the cosine term -> doubles reflectance
    low = radiometry.radiance_to_toa(np.array([10.0], dtype=np.float32), 100.0, 1.0, 30.0)
    assert low[0] == pytest.approx(2 * rho[0], rel=1e-5)


def test_toa_refused_without_cited_esun():
    with pytest.raises(radiometry.EsunNotConfigured, match="cited source"):
        radiometry.to_physical(np.array([100], dtype=np.uint16), META, 3, mode="toa")


def test_load_esun_demands_source_and_all_bands(tmp_path):
    f = tmp_path / "esun.json"
    f.write_text(json.dumps({"source": "x", "bands": {"2": 1, "3": 2, "4": 3, "5": None}}))
    with pytest.raises(radiometry.EsunNotConfigured):
        radiometry.load_esun(f)
    f.write_text(json.dumps({"source": None, "bands": {"2": 1, "3": 2, "4": 3, "5": 4}}))
    with pytest.raises(radiometry.EsunNotConfigured):
        radiometry.load_esun(f)
    f.write_text(json.dumps({"source": "test", "bands": {"2": 1, "3": 2, "4": 3, "5": 4}}))
    assert radiometry.load_esun(f) == {2: 1.0, 3: 2.0, 4: 3.0, 5: 4.0}


def test_toa_end_to_end_with_configured_table(monkeypatch):
    monkeypatch.setattr(radiometry, "load_esun", lambda: {2: 10.0, 3: 20.0, 4: 30.0, 5: 40.0})
    dn = np.array([0, 1023], dtype=np.uint16)
    out = radiometry.to_physical(dn, META, 3, mode="toa")
    assert np.isnan(out[0])  # background stays no-data
    d = radiometry.earth_sun_distance_au(143)  # 23 May
    assert out[1] == pytest.approx(math.pi * 47.0 * d * d / (20.0 * math.sin(math.radians(65.0))), rel=1e-5)


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        radiometry.to_physical(np.array([1], dtype=np.uint16), META, 3, mode="bogus")


# --- index registry -------------------------------------------------------------------------------
from app import indices  # noqa: E402,F811


def test_registry_band_pairs_and_formulas():
    expected = {"ndvi": ("nir", "red"), "ndwi": ("green", "nir"), "mndwi": ("green", "swir"), "ndbi": ("swir", "nir")}
    assert {k: v.bands for k, v in indices.INDICES.items()} == expected
    assert indices.INDICES["ndwi"].formula == "(green-nir)/(green+nir)"


def test_compute_hand_values():
    bands = {k: np.array([v], dtype=np.float32) for k, v in dict(green=6.0, red=2.0, nir=10.0, swir=4.0).items()}
    assert indices.compute("ndvi", bands)[0] == pytest.approx((10 - 2) / (10 + 2))
    assert indices.compute("ndwi", bands)[0] == pytest.approx((6 - 10) / (6 + 10))
    assert indices.compute("mndwi", bands)[0] == pytest.approx((6 - 4) / (6 + 4))
    assert indices.compute("ndbi", bands)[0] == pytest.approx((4 - 10) / (4 + 10))


def test_savi_and_evi_are_not_offered():
    assert "savi" not in indices.INDICES and "evi" not in indices.INDICES


@needs_data
def test_hussain_sagar_lake_is_the_wettest_and_least_vegetated_place():
    """Real-data check of band wiring and geolocation: the lake must stand out in the right direction."""
    from rasterio.warp import transform

    bb = (78.40, 17.35, 78.55, 17.45)
    scene = find_scenes(bb)[0]
    lon, lat = 78.4738, 17.4239  # Hussain Sagar, approx. centre
    for name, lake_is_high in (("ndwi", True), ("mndwi", True), ("ndvi", False), ("ndbi", False)):
        clip = scene.index(bb, name)
        x, y = transform("EPSG:4326", clip.crs, [lon], [lat])
        col, row = (int(v) for v in ~clip.transform @ (x[0], y[0]))
        lake = float(np.nanmean(clip.data[row - 2:row + 3, col - 2:col + 3]))
        pct = float((clip.data[np.isfinite(clip.data)] < lake).mean())
        assert (pct > 0.95) if lake_is_high else (pct < 0.05), f"{name}: lake at percentile {pct:.2f}"
