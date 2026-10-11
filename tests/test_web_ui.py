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
    assert len(ring) == 5 and ring[0] == ring[-1]  # a closed quadrilateral (the tilted image corners)
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
