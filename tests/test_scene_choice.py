"""Choosing a particular Bhoonidhi scene (date) instead of always taking the newest one."""
import pytest

from app import fetchjobs, webauth
from app.bhoonidhi_client import BhoonidhiClient
from app.scene import fetch_dir
from tests.live_helpers import CENTER, FakeBhoonidhi, feature, make_zip
from tests.test_fetchjobs import AREA, HEADERS, SCENE, login, post, wait  # noqa: F401

OLDER = "R2318MAY2026078246009900060PSANSTUC00GTDF"  # 18 May
MISSES = "R2309OCT2026080292009900060PSANSTUC00GTDF"  # the search lists it, but its true image is elsewhere


def corners(lon, lat, half):
    return {"UL": (lon - half, lat + half), "UR": (lon + half, lat + half), "LR": (lon + half, lat - half), "LL": (lon - half, lat - half)}


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app.server import flask_app

    api = FakeBhoonidhi([feature(MISSES), feature(SCENE), feature(OLDER)], archives={
        SCENE: make_zip(tmp_path / "z1", SCENE), OLDER: make_zip(tmp_path / "z2", OLDER, "18-MAY-2026"),
        MISSES: make_zip(tmp_path / "z3", MISSES, "09-OCT-2026")})
    api.meta_corners = {SCENE: corners(*CENTER, 0.5), OLDER: corners(*CENTER, 0.5), MISSES: corners(78.9, 17.5, 0.3)}
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("BROWSER_LOGIN", "1")
    monkeypatch.setattr(webauth, "make_client",
                        lambda u, p: BhoonidhiClient(u, p, session=api, sleep=lambda s: None, download_auth="portal"))
    monkeypatch.setattr("app.scene.DATA_DIR", data)
    monkeypatch.setattr("app.processes.ndvi.DATA_DIR", data)
    monkeypatch.setattr(fetchjobs, "SEARCH_COOLDOWN_S", 0)
    webauth.store.__init__()
    fetchjobs.jobs.__init__()
    fetchjobs._last_search.clear()
    return flask_app.test_client(), api, data


def test_listing_needs_login_and_the_feature_switched_on(env, monkeypatch):
    c, *_ = env
    assert post(c, "/api/scenes", {"bbox": AREA}).status_code == 401
    monkeypatch.delenv("BROWSER_LOGIN")
    assert post(c, "/api/scenes", {"bbox": AREA}).status_code == 403
    monkeypatch.setenv("BROWSER_LOGIN", "1")
    assert c.post("/api/scenes", json={"bbox": AREA}).status_code == 400  # no X-Requested-With header


def test_the_listing_offers_every_date_and_says_which_images_truly_cover_the_area(env):
    c, *_ = env
    login(c)
    scenes = post(c, "/api/scenes", {"bbox": AREA}).get_json()["scenes"]
    by_id = {s["id"]: s for s in scenes}
    assert [s["date"] for s in scenes] == ["2026-10-09", "2026-05-23", "2026-05-18"]  # newest first
    assert by_id[SCENE]["covers"] is True and by_id[OLDER]["covers"] is True
    assert by_id[MISSES]["covers"] is False                                            # tilted away: flagged, not hidden


def test_listing_validates_input_and_is_rate_limited(env, monkeypatch):
    c, *_ = env
    login(c)
    assert post(c, "/api/scenes", {"bbox": [1, 2]}).status_code == 400
    assert post(c, "/api/scenes", {"bbox": [20, 80, 10, 70]}).status_code == 400
    monkeypatch.setattr(fetchjobs, "SEARCH_COOLDOWN_S", 60)
    fetchjobs._last_search.clear()
    assert post(c, "/api/scenes", {"bbox": AREA}).status_code == 200
    assert post(c, "/api/scenes", {"bbox": AREA}).status_code == 429


def test_fetching_a_chosen_scene_gets_exactly_that_one(env):
    c, api, data = env
    login(c)
    r = post(c, "/api/fetch", {"bbox": AREA, "date": "2026-05-18", "scene_id": OLDER})
    done = wait(c, r.get_json()["job"])
    assert done["state"] == "done" and done["scene"] == {"id": OLDER, "date": "2026-05-18"}
    assert (fetch_dir(data) / OLDER).exists() and not (fetch_dir(data) / SCENE).exists()


def test_without_a_choice_the_newest_suitable_scene_is_still_the_default(env):
    c, *_ = env
    login(c)
    done = wait(c, post(c, "/api/fetch", {"bbox": AREA}).get_json()["job"])
    assert done["scene"]["id"] == SCENE  # the 9 Oct scene is skipped: its true footprint misses the area


def test_a_scene_that_does_not_cover_the_area_is_refused_without_downloading(env):
    c, api, data = env
    login(c)
    done = wait(c, post(c, "/api/fetch", {"bbox": AREA, "scene_id": MISSES}).get_json()["job"])
    assert done["state"] == "error" and "does not cover" in done["error"]
    assert not [p for _, p, _ in api.calls if p.startswith("/bhoonidhi/data/")]


@pytest.mark.parametrize("bad", ["", "../etc/passwd", "a b", 123, "x" * 200])
def test_scene_ids_are_validated(env, bad):
    c, *_ = env
    login(c)
    assert post(c, "/api/fetch", {"bbox": AREA, "scene_id": bad}).status_code == 400
