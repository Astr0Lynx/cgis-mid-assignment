"""Fetch a scene from Bhoonidhi for a logged-in browser session, as a background job with progress.

This is deliberately NOT part of the WPS: the OGC service stays stateless and never sees a token. The page asks this
endpoint to bring a scene onto the server (using the user's own session), polls its progress, and once the scene is on
disk it runs the ordinary WPS Execute. Fetched scenes live in data/_fetched and expire (see app.cleanup.purge_fetched).
"""
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request

from app import live_fetch
from app.bhoonidhi_client import BhoonidhiError
from app.webauth import _fail, current_session, enabled

LOGGER = logging.getLogger("bhoonidhi.fetchjobs")
MAX_RUNNING = 2  # fetches at once across all sessions (each is a ~140 MB download)
KEEP_FINISHED_S = 3600

SCENE_ID_RE = re.compile(r"[A-Za-z0-9_]{8,80}")
SEARCH_COOLDOWN_S = 2  # per session: a listing makes several requests to Bhoonidhi

bp = Blueprint("fetchjobs", __name__)
_last_search: dict[str, float] = {}


@dataclass
class Job:
    id: str
    sid: str
    state: str = "running"  # running | done | error
    message: str = "Starting"
    percent: int = 0
    scene: dict | None = None
    error: str | None = None
    scene_id: str | None = None  # a particular scene the user chose (otherwise: the newest suitable one)
    started: float = field(default_factory=time.monotonic)


class JobStore:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def running(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.state == "running"]

    def start(self, session, bbox, wanted, data_dir: Path, run=None, scene_id: str | None = None) -> Job:
        """Create a job and run it in a thread. Raises RuntimeError with a user-facing reason if it may not start."""
        with self.lock:
            now = time.monotonic()
            for jid in [i for i, j in self.jobs.items() if j.state != "running" and now - j.started > KEEP_FINISHED_S]:
                del self.jobs[jid]
            if any(j.sid == session.sid for j in self.running()):
                raise RuntimeError("A scene is already being fetched for this session.")
            if len(self.running()) >= MAX_RUNNING:
                raise RuntimeError("The server is busy fetching other scenes; try again in a minute.")
            job = Job(uuid.uuid4().hex, session.sid, scene_id=scene_id)
            self.jobs[job.id] = job
        threading.Thread(target=run or _run, args=(job, session, bbox, wanted, data_dir), name="fetch-" + job.id[:6],
                         daemon=True).start()
        return job


jobs = JobStore()


def _run(job: Job, session, bbox, wanted, data_dir: Path) -> None:
    from app.scene import fetch_dir

    def progress(message: str, percent: int) -> None:
        job.message, job.percent = message, max(job.percent, min(99, int(percent)))

    try:
        with session.lock:  # one Bhoonidhi operation at a time per session
            scene = live_fetch.ensure_scene(bbox, wanted, fetch_dir(data_dir), progress=progress, client=session.client,
                                            scene_id=job.scene_id)
        job.scene = {"id": scene.scene_id, "date": scene.date.date().isoformat()}
        job.message, job.percent, job.state = "Scene ready", 100, "done"
    except BhoonidhiError as e:
        LOGGER.warning("fetch job %s (scene_id=%s, wanted=%s) failed: %s", job.id[:6], job.scene_id, wanted, e)
        job.error, job.state = str(e), "error"
    except Exception as e:  # never show an unexpected error's text (it could echo request details)
        LOGGER.error("fetch job failed: %s", type(e).__name__)
        job.error, job.state = "Fetching the scene failed unexpectedly.", "error"


def _view(job: Job) -> dict:
    return {"job": job.id, "state": job.state, "message": job.message, "percent": job.percent,
            "scene": job.scene, "error": job.error}


