"""Fetching a scene for a logged-in browser session: a background job with progress, then the normal WPS."""
import json
import time

import pytest

from app import fetchjobs, webauth
from app.bhoonidhi_client import BhoonidhiClient
from app.scene import fetch_dir, find_scenes, scene_folders
from tests.live_helpers import FakeBhoonidhi, feature, make_scene, make_zip

HEADERS = {"X-Requested-With": "XMLHttpRequest"}
SCENE = "R2323MAY2026078317010000060PSANSTUC00GTDF"
AREA = [17.49, 77.49, 17.51, 77.51]  # minlat, minlon, maxlat, maxlon (OGC order)
BBOX = (77.49, 17.49, 77.51, 17.51)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app.server import flask_app

    api = FakeBhoonidhi([feature(SCENE)], archives={SCENE: make_zip(tmp_path / "zips", SCENE)})
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("BROWSER_LOGIN", "1")
    monkeypatch.setattr(webauth, "make_client",
                        lambda u, p: BhoonidhiClient(u, p, session=api, sleep=lambda s: None, download_auth="portal"))
    monkeypatch.setattr("app.scene.DATA_DIR", data)
    monkeypatch.setattr("app.processes.ndvi.DATA_DIR", data)
    webauth.store.__init__()
    fetchjobs.jobs.__init__()
    return flask_app.test_client(), api, data


def post(c, path, body=None):
    return c.post(path, json=body if body is not None else {}, headers=HEADERS)


def login(c):
    assert post(c, "/api/login", {"user": "student", "password": "correct-horse"}).get_json() == {"status": "ready"}


