"""Browser login to Bhoonidhi, for a locally run or privately hosted instance.

Why our server has to see the credentials: the browser cannot log in to Bhoonidhi directly (the portal does not allow
cross-site requests), so the password travels browser -> this server -> Bhoonidhi, in a POST body over HTTPS. It is used
for that one login only: it is never stored on disk, never logged, and the client object drops it as soon as the login
(including the emailed one-time code) has finished. Only the resulting session tokens stay, in memory, under a random
cookie id; they expire after 30 minutes idle (2 hours at most) or at logout.

OFF unless BROWSER_LOGIN=1. Do not enable it where the traffic passes through a third party that terminates TLS (a free
ngrok tunnel does), and never on an instance whose users should not be able to spend each other's quota.
"""
import os
import secrets
import threading
import time
from dataclasses import dataclass, field

from flask import Blueprint, jsonify, request

from app.bhoonidhi_client import BhoonidhiClient, BhoonidhiError

IDLE_SECONDS = 30 * 60
MAX_SECONDS = 2 * 60 * 60
LOGIN_ATTEMPTS = (10, 60)  # at most 10 login/code attempts per client address per minute
COOKIE = "sid"

bp = Blueprint("webauth", __name__)
_clock = time.monotonic  # replaced in tests


def make_client(user: str, password: str) -> BhoonidhiClient:
    """One client per web session: downloads go through the portal route (the only one that works)."""
    return BhoonidhiClient(user, password, download_auth="portal",
                           base_url=os.environ.get("BHOONIDHI_API_URL", "https://bhoonidhi-api.nrsc.gov.in"))


@dataclass
class WebSession:
    sid: str
    client: BhoonidhiClient
    user: str
    state: str  # "otp" (waiting for the emailed code) or "ready"
    created: float
    touched: float
    otp_attempts: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)  # one Bhoonidhi operation at a time per session


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, WebSession] = {}
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}

    def create(self, client, user: str, state: str) -> WebSession:
        now = _clock()
        s = WebSession(secrets.token_urlsafe(32), client, user, state, now, now)
        with self._lock:
            self._sweep(now)
            self._sessions[s.sid] = s
        return s

    def get(self, sid: str | None) -> WebSession | None:
        if not sid:
            return None
        now = _clock()
        with self._lock:
            s = self._sessions.get(sid)
            if s and (now - s.touched > IDLE_SECONDS or now - s.created > MAX_SECONDS):
                self._drop_locked(sid)
                return None
            if s:
                s.touched = now
            return s

    def drop(self, sid: str | None) -> None:
        with self._lock:
            self._drop_locked(sid)

    def _drop_locked(self, sid) -> None:
        s = self._sessions.pop(sid, None)
        if s:
            s.client.forget_password()  # a no-op if already forgotten

    def _sweep(self, now: float) -> None:
        for sid, s in list(self._sessions.items()):
            if now - s.touched > IDLE_SECONDS or now - s.created > MAX_SECONDS:
                self._drop_locked(sid)

    def allow_attempt(self, who: str) -> bool:
        """Sliding-window limit on login and code attempts per client address."""
        now, (limit, window) = _clock(), LOGIN_ATTEMPTS
        with self._lock:
            recent = [t for t in self._attempts.get(who, []) if now - t < window]
            if len(recent) >= limit:
                self._attempts[who] = recent
                return False
            recent.append(now)
            self._attempts[who] = recent
            return True

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


store = SessionStore()


def enabled() -> bool:
    return os.environ.get("BROWSER_LOGIN") == "1"


def current_session() -> WebSession | None:
    return store.get(request.cookies.get(COOKIE))


def _fail(message: str, status: int):
    return jsonify({"error": message}), status


def _guard():
    """Common checks for the state-changing endpoints; returns an error response or None."""
    if not enabled():
        return _fail("Logging in to Bhoonidhi is disabled on this instance.", 403)
    # A custom header cannot be sent by another site's form or image tag; with SameSite cookies this blocks CSRF.
    if request.headers.get("X-Requested-With") != "XMLHttpRequest" or not request.is_json:
        return _fail("Bad request.", 400)
    if not store.allow_attempt(request.remote_addr or "?"):
        return _fail("Too many attempts; wait a minute.", 429)
    return None


def _set_cookie(resp, sid: str):
    secure = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
    resp.set_cookie(COOKIE, sid, httponly=True, samesite="Strict", secure=secure, max_age=MAX_SECONDS)
    return resp


@bp.route("/api/session")
def api_session():
    s = current_session() if enabled() else None
    return jsonify({"enabled": enabled(), "logged_in": bool(s and s.state == "ready"),
                    "otp_pending": bool(s and s.state == "otp"), "user": s.user if s else None})


@bp.route("/api/login", methods=["POST"])
def api_login():
    if (bad := _guard()) is not None:
        return bad
    body = request.get_json(silent=True) or {}
    user, password = body.get("user"), body.get("password")
    if not (isinstance(user, str) and isinstance(password, str) and 0 < len(user) <= 200 and 0 < len(password) <= 200):
        return _fail("Enter your Bhoonidhi user id and password.", 400)
    store.drop(request.cookies.get(COOKIE))  # a new login replaces any earlier session
    client = make_client(user, password)
    try:
        client._ensure_token()  # the official API login: needed to search the catalogue (no code is asked for)
        ready = client.portal_login_begin()  # the portal's own login: needed to download (may email a code)
    except BhoonidhiError as e:
        client.forget_password()
        return _fail(str(e), 401)
    except Exception:  # never let a surprise echo anything about the request
        client.forget_password()
        return _fail("Could not log in to Bhoonidhi.", 502)
    if ready:
        client.forget_password()
    session = store.create(client, user, "ready" if ready else "otp")
    return _set_cookie(jsonify({"status": "ready" if ready else "otp_required"}), session.sid)


@bp.route("/api/otp", methods=["POST"])
def api_otp():
    if (bad := _guard()) is not None:
        return bad
    s = current_session()
    if not s or s.state != "otp":
        return _fail("No login is waiting for a code. Log in first.", 409)
    s.otp_attempts += 1
    if s.otp_attempts > 5:
        store.drop(s.sid)
        return _fail("Too many wrong codes; log in again.", 429)
    try:
        s.client.portal_login_finish((request.get_json(silent=True) or {}).get("code", ""))
    except BhoonidhiError as e:
        return _fail(str(e), 401)
    except Exception:
        return _fail("Could not verify the code.", 502)
    s.state = "ready"
    s.client.forget_password()
    return jsonify({"status": "ready"})


@bp.route("/api/logout", methods=["POST"])
def api_logout():
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return _fail("Bad request.", 400)
    store.drop(request.cookies.get(COOKIE))
    resp = jsonify({"status": "logged_out"})
    resp.delete_cookie(COOKIE)
    return resp