@bp.route("/api/fetch", methods=["POST"])
def api_fetch():
    from app.processes.ndvi import MAX_PIXELS, _approx_pixels
    from app.scene import DATA_DIR

    if not enabled():
        return _fail("Fetching from Bhoonidhi is disabled on this instance.", 403)
    if request.headers.get("X-Requested-With") != "XMLHttpRequest" or not request.is_json:
        return _fail("Bad request.", 400)
    session = current_session()
    if not session or session.state != "ready":
        return _fail("Log in to Bhoonidhi first.", 401)
    body = request.get_json(silent=True) or {}
    try:
        miny, minx, maxy, maxx = (float(v) for v in body["bbox"])  # OGC order: latitude first
        wanted = date.fromisoformat(body["date"]) if body.get("date") else None
    except (KeyError, TypeError, ValueError):
        return _fail("Send bbox as [minlat, minlon, maxlat, maxlon] and an optional date YYYY-MM-DD.", 400)
    if not (-90 <= miny < maxy <= 90 and -180 <= minx < maxx <= 180):
        return _fail("Invalid area.", 400)
    bbox = (minx, miny, maxx, maxy)
    if _approx_pixels(bbox) > MAX_PIXELS:
        return _fail("That area is larger than this service handles in one request.", 400)
    scene_id = body.get("scene_id")
    if scene_id is not None and not (isinstance(scene_id, str) and SCENE_ID_RE.fullmatch(scene_id)):
        return _fail("Invalid scene id.", 400)
    try:
        job = jobs.start(session, bbox, wanted, DATA_DIR, scene_id=scene_id)
    except RuntimeError as e:
        return _fail(str(e), 409)
    return jsonify(_view(job)), 202


@bp.route("/api/scenes", methods=["POST"])
def api_scenes():
    """List the Bhoonidhi scenes that cover the area, so the user can choose a date instead of taking the newest."""
    from app.processes.ndvi import MAX_PIXELS, _approx_pixels

    if not enabled():
        return _fail("Fetching from Bhoonidhi is disabled on this instance.", 403)
    if request.headers.get("X-Requested-With") != "XMLHttpRequest" or not request.is_json:
        return _fail("Bad request.", 400)
    session = current_session()
    if not session or session.state != "ready":
        return _fail("Log in to Bhoonidhi first.", 401)
    body = request.get_json(silent=True) or {}
    try:
        miny, minx, maxy, maxx = (float(v) for v in body["bbox"])
        today = date.today()
        end = date.fromisoformat(body["end"]) if body.get("end") else today
        start = date.fromisoformat(body["start"]) if body.get("start") else end - timedelta(days=180)
    except (KeyError, TypeError, ValueError):
        return _fail("Send bbox as [minlat, minlon, maxlat, maxlon] and optionally start and end as YYYY-MM-DD.", 400)
    if start > end:
        return _fail("The start date is after the end date.", 400)
    if end > today + timedelta(days=1):
        return _fail("The end date is in the future.", 400)
    if start < live_fetch.EARLIEST:
        return _fail(f"There is no Resourcesat-2 data before {live_fetch.EARLIEST.isoformat()}.", 400)
    if not (-90 <= miny < maxy <= 90 and -180 <= minx < maxx <= 180):
        return _fail("Invalid area.", 400)
    bbox = (minx, miny, maxx, maxy)
    if _approx_pixels(bbox) > MAX_PIXELS:
        return _fail("That area is larger than this service handles in one request.", 400)
    now = time.monotonic()
    since = now - _last_search.get(session.sid, -1e9)
    if since < SEARCH_COOLDOWN_S:
        return _fail(f"This service spaces searches out: try again in {max(1, round(SEARCH_COOLDOWN_S - since))} second(s). "
                     "(This is not Bhoonidhi's limit.)", 429)
    _last_search[session.sid] = now
    try:
        with session.lock:
            listing = live_fetch.list_scenes(session.client, bbox, start, end)
    except BhoonidhiError as e:
        LOGGER.warning("scene listing refused for %s..%s: %s", start, end, e)
        return _fail(str(e), 502)
    except Exception as e:
        LOGGER.error("scene listing failed: %s", type(e).__name__)
        return _fail("Searching Bhoonidhi failed unexpectedly.", 502)
    return jsonify({"scenes": listing["scenes"], "more": listing["more"], "start": start.isoformat(), "end": end.isoformat()})


@bp.route("/api/fetch/<job_id>")
def api_fetch_status(job_id):
    session = current_session()
    job = jobs.jobs.get(job_id)
    if not session or not job or job.sid != session.sid:
        return _fail("Unknown job.", 404)
    return jsonify(_view(job))