def wait(c, job, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        v = c.get(f"/api/fetch/{job}").get_json()
        if v["state"] != "running":
            return v
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_fetch_needs_a_login(env):
    c, _, _ = env
    assert post(c, "/api/fetch", {"bbox": AREA}).status_code == 401


def test_fetch_is_off_unless_browser_login_is_on(env, monkeypatch):
    c, _, _ = env
    monkeypatch.delenv("BROWSER_LOGIN")
    assert post(c, "/api/fetch", {"bbox": AREA}).status_code == 403


def test_a_fetch_downloads_into_the_expiring_folder_and_then_the_wps_can_use_it(env):
    c, api, data = env
    login(c)
    r = post(c, "/api/fetch", {"bbox": AREA})
    assert r.status_code == 202
    done = wait(c, r.get_json()["job"])
    assert done["state"] == "done" and done["percent"] == 100 and done["scene"] == {"id": SCENE, "date": "2026-05-23"}
    assert (fetch_dir(data) / SCENE / "BAND_META.txt").exists()
    assert not (data / SCENE).exists()  # never in the permanent area
    assert [s.scene_id for s in find_scenes(BBOX, data)] == [SCENE]
    # the ordinary, stateless WPS now answers for that area, with no token anywhere in the request
    url = ("/wps?service=WPS&version=1.0.0&request=Execute&identifier=spectral-index&DataInputs="
           "bbox=17.49,77.49,17.51,77.51,urn:ogc:def:crs:EPSG::4326;index=ndvi;date=2026-05-23&RawDataOutput=stats")
    stats = json.loads(c.get(url).data)
    assert stats["scene_id"] == SCENE and stats["valid_fraction"] == 1.0


def test_progress_only_moves_forward_and_names_the_steps(env, monkeypatch):
    c, api, data = env
    seen = []
    real = fetchjobs.live_fetch.ensure_scene

    def spy(bbox, wanted, d, progress=None, client=None, **kw):
        def both(message, percent):
            seen.append((message, percent))
            progress(message, percent)

        return real(bbox, wanted, d, progress=both, client=client, **kw)

    monkeypatch.setattr(fetchjobs.live_fetch, "ensure_scene", spy)
    login(c)
    wait(c, post(c, "/api/fetch", {"bbox": AREA}).get_json()["job"])
    assert seen and [p for _, p in seen] == sorted(p for _, p in seen) and seen[0][0] == "Searching Bhoonidhi"


def test_a_failing_fetch_reports_a_clean_error(env):
    c, api, _ = env
    api.features = []  # nothing on Bhoonidhi for this area
    login(c)
    done = wait(c, post(c, "/api/fetch", {"bbox": AREA}).get_json()["job"])
    assert done["state"] == "error" and "No online Resourcesat LISS-III scene" in done["error"] and done["scene"] is None


def test_an_unexpected_error_never_leaks_its_text(env, monkeypatch):
    c, _, _ = env

    def boom(*a, **k):
        raise ValueError("token abc123 appeared here")

    monkeypatch.setattr(fetchjobs.live_fetch, "ensure_scene", boom)
    login(c)
    done = wait(c, post(c, "/api/fetch", {"bbox": AREA}).get_json()["job"])
    assert done["state"] == "error" and "abc123" not in json.dumps(done)


def test_one_job_per_session(env, monkeypatch):
    c, api, data = env
    release = []

    def slow(job, session, bbox, wanted, d):
        while not release:
            time.sleep(0.01)
        job.state = "done"

    monkeypatch.setattr(fetchjobs, "_run", slow)
    login(c)
    assert post(c, "/api/fetch", {"bbox": AREA}).status_code == 202
    again = post(c, "/api/fetch", {"bbox": AREA})
    assert again.status_code == 409 and "already being fetched" in again.get_json()["error"]
    release.append(1)


def test_global_limit_across_sessions(env, monkeypatch):
    from app.server import flask_app

    c, api, data = env
    release = []

    def slow(job, session, bbox, wanted, d):
        while not release:
            time.sleep(0.01)
        job.state = "done"

    monkeypatch.setattr(fetchjobs, "_run", slow)
    monkeypatch.setattr(fetchjobs, "MAX_RUNNING", 1)
    login(c)
    assert post(c, "/api/fetch", {"bbox": AREA}).status_code == 202
    other = flask_app.test_client()
    login(other)
    busy = post(other, "/api/fetch", {"bbox": AREA})
    assert busy.status_code == 409 and "busy" in busy.get_json()["error"]
    release.append(1)


def test_a_session_cannot_read_another_sessions_job(env):
    from app.server import flask_app

    c, _, _ = env
    login(c)
    job = post(c, "/api/fetch", {"bbox": AREA}).get_json()["job"]
    wait(c, job)
    stranger = flask_app.test_client()
    login(stranger)
    assert stranger.get(f"/api/fetch/{job}").status_code == 404
    assert flask_app.test_client().get(f"/api/fetch/{job}").status_code == 404  # not even logged in


@pytest.mark.parametrize("body", [{}, {"bbox": [1, 2, 3]}, {"bbox": "x"}, {"bbox": [17.5, 77.5, 17.4, 77.4]},
                                  {"bbox": AREA, "date": "yesterday"}, {"bbox": [0, 0, 40, 40]}])
def test_bad_requests_are_refused_before_anything_is_fetched(env, body):
    c, api, _ = env
    login(c)
    assert post(c, "/api/fetch", body).status_code == 400
    assert api.count("/download") == 0 and api.count("/data/search") == 0


def test_fetch_requires_the_ajax_header(env):
    c, _, _ = env
    login(c)
    assert c.post("/api/fetch", json={"bbox": AREA}).status_code == 400


def test_scene_folders_lists_permanent_and_fetched_scenes(tmp_path):
    a, b = "R2323MAY2026000000000000000PSANSTUC00GTDF", "R2318MAY2026000000000000000PSANSTUC00GTDF"
    make_scene(tmp_path / a, a)
    make_scene(fetch_dir(tmp_path) / b, b)
    assert sorted(p.name for p in scene_folders(tmp_path)) == [b, a]
