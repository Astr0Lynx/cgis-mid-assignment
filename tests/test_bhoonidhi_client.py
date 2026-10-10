"""The Bhoonidhi client against a fake of the documented API (no network, no credentials)."""
from datetime import date

import pytest

from app.bhoonidhi_client import (BhoonidhiClient, BhoonidhiError, Item, date_from_id, footprint_contains_bbox,
                                  load_env_file)
from tests.live_helpers import FakeBhoonidhi, feature, footprint, make_zip

SCENE = "R2323MAY2026078317010000060PSANSTUC00GTDF"
BBOX = (77.49, 17.49, 77.51, 17.51)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def make_client(api, clock=None, **kw):
    clock = clock or Clock()
    return BhoonidhiClient("student", "correct-horse", base_url="https://bhoonidhi-api.nrsc.gov.in", session=api,
                           sleep=clock.sleep, clock=clock, **kw), clock


# ---- authentication -----------------------------------------------------------------------------------------

def test_login_sends_documented_body_and_reuses_the_token():
    api = FakeBhoonidhi([feature(SCENE)])
    client, _ = make_client(api)
    client.search(BBOX)
    client.search(BBOX)
    assert api.count("/auth/token") == 1  # authentication is limited to 20 per hour: one login serves many calls
    assert api.calls[0][2]["json"] == {"userId": "student", "password": "correct-horse", "grant_type": "password"}
    assert api.calls[1][2]["headers"]["Authorization"] == "Bearer token-1"


def test_expired_token_is_renewed_with_the_refresh_token_not_the_password():
    api = FakeBhoonidhi([feature(SCENE)], expires_in=1200)
    client, clock = make_client(api)
    client.search(BBOX)
    clock.now += 1200  # past expiry
    client.search(BBOX)
    bodies = [kw["json"] for _, path, kw in api.calls if path == "/auth/token"]
    assert [b["grant_type"] for b in bodies] == ["password", "refresh_token"]
    assert "password" not in bodies[1] and bodies[1]["refresh_token"] == "refresh-1"


def test_session_expired_mid_call_triggers_one_relogin_and_succeeds():
    api = FakeBhoonidhi([feature(SCENE)])
    client, _ = make_client(api)
    client.search(BBOX)
    api.valid_tokens.clear()  # server drops the session although our clock says the token is still good
    assert len(client.search(BBOX)) == 1
    assert api.count("/auth/token") == 2


@pytest.mark.parametrize("status,phrase", [(401, "rejected"), (403, "maximum number of sessions"), (429, "too many")])
def test_login_failures_give_readable_messages_without_leaking_the_password(status, phrase):
    class Refusing(FakeBhoonidhi):
        def _auth(self, body):
            from tests.live_helpers import FakeResponse
            return FakeResponse(status, {"Description": "x"})

    client, _ = make_client(Refusing())
    with pytest.raises(BhoonidhiError) as e:
        client.search(BBOX)
    assert phrase in str(e.value) and "correct-horse" not in str(e.value)


def test_missing_credentials_are_refused_up_front():
    with pytest.raises(BhoonidhiError, match="not set"):
        BhoonidhiClient("", "")


# ---- search -------------------------------------------------------------------------------------------------

def test_search_body_follows_the_specification():
    api = FakeBhoonidhi([feature(SCENE)])
    client, _ = make_client(api)
    client.search(BBOX, date(2026, 5, 23), date(2026, 5, 23))
    body = next(kw["json"] for _, path, kw in api.calls if path == "/data/search")
    assert body["collections"] == ["ResourceSat-2_LISS3_L2", "ResourceSat-2A_LISS3_L2"]
    assert body["bbox"] == ["77.49", "17.49", "77.51", "17.51"]  # strings, as in the specification's example
    assert body["datetime"] == "2026-05-23T00:00:00Z/2026-05-23T23:59:59Z"
    assert body["filter"] == {"op": "eq", "args": [{"property": "Online"}, "Y"]} and body["filter-lang"] == "cql2-json"
    assert 0 < body["limit"] <= 500


