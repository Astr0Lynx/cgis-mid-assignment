"""Live mode end to end, with a fake Bhoonidhi: search -> download -> coverage check -> index, via the WPS endpoint."""
import json
from datetime import date

import pytest

from app import live_fetch
from app.bhoonidhi_client import BhoonidhiClient, BhoonidhiError
from tests.live_helpers import FakeBhoonidhi, SMALL_BBOX_LATLON, feature, footprint, make_zip

SCENE = "R2323MAY2026078317010000060PSANSTUC00GTDF"
OLDER = "R2318MAY2026078246009900060PSANSTUC00GTDF"
BBOX = (77.49, 17.49, 77.51, 17.51)  # minx, miny, maxx, maxy
OTHER_PLACE = (78.60, 17.49)


def client_for(api):
    return BhoonidhiClient("student", "correct-horse", session=api, sleep=lambda s: None)


# ---- enabling -------------------------------------------------------------------------------------------------

def test_live_mode_needs_the_flag_and_both_credentials(monkeypatch):
    for k in ("BHOONIDHI_LIVE", "BHOONIDHI_USER", "BHOONIDHI_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert not live_fetch.enabled()
    monkeypatch.setenv("BHOONIDHI_LIVE", "1")
    assert not live_fetch.enabled()  # no credentials
    monkeypatch.setenv("BHOONIDHI_USER", "u")
    assert not live_fetch.enabled()
    monkeypatch.setenv("BHOONIDHI_PASSWORD", "p")
    assert live_fetch.enabled()
    monkeypatch.setenv("BHOONIDHI_LIVE", "0")
    assert not live_fetch.enabled()


# ---- scene selection --------------------------------------------------------------------------------------------

def test_candidates_drop_offline_and_non_covering_scenes_and_sort_newest_first():
    api = FakeBhoonidhi([
        feature(OLDER),
        feature(SCENE),
        feature("R2301MAY2026078000000000000PSANSTUC00GTDF", online="N"),
        feature("R2310MAY2026078111111111111PSANSTUC00GTDF", geometry=footprint(center=OTHER_PLACE)),
    ])
    found = live_fetch.candidates(BBOX, None, client_for(api))
    assert [i.id for i in found] == [SCENE, OLDER]


def test_a_given_date_searches_exactly_that_day():
    api = FakeBhoonidhi([feature(SCENE)])
    live_fetch.candidates(BBOX, date(2026, 5, 23), client_for(api))
    body = next(kw["json"] for _, path, kw in api.calls if path == "/data/search")
    assert body["datetime"] == "2026-05-23T00:00:00Z/2026-05-23T23:59:59Z"


def test_without_a_date_the_search_looks_back_a_bounded_window():
    api = FakeBhoonidhi([feature(SCENE)])
    live_fetch.candidates(BBOX, None, client_for(api))
    body = next(kw["json"] for _, path, kw in api.calls if path == "/data/search")
    start, end = (part[:10] for part in body["datetime"].split("/"))
    assert (date.fromisoformat(end) - date.fromisoformat(start)).days == live_fetch.LOOKBACK_DAYS


# ---- ensure_scene ---------------------------------------------------------------------------------------------------

def test_ensure_scene_downloads_checks_coverage_and_reports_progress(tmp_path):
    api = FakeBhoonidhi([feature(SCENE)], archives={SCENE: make_zip(tmp_path, SCENE)})
    steps = []
    scene = live_fetch.ensure_scene(BBOX, None, tmp_path / "data", progress=lambda m, p: steps.append((m, p)),
                                    client=client_for(api))
    assert scene.scene_id == SCENE and scene.date.date() == date(2026, 5, 23)
    assert [p for _, p in steps] == sorted(p for _, p in steps)  # progress only moves forward
    assert steps[0][0] == "Searching Bhoonidhi" and steps[-1][0] == "Checking coverage"


def test_a_scene_whose_pixels_do_not_cover_the_bbox_is_skipped_for_the_next(tmp_path):
    # footprint says "covers", but the pixels are somewhere else (a tilted-edge style mismatch)
    bad_zip = make_zip(tmp_path, SCENE, center=OTHER_PLACE)
    good_zip = make_zip(tmp_path, OLDER, date_text="18-MAY-2026")
    api = FakeBhoonidhi([feature(SCENE), feature(OLDER)], archives={SCENE: bad_zip, OLDER: good_zip})
    scene = live_fetch.ensure_scene(BBOX, None, tmp_path / "data", client=client_for(api))
    assert scene.scene_id == OLDER and api.count("/download") == 2


def test_downloads_per_request_are_capped_to_protect_the_quota(tmp_path, monkeypatch):
    monkeypatch.setattr(live_fetch, "MAX_DOWNLOADS", 1)
    bad = make_zip(tmp_path, SCENE, center=OTHER_PLACE)
    api = FakeBhoonidhi([feature(SCENE), feature(OLDER)], archives={SCENE: bad, OLDER: bad})
    with pytest.raises(BhoonidhiError, match="do not fully cover"):
        live_fetch.ensure_scene(BBOX, None, tmp_path / "data", client=client_for(api))
    assert api.count("/download") == 1


def test_nothing_found_is_a_clear_error(tmp_path):
    with pytest.raises(BhoonidhiError, match="No online Resourcesat LISS-III scene.*2026-05-23"):
        live_fetch.ensure_scene(BBOX, date(2026, 5, 23), tmp_path / "data", client=client_for(FakeBhoonidhi()))


# ---- through the WPS endpoint -------------------------------------------------------------------------------------------

EXEC = ("/wps?service=WPS&version=1.0.0&request=Execute&identifier=ndvi"
        f"&DataInputs=bbox={SMALL_BBOX_LATLON},urn:ogc:def:crs:EPSG::4326")


@pytest.fixture
def server(tmp_path, monkeypatch):
    from app.server import flask_app

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr("app.processes.ndvi.DATA_DIR", data)
    monkeypatch.setattr(live_fetch, "_client", None)
    for k in ("BHOONIDHI_LIVE", "BHOONIDHI_USER", "BHOONIDHI_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    return flask_app.test_client(), data, tmp_path, monkeypatch


def turn_on(monkeypatch, api):
    monkeypatch.setenv("BHOONIDHI_LIVE", "1")
    monkeypatch.setenv("BHOONIDHI_USER", "student")
    monkeypatch.setenv("BHOONIDHI_PASSWORD", "correct-horse")
    monkeypatch.setattr(live_fetch, "_client", client_for(api))


def test_by_default_there_is_no_live_fetching_and_nothing_is_contacted(server):
    client, data, tmp, mp = server
    api = FakeBhoonidhi([feature(SCENE)], archives={SCENE: make_zip(tmp, SCENE)})
    mp.setattr(live_fetch, "_client", client_for(api))  # present, but the mode is off
    r = client.get(EXEC)
    assert b"No available scene fully covers" in r.data
    assert api.calls == [] and not list(data.iterdir())


def test_live_mode_fetches_computes_and_then_serves_from_disk(server):
    client, data, tmp, mp = server
    api = FakeBhoonidhi([feature(SCENE)], archives={SCENE: make_zip(tmp, SCENE)})
    turn_on(mp, api)

    first = client.get(EXEC + "&RawDataOutput=stats")
    stats = json.loads(first.data)
    assert stats["scene_id"] == SCENE and stats["date"] == "2026-05-23" and stats["valid_fraction"] == 1.0
    assert (data / SCENE / "BAND_META.txt").exists()
    # constant synthetic bands: NDVI = (nir-red)/(nir+red) on radiance, to within float rounding
    nir, red = 700 * 31.5 / 1023, 300 * 47.0 / 1023
    assert stats["mean"] == pytest.approx((nir - red) / (nir + red), rel=1e-4)

    downloads = api.count("/download")
    second = client.get(EXEC + "&RawDataOutput=stats")
    assert json.loads(second.data)["scene_id"] == SCENE
    assert api.count("/download") == downloads  # now local: no second download, no second search


def test_live_mode_honours_the_date_input(server):
    client, data, tmp, mp = server
    api = FakeBhoonidhi([feature(OLDER)], archives={OLDER: make_zip(tmp, OLDER, date_text="18-MAY-2026")})
    turn_on(mp, api)
    stats = json.loads(client.get(EXEC + ";date=2026-05-18&RawDataOutput=stats").data)
    assert stats["date"] == "2026-05-18"
    body = next(kw["json"] for _, path, kw in api.calls if path == "/data/search")
    assert body["datetime"].startswith("2026-05-18T00:00:00Z")


def test_live_failures_become_clean_ogc_exceptions_without_secrets(server):
    client, data, tmp, mp = server
    turn_on(mp, FakeBhoonidhi())  # nothing found
    r = client.get(EXEC)
    assert b"ProcessFailed" in r.data and b"No online Resourcesat LISS-III scene" in r.data

    api = FakeBhoonidhi([feature(SCENE)], password="something-else")  # wrong password
    turn_on(mp, api)
    r = client.get(EXEC)
    assert b"authentication failed" in r.data
    assert b"correct-horse" not in r.data and b"student" not in r.data
