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


# ---- any date range, not a fixed list of presets ------------------------------------------------------------------------------
def listing(c, **body):
    return post(c, "/api/scenes", {"bbox": AREA, **body})


def test_the_listing_honours_the_chosen_start_and_end(env):
    c, api, _ = env
    api.filter_dates = True
    login(c)
    only_may = listing(c, start="2026-05-01", end="2026-05-31").get_json()
    assert [s["date"] for s in only_may["scenes"]] == ["2026-05-23", "2026-05-18"] and only_may["more"] is False
    assert (only_may["start"], only_may["end"]) == ("2026-05-01", "2026-05-31")
    assert listing(c, start="2026-06-01", end="2026-09-30").get_json()["scenes"] == []   # a gap: honestly empty


def test_a_long_range_is_searched_in_year_windows_newest_first(env, monkeypatch):
    c, api, _ = env
    api.filter_dates = True
    login(c)
    got = listing(c, start="2022-01-01", end="2026-10-12").get_json()
    assert [s["date"] for s in got["scenes"]] == ["2026-10-09", "2026-05-23", "2026-05-18"]   # older windows are searched too
    windows = api.search_windows
    assert len(windows) == 5 and windows[0][1].isoformat() == "2026-10-12" and windows == sorted(windows, reverse=True)
    assert all((hi - lo).days <= 364 for lo, hi in windows)


def test_a_range_with_more_scenes_than_the_list_shows_says_so(env, monkeypatch):
    from app import live_fetch

    c, api, _ = env
    api.filter_dates = True
    monkeypatch.setattr(live_fetch, "LIST_LIMIT", 2)
    login(c)
    got = listing(c, start="2026-01-01", end="2026-10-12").get_json()
    assert [s["date"] for s in got["scenes"]] == ["2026-10-09", "2026-05-23"] and got["more"] is True  # the oldest was left out


@pytest.mark.parametrize("body", [
    {"start": "2026-06-01", "end": "2026-05-01"},      # start after end
    {"start": "2026-05-01", "end": "2999-01-01"},      # the future
    {"start": "2005-01-01", "end": "2026-05-01"},      # before Resourcesat-2
    {"start": "yesterday", "end": "2026-05-01"},       # not a date
])
def test_bad_date_ranges_are_refused(env, body):
    c, *_ = env
    login(c)
    assert listing(c, **body).status_code == 400


def test_the_whole_history_since_2011_can_be_searched(env):
    c, api, _ = env
    api.filter_dates = True
    login(c)
    got = listing(c, start="2011-01-01", end="2026-10-12")
    assert got.status_code == 200 and len(got.get_json()["scenes"]) == 3
    assert len(api.search_windows) == 16                                # one search per year-long window


def test_a_scene_returned_by_several_searches_is_listed_once(env):
    c, api, _ = env
    login(c)                      # this fake ignores the date range, so every year-long window returns the same scenes
    got = listing(c, start="2011-01-01", end="2026-10-12").get_json()["scenes"]
    assert sorted(s["id"] for s in got) == sorted([MISSES, SCENE, OLDER])


# ---- a full page of results must not hide the newest scenes ---------------------------------------------------------------------
def many_scenes(n_days, step=2):
    from datetime import date, timedelta

    months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    out, day = [], date(2025, 10, 13)
    for k in range(0, n_days, step):
        d = day + timedelta(days=k)
        out.append(feature(f"R2{d.day:02d}{months[d.month - 1]}{d.year}0782460099000{k % 10}60PSANSTUC00GTDF"))
    return out


def test_a_year_with_more_scenes_than_one_search_returns_still_lists_the_newest(env):
    c, api, _ = env
    api.features = many_scenes(364, step=2)           # 182 scenes in the year: more than one page of 100
    api.filter_dates = True
    api.truncate_at = 100                              # the real search cuts an over-full answer off, keeping the OLDEST
    login(c)
    got = listing(c, start="2025-10-13", end="2026-10-12").get_json()
    dates = [s["date"] for s in got["scenes"]]
    assert dates == sorted(dates, reverse=True) and len(dates) == 40 and got["more"] is True
    assert dates[0] >= "2026-10-08"                    # the newest scenes are there, not a list that stops months earlier
    searches = api.search_windows
    assert len(searches) > 1 and all((hi - lo).days < 364 for lo, hi in searches[1:])   # the full page was split


def test_the_newest_scene_fetch_also_survives_a_full_page(env):
    from app import live_fetch

    c, api, _ = env
    api.features = many_scenes(364, step=1)           # a scene a day for a year
    api.filter_dates = True
    api.truncate_at = 100
    login(c)
    items = live_fetch.search_complete(webauth.store._sessions[next(iter(webauth.store._sessions))].client,
                                       (77.49, 17.49, 77.51, 17.51), __import__("datetime").date(2025, 10, 13),
                                       __import__("datetime").date(2026, 10, 12))
    assert len(items) == 364