def test_search_parses_items_and_falls_back_to_the_date_in_the_id():
    api = FakeBhoonidhi([feature(SCENE), feature("R2318MAY2026078246009900060PSANSTUC00GTDF", online="N",
                                                 datetime="2026-05-18T05:00:00Z")])
    client, _ = make_client(api)
    first, second = client.search(BBOX)
    assert first == Item(SCENE, "ResourceSat-2_LISS3_L2", date(2026, 5, 23), footprint(), True)
    assert second.date == date(2026, 5, 18) and second.online is False


def test_search_failure_is_reported_not_swallowed():
    class Down(FakeBhoonidhi):
        def request(self, method, url, **kw):
            from tests.live_helpers import FakeResponse
            return super().request(method, url, **kw) if "auth" in url else FakeResponse(503, {"Description": "STAC down"})

    client, _ = make_client(Down())
    with pytest.raises(BhoonidhiError, match="503.*STAC down"):
        client.search(BBOX)


def test_searches_are_spaced_to_respect_three_per_second():
    api = FakeBhoonidhi([feature(SCENE)])
    client, clock = make_client(api)
    client.search(BBOX)
    before = clock.now
    client.search(BBOX)
    assert clock.now - before >= 0.4 - 1e-9


def test_date_from_id_and_footprint_check():
    assert date_from_id(SCENE) == date(2026, 5, 23)
    assert date_from_id("R2318MAY2026078246009900060PSANSTUC00GTDF") == date(2026, 5, 18)
    assert date_from_id("nonsense") is None
    assert footprint_contains_bbox(footprint(), BBOX)
    assert not footprint_contains_bbox(footprint(half=0.005), BBOX)  # footprint smaller than the bbox
    assert not footprint_contains_bbox(footprint(center=(78.5, 17.5)), BBOX)  # different place
    assert footprint_contains_bbox(None, BBOX)  # nothing to judge by: pixels are checked after download


# ---- download -----------------------------------------------------------------------------------------------

def item(scene_id=SCENE):
    return Item(scene_id, "ResourceSat-2_LISS3_L2", date_from_id(scene_id), footprint(), True)


def test_download_unpacks_only_the_needed_files_into_a_scene_folder(tmp_path):
    api = FakeBhoonidhi(archives={SCENE: make_zip(tmp_path, SCENE)})
    client, _ = make_client(api)
    folder = client.download_scene(item(), tmp_path / "data")
    assert folder == tmp_path / "data" / SCENE
    assert sorted(p.name for p in folder.iterdir()) == ["BAND2.tif", "BAND3.tif", "BAND4.tif", "BAND5.tif", "BAND_META.txt"]
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "data" / "evil.txt").exists()
    assert sorted(p.name for p in (tmp_path / "data").iterdir()) == [SCENE]  # no .part/.lock/.staging left behind
    download = next(kw for _, path, kw in api.calls if path == "/download")
    assert download["params"] == {"id": SCENE, "collection": "ResourceSat-2_LISS3_L2"}


def test_download_is_skipped_when_the_scene_is_already_there(tmp_path):
    api = FakeBhoonidhi(archives={SCENE: make_zip(tmp_path, SCENE)})
    client, _ = make_client(api)
    client.download_scene(item(), tmp_path / "data")
    calls = len(api.calls)
    client.download_scene(item(), tmp_path / "data")
    assert len(api.calls) == calls


def test_busy_server_is_retried_after_waiting(tmp_path):
    api = FakeBhoonidhi(archives={SCENE: make_zip(tmp_path, SCENE)})
    api.download_statuses[SCENE] = [412, 504]  # concurrent limit, then interrupted
    client, clock = make_client(api)
    start = clock.now
    client.download_scene(item(), tmp_path / "data")
    assert api.count("/download") == 3
    assert clock.now - start >= 5 + 20  # waited between attempts instead of hammering


