"""Browser login: credentials are used once, never kept, the code step works, and it is off by default."""
import pytest

from app import webauth
from app.bhoonidhi_client import BhoonidhiClient
from tests.live_helpers import FakeBhoonidhi

HEADERS = {"X-Requested-With": "XMLHttpRequest"}
CREDS = {"user": "student", "password": "correct-horse"}


@pytest.fixture
def web(monkeypatch):
    from app.server import flask_app

    api = FakeBhoonidhi()
    clock = {"now": 1000.0}
    monkeypatch.setenv("BROWSER_LOGIN", "1")
    monkeypatch.setattr(webauth, "make_client",
                        lambda u, p: BhoonidhiClient(u, p, session=api, sleep=lambda s: None, download_auth="portal"))
    monkeypatch.setattr(webauth, "_clock", lambda: clock["now"])
    webauth.store.__init__()  # a fresh store for each test
    c = flask_app.test_client()
    return c, api, clock


def post(c, path, body=None, headers=HEADERS):
    return c.post(path, json=body if body is not None else {}, headers=headers)


def test_login_is_off_unless_switched_on(monkeypatch):
    from app.server import flask_app

    monkeypatch.delenv("BROWSER_LOGIN", raising=False)
    c = flask_app.test_client()
    assert c.get("/api/session").get_json() == {"enabled": False, "logged_in": False, "otp_pending": False, "user": None}
    assert post(c, "/api/login", CREDS).status_code == 403


def test_login_without_a_code_gets_a_session_cookie_that_is_locked_down(web):
    c, api, _ = web
    r = post(c, "/api/login", CREDS)
    assert r.status_code == 200 and r.get_json() == {"status": "ready"}
    cookie = r.headers["Set-Cookie"]
    assert "sid=" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    s = c.get("/api/session").get_json()
    assert s["enabled"] and s["logged_in"] and not s["otp_pending"] and s["user"] == "student"


def test_the_password_is_not_kept_after_login_and_never_appears_in_any_reply(web):
    c, api, _ = web
    r = post(c, "/api/login", CREDS)
    session = next(iter(webauth.store._sessions.values()))
    assert session.client._password is None and "correct-horse" not in session.client._secrets
    assert "correct-horse" not in r.get_data(as_text=True) + str(r.headers)
    assert "correct-horse" not in c.get("/api/session").get_data(as_text=True)


def test_the_emailed_code_step(web):
    c, api, _ = web
    api.portal_otp = True
    assert post(c, "/api/login", CREDS).get_json() == {"status": "otp_required"}
    s = c.get("/api/session").get_json()
    assert s["otp_pending"] and not s["logged_in"]
    bad = post(c, "/api/otp", {"code": "000000"})
    assert bad.status_code == 401 and "rejected" in bad.get_json()["error"]
    short = post(c, "/api/otp", {"code": "12"})
    assert short.status_code == 401 and "6 digits" in short.get_json()["error"]
    ok = post(c, "/api/otp", {"code": "123456"})
    assert ok.get_json() == {"status": "ready"} and c.get("/api/session").get_json()["logged_in"]
    session = next(iter(webauth.store._sessions.values()))
    assert session.client._password is None  # dropped once the whole login finished


def test_a_wrong_password_is_refused_without_echoing_it(web):
    c, api, _ = web
    r = post(c, "/api/login", {"user": "student", "password": "nope-nope"})
    assert r.status_code == 401 and "nope-nope" not in r.get_data(as_text=True)
    assert webauth.store.count() == 0 and not c.get("/api/session").get_json()["logged_in"]


def test_code_endpoint_needs_a_pending_login(web):
    c, _, _ = web
    assert post(c, "/api/otp", {"code": "123456"}).status_code == 409


