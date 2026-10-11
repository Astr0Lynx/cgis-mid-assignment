"""The map page and its two JSON endpoints (the page itself is a client of the WPS)."""
import json

import pytest

from app.scene import DATA_DIR

needs_data = pytest.mark.skipif(not list(DATA_DIR.glob("*/BAND_META.txt")), reason="no scenes in data/")


@pytest.fixture(scope="module")
def client():
    from app.server import flask_app
    return flask_app.test_client()


def test_page_is_served_and_uses_only_local_assets(client):
    r = client.get("/")
    assert r.status_code == 200 and r.headers["Content-Type"].startswith("text/html")
    html = r.data.decode()
    assert "Bhoonidhi WPS Explorer" in html and "/static/app.js" in html and "/static/vendor/leaflet/leaflet.js" in html
    assert "http://" not in html.replace("http://www.", "").replace("https://", "")  # no script/style from a CDN
    for asset in ("/static/app.js", "/static/style.css", "/static/vendor/leaflet/leaflet.js",
                  "/static/vendor/leaflet/leaflet.css", "/static/vendor/leaflet/images/layers.png"):
        assert client.get(asset).status_code == 200, asset


def test_info_lists_the_indices_and_limits(client):
    d = client.get("/api/info").get_json()
    assert [i["name"] for i in d["indices"]] == ["ndvi", "ndwi", "mndwi", "ndbi"]
    assert d["indices"][0]["formula"] == "(nir-red)/(nir+red)" and d["resolution_m"] == 24 and d["max_pixels"] > 0


@needs_data
def test_coverage_returns_true_image_footprints_newest_first(client):
    fc = client.get("/api/coverage").get_json()
    assert fc["type"] == "FeatureCollection" and fc["features"]
    dates = [f["properties"]["date"] for f in fc["features"]]
    assert dates == sorted(dates, reverse=True)
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    assert len(ring) >= 5 and ring[0] == ring[-1]  # a closed polygon: the tilted image corners, possibly clipped by the raster
    assert all(70 < lon < 90 and 8 < lat < 30 for lon, lat in ring)


def test_coverage_skips_folders_without_usable_metadata(client, tmp_path, monkeypatch):
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "BAND_META.txt").write_text("OTSProductID= x\n")  # no corners, no date
    monkeypatch.setattr("app.scene.DATA_DIR", tmp_path)
    assert client.get("/api/coverage").get_json()["features"] == []


def test_the_request_the_page_builds_is_a_valid_wps_execute(client):
    """Mirror of executeUrl() in app.js: if this stops working, the page's Run button is broken."""
    s, w, n, e = 17.35, 78.40, 17.45, 78.55
    url = (f"/wps?service=WPS&version=1.0.0&request=Execute&identifier=spectral-index&DataInputs="
           f"bbox={s},{w},{n},{e},urn:ogc:def:crs:EPSG::4326;index=ndvi;date=2026-05-23;radiometry=radiance")
    text = client.get(url).data.decode()
    assert "ProcessSucceeded" in text or "No available scene" in text  # succeeds where data exists, else a clean error


def test_coverage_only_promises_the_data_that_is_really_on_disk(client, tmp_path, monkeypatch):
    """Regression: the outline used to be the scene's full footprint although the cache holds only a clip of it."""
    from tests.live_helpers import make_scene

    folder = make_scene(tmp_path / "R2323MAY2026999999999900060PSANSTUC00GTDF", "R2323MAY2026999999999900060PSANSTUC00GTDF")
    meta = folder / "BAND_META.txt"
    big = {"UL": (76.5, 18.5), "UR": (78.5, 18.5), "LR": (78.5, 16.5), "LL": (76.5, 16.5)}  # +-1 degree: far bigger than the 7 km raster
    meta.write_text(meta.read_text() + "".join(f"Image{c}Lon= {lon}\nImage{c}Lat= {lat}\n" for c, (lon, lat) in big.items()))
    monkeypatch.setattr("app.scene.DATA_DIR", tmp_path)
    ring = client.get("/api/coverage").get_json()["features"][0]["geometry"]["coordinates"][0]
    lons, lats = [p[0] for p in ring], [p[1] for p in ring]
    assert max(lons) - min(lons) < 0.1 and max(lats) - min(lats) < 0.1  # about 7 km, not 2 degrees
    assert min(lons) < 77.5 < max(lons) and min(lats) < 17.5 < max(lats)  # and it is where the raster actually is


def test_a_scene_without_corner_metadata_is_still_listed_by_its_raster_extent(client, tmp_path, monkeypatch):
    """Found by the browser test: such a scene used to vanish from the page's coverage, although the WPS could use it."""
    from tests.live_helpers import make_scene

    make_scene(tmp_path / "R2301OCT2026999999999900060PSANSTUC00GTDF", "R2301OCT2026999999999900060PSANSTUC00GTDF")
    monkeypatch.setattr("app.scene.DATA_DIR", tmp_path)
    features = client.get("/api/coverage").get_json()["features"]
    assert len(features) == 1
    ring = features[0]["geometry"]["coordinates"][0]
    assert max(p[0] for p in ring) - min(p[0] for p in ring) < 0.1  # the 7 km raster, not a made-up footprint