def test_persistent_refusal_gives_up_with_a_clear_message(tmp_path):
    api = FakeBhoonidhi(archives={SCENE: make_zip(tmp_path, SCENE)})
    api.download_statuses[SCENE] = [412] * 10
    client, _ = make_client(api)
    with pytest.raises(BhoonidhiError, match="412"):
        client.download_scene(item(), tmp_path / "data")
    assert not list((tmp_path / "data").iterdir())  # failed attempts clean up after themselves


def test_offline_product_gives_a_clear_message(tmp_path):
    client, _ = make_client(FakeBhoonidhi())  # no archive registered -> 404
    with pytest.raises(BhoonidhiError, match="not available on online storage"):
        client.download_scene(item(), tmp_path / "data")


def test_a_response_that_is_not_a_zip_is_reported_with_its_first_bytes(tmp_path):
    api = FakeBhoonidhi(archives={SCENE: b"<html>login page</html>" * 10})
    client, _ = make_client(api)
    with pytest.raises(BhoonidhiError, match="not a zip archive.*html"):
        client.download_scene(item(), tmp_path / "data")


def test_archive_missing_a_band_is_rejected(tmp_path):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("BAND2.tif", b"x")
        zf.writestr("BAND_META.txt", b"x")
    client, _ = make_client(FakeBhoonidhi(archives={SCENE: buf.getvalue()}))
    with pytest.raises(BhoonidhiError, match="missing expected files: BAND3.tif, BAND4.tif, BAND5.tif"):
        client.download_scene(item(), tmp_path / "data")


def test_download_refuses_when_disk_is_nearly_full(tmp_path):
    client, _ = make_client(FakeBhoonidhi())
    with pytest.raises(BhoonidhiError, match="GB free"):
        client.download_scene(item(), tmp_path / "data", min_free_gb=10 ** 9)


def test_interrupted_transfer_restarts_from_scratch(tmp_path):
    from tests.live_helpers import FakeResponse

    zipped = make_zip(tmp_path, SCENE)

    class Flaky(FakeBhoonidhi):
        first = True

        def request(self, method, url, **kw):
            r = super().request(method, url, **kw)
            if url.endswith("/download") and Flaky.first and r.status_code == 200:
                Flaky.first = False
                return FakeResponse(200, chunks=r._chunks, fail_after=2)  # breaks mid-stream
            return r

    client, _ = make_client(Flaky(archives={SCENE: zipped}))
    folder = client.download_scene(item(), tmp_path / "data")  # the portal has no HTTP range support: start over
    assert (folder / "BAND_META.txt").exists()


# ---- .env -----------------------------------------------------------------------------------------------------

def test_env_file_is_loaded_without_overriding_and_ignores_comments(tmp_path, monkeypatch):
    import os

    f = tmp_path / ".env"
    f.write_text('# comment\nBHOONIDHI_USER="alice"\nBHOONIDHI_PASSWORD = s3cret\n\nEMPTY\nOTHER=1\n')
    monkeypatch.delenv("BHOONIDHI_USER", raising=False)
    monkeypatch.delenv("BHOONIDHI_PASSWORD", raising=False)
    monkeypatch.setenv("OTHER", "keep")
    load_env_file(f)
    assert os.environ["BHOONIDHI_USER"] == "alice" and os.environ["BHOONIDHI_PASSWORD"] == "s3cret"
    assert os.environ["OTHER"] == "keep"
    load_env_file(tmp_path / "missing.env")  # a missing file is fine


# ---- diagnosing a refused download -------------------------------------------------------------------------------

def test_error_messages_include_what_the_server_said_and_never_a_secret(tmp_path):
    from tests.live_helpers import FakeResponse

    api = FakeBhoonidhi(archives={SCENE: make_zip(tmp_path, SCENE)})
    client, _ = make_client(api)
    client.search(BBOX)  # logs in, so the client now holds a token
    token = client._access
    api.download_response = lambda kw: FakeResponse(
        401, body=f"<html>Unauthorized: token {token} rejected for user</html>", reason="UNAUTHORIZED")
    with pytest.raises(BhoonidhiError) as e:
        client.download_scene(item(), tmp_path / "data")
    msg = str(e.value)
    assert "401" in msg and "UNAUTHORIZED" in msg and "Unauthorized: token" in msg
    assert token not in msg and "correct-horse" not in msg  # the server echoed the token; it must be scrubbed