def test_too_many_wrong_codes_end_the_login(web):
    c, api, _ = web
    api.portal_otp = True
    post(c, "/api/login", CREDS)
    codes = [post(c, "/api/otp", {"code": "000000"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert not c.get("/api/session").get_json()["otp_pending"]


def test_state_changing_calls_need_the_ajax_header_and_json(web):
    c, _, _ = web
    assert c.post("/api/login", json=CREDS).status_code == 400  # no X-Requested-With: what a cross-site form would send
    assert c.post("/api/login", data="user=a&password=b", headers=HEADERS).status_code == 400  # not JSON


def test_attempts_are_rate_limited(web):
    c, _, _ = web
    statuses = [post(c, "/api/login", {"user": "x", "password": "y"}).status_code for _ in range(12)]
    assert 429 in statuses and statuses[:10] == [401] * 10


def test_logout_ends_the_session(web):
    c, _, _ = web
    post(c, "/api/login", CREDS)
    assert post(c, "/api/logout").get_json() == {"status": "logged_out"}
    assert not c.get("/api/session").get_json()["logged_in"] and webauth.store.count() == 0


def test_sessions_expire_when_idle_and_after_the_maximum_age(web):
    c, _, clock = web
    post(c, "/api/login", CREDS)
    clock["now"] += webauth.IDLE_SECONDS - 1
    assert c.get("/api/session").get_json()["logged_in"]  # activity renews the idle timer
    clock["now"] += webauth.IDLE_SECONDS + 1
    assert not c.get("/api/session").get_json()["logged_in"]
    post(c, "/api/login", CREDS)
    for _ in range(5):  # keep it busy, but not forever
        clock["now"] += webauth.MAX_SECONDS / 4
        c.get("/api/session")
    assert not c.get("/api/session").get_json()["logged_in"]


def test_a_new_login_replaces_the_old_session(web):
    c, _, _ = web
    post(c, "/api/login", CREDS)
    post(c, "/api/login", CREDS)
    assert webauth.store.count() == 1


def test_rate_limit_uses_the_real_client_address_only_behind_a_trusted_proxy(web, monkeypatch):
    c, api, clock = web
    wrong = {"user": "student", "password": "nope"}

    def attempts(n, forwarded):
        return [post(c, "/api/login", wrong, {**HEADERS, "X-Forwarded-For": forwarded}).status_code for _ in range(n)]

    # not trusted: a client-supplied header must not let anyone dodge the limit
    assert 429 in attempts(12, "9.9.9.9") and 429 in attempts(1, "8.8.8.8")
    webauth.store.__init__()
    monkeypatch.setenv("TRUST_PROXY", "1")
    assert 429 in attempts(12, "1.1.1.1")                 # one real visitor is limited...
    assert 429 not in attempts(1, "spoof, 2.2.2.2")       # ...another is not (the proxy's entry is the last one)


def test_a_login_still_waiting_for_its_code_expires_so_the_user_can_start_over(web):
    c, api, clock = web
    api.portal_otp = True
    assert post(c, "/api/login", CREDS).get_json() == {"status": "otp_required"}
    assert c.get("/api/session").get_json()["otp_pending"] is True          # e.g. the page was closed or the PC restarted
    clock["now"] += webauth.OTP_WAIT_SECONDS - 1
    assert c.get("/api/session").get_json()["otp_pending"] is True          # still within the time the code is good for
    clock["now"] += 2
    s = c.get("/api/session").get_json()
    assert s["otp_pending"] is False and s["logged_in"] is False             # dropped: the page asks for the credentials again
    assert post(c, "/api/login", CREDS).get_json() == {"status": "otp_required"}   # and a fresh login works


def test_starting_over_discards_the_pending_login_at_once(web):
    c, api, _ = web
    api.portal_otp = True
    post(c, "/api/login", CREDS)
    assert c.get("/api/session").get_json()["otp_pending"] is True
    assert post(c, "/api/logout").status_code == 200
    assert c.get("/api/session").get_json()["otp_pending"] is False


def test_a_logged_in_session_is_not_cut_short_by_the_code_wait_limit(web):
    c, api, clock = web
    assert post(c, "/api/login", CREDS).get_json() == {"status": "ready"}
    clock["now"] += webauth.OTP_WAIT_SECONDS + 60                           # the limit only applies to a login awaiting its code
    assert c.get("/api/session").get_json()["logged_in"] is True