def test_diagnose_download_shows_a_redirect_without_following_it_or_leaking(tmp_path):
    from tests.live_helpers import FakeResponse

    seen = {}

    def answer(kw):
        seen.update(kw)
        return FakeResponse(302, headers={"Location": "https://files.example.org/store/x.zip?Signature=SECRET123",
                                          "Server": "nginx"}, chunks=[b""], reason="FOUND")

    api = FakeBhoonidhi()
    api.download_response = answer
    client, _ = make_client(api)
    report = client.diagnose_download(item())
    assert report["status"] == 302 and report["location"] == "https://files.example.org/store/x.zip"
    assert "SECRET123" not in str(report)  # the query string (a possible signature) is dropped
    assert seen["allow_redirects"] is False and seen["stream"] is True


def test_diagnose_download_recognises_a_zip_and_a_refusal(tmp_path):
    from tests.live_helpers import FakeResponse

    api = FakeBhoonidhi(archives={SCENE: make_zip(tmp_path, SCENE)})
    client, _ = make_client(api)
    ok = client.diagnose_download(item())
    assert ok["status"] == 200 and ok["starts_with"] == "PK (a zip archive)" and ok["content_length"]
    api.download_response = lambda kw: FakeResponse(401, headers={"WWW-Authenticate": "Bearer realm=x"}, chunks=[b"nope"])
    refused = client.diagnose_download(item())
    assert refused["status"] == 401 and refused["www_authenticate"] == "Bearer realm=x" and refused["starts_with"] == "nope"


# ---- how the token is presented to /download -------------------------------------------------------------------------

def test_probe_finds_the_style_the_server_accepts_and_stops_there(tmp_path):
    api = FakeBhoonidhi(archives={SCENE: make_zip(tmp_path, SCENE)})
    api.download_accepts = {"query"}  # like the portal: the token travels as ?token=
    client, _ = make_client(api)
    results = client.probe_download_auth(item())
    assert [(r["style"], r["status"]) for r in results] == [("bearer", 401), ("query", 200)]  # stopped at the first 200
    assert results[0]["starts_with"].startswith('{"ErrorCode"') or "Invalid session" in str(results[0])
    assert results[1]["starts_with"] == "PK (a zip archive)"
    assert "token-1" not in str(results)  # nothing secret in the report


def test_probe_reports_every_refusal_when_nothing_works(tmp_path):
    api = FakeBhoonidhi(archives={SCENE: make_zip(tmp_path, SCENE)})
    api.download_accepts = set()
    client, _ = make_client(api)
    results = client.probe_download_auth(item())
    assert [r["style"] for r in results] == ["bearer", "query", "token-header"]
    assert all(r["status"] == 401 for r in results)


@pytest.mark.parametrize("style", ["query", "token-header"])
def test_download_works_when_configured_for_the_style_the_server_accepts(tmp_path, style):
    api = FakeBhoonidhi(archives={SCENE: make_zip(tmp_path, SCENE)})
    api.download_accepts = {style}
    client = BhoonidhiClient("student", "correct-horse", session=api, sleep=lambda s: None, download_auth=style)
    folder = client.download_scene(item(), tmp_path / "data")
    assert (folder / "BAND_META.txt").exists()


def test_download_with_the_wrong_style_fails_with_the_servers_explanation(tmp_path):
    api = FakeBhoonidhi(archives={SCENE: make_zip(tmp_path, SCENE)})
    api.download_accepts = {"query"}
    client, _ = make_client(api)  # default bearer
    with pytest.raises(BhoonidhiError, match="401.*Invalid session detected"):
        client.download_scene(item(), tmp_path / "data")


def test_unknown_download_style_is_rejected_up_front():
    with pytest.raises(BhoonidhiError, match="unknown download auth style"):
        BhoonidhiClient("u", "p", download_auth="cookie")
