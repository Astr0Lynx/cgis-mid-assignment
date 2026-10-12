"""Live mode: when no local scene covers a request, fetch one from Bhoonidhi, then carry on as usual.

OFF unless BHOONIDHI_LIVE=1 and both BHOONIDHI_USER and BHOONIDHI_PASSWORD are set. It acts on a personal Bhoonidhi
account (login, download quota), so it is meant for a locally run service, not a public one. The public instance
runs cache-only and never has credentials.
"""
import logging
import os
import threading
from datetime import date, timedelta
from pathlib import Path

from app.bhoonidhi_client import BhoonidhiClient, BhoonidhiError, Item, footprint_contains_bbox, image_corners_geometry
from app.scene import Scene

LOGGER = logging.getLogger("bhoonidhi.live")
LOOKBACK_DAYS = int(os.environ.get("BHOONIDHI_LOOKBACK_DAYS", 60))  # when no date is given: LISS-III revisits every ~24 days
MAX_DOWNLOADS = int(os.environ.get("BHOONIDHI_MAX_DOWNLOADS", 2))  # per request, to protect the account's quota

_client = None
_client_lock = threading.Lock()


def enabled() -> bool:
    return (os.environ.get("BHOONIDHI_LIVE") == "1"
            and bool(os.environ.get("BHOONIDHI_USER")) and bool(os.environ.get("BHOONIDHI_PASSWORD")))


def get_client() -> BhoonidhiClient:
    """One client per process so its token is reused (authentication is limited to 20 calls per hour)."""
    global _client
    with _client_lock:
        if _client is None:
            _client = BhoonidhiClient(os.environ["BHOONIDHI_USER"], os.environ["BHOONIDHI_PASSWORD"],
                                      base_url=os.environ.get("BHOONIDHI_API_URL", "https://bhoonidhi-api.nrsc.gov.in"),
                                      download_auth=os.environ.get("BHOONIDHI_DOWNLOAD_AUTH", "bearer"))
        return _client


def candidates(bbox, wanted: date | None, client: BhoonidhiClient) -> list[Item]:
    """Online scenes whose footprint contains the bbox, newest first (exact day if `wanted` is given)."""
    if wanted:
        start = end = wanted
    else:
        end = date.today()
        start = end - timedelta(days=LOOKBACK_DAYS)
    found = client.search(bbox, start, end)
    usable = [i for i in found if i.online is not False and footprint_contains_bbox(i.geometry, bbox)]
    return sorted(usable, key=lambda i: i.date or date.min, reverse=True)


MAX_PRECHECKS = 6  # candidates whose true footprint is read before any download


def precheck(client, items: list[Item], bbox, note) -> list[Item]:
    """Drop candidates whose TRUE image footprint (from the portal's small metadata file) misses the area.

    Bhoonidhi's search lists a scene as covering when the rectangle around its tilted image does, which wastes a ~140 MB
    download. A candidate whose metadata cannot be read is kept (the pixel check after download still protects us).
    Only the portal route can do this; other clients return the list unchanged.
    """
    if getattr(client, "download_auth", None) != "portal":
        return items
    note("Checking scene footprints", 15)
    kept = []
    for n, item in enumerate(items):
        if n >= MAX_PRECHECKS:
            kept.append(item)
            continue
        try:
            record = client.find_portal_record(item, bbox)
            meta = client.fetch_portal_meta(record) if record else None
        except BhoonidhiError:
            meta = None
        geometry = image_corners_geometry(meta) if meta else None
        if geometry is None or footprint_contains_bbox(geometry, bbox):
            kept.append(item)
        else:
            LOGGER.info("skipping %s: its image does not cover the area", item.id)
    return kept


MAX_LIST_DAYS = 400
LIST_LIMIT = 40


def list_scenes(client, bbox, days: int, max_checks: int = 10) -> list[dict]:
    """The online scenes whose footprint contains the bbox in the last `days` days, newest first, for the user to choose from.

    For the newest `max_checks` the scene's TRUE image footprint is read from its public metadata file (portal clients only),
    so each entry says whether the tilted image really covers the area: covers is True, False, or None when unknown.
    """
    days = max(1, min(int(days), MAX_LIST_DAYS))
    end = date.today()
    found = client.search(bbox, end - timedelta(days=days), end)
    usable = [i for i in found if i.online is not False and footprint_contains_bbox(i.geometry, bbox)]
    usable.sort(key=lambda i: i.date or date.min, reverse=True)
    out = []
    for n, item in enumerate(usable[:LIST_LIMIT]):
        covers = None
        if n < max_checks and getattr(client, "download_auth", None) == "portal":
            try:
                record = client.find_portal_record(item, bbox)
                meta = client.fetch_portal_meta(record) if record else None
                geometry = image_corners_geometry(meta) if meta else None
                covers = footprint_contains_bbox(geometry, bbox) if geometry else None
            except BhoonidhiError:
                covers = None
        out.append({"id": item.id, "date": item.date.isoformat() if item.date else None,
                    "collection": item.collection, "covers": covers})
    return out


def ensure_scene(bbox, wanted: date | None, data_dir: Path, progress=None, client: BhoonidhiClient | None = None,
                 min_valid: float = 0.95, scene_id: str | None = None) -> Scene:
    """Make sure a local scene covers bbox (on `wanted`, if given) and return it.

    `progress(message, percent)` is called along the way. Raises BhoonidhiError with a message safe to show.
    """
    note = progress or (lambda message, percent: None)
    client = client or get_client()
    note("Searching Bhoonidhi", 10)
    options = candidates(bbox, wanted, client)
    if scene_id:  # the user picked one particular scene from the list
        options = [i for i in options if i.id == scene_id]
    options = precheck(client, options, bbox, note)
    if not options:
        if scene_id:
            raise BhoonidhiError("That scene does not cover this area (or is no longer online); pick another one")
        raise BhoonidhiError(
            "No online Resourcesat LISS-III scene on Bhoonidhi covers this bbox"
            + (f" on {wanted.isoformat()}" if wanted else f" in the last {LOOKBACK_DAYS} days"))

    for n, item in enumerate(options[:MAX_DOWNLOADS], start=1):
        note(f"Downloading {item.id} ({n} of {min(len(options), MAX_DOWNLOADS)}); this can take minutes", 20)
        folder = client.download_scene(item, data_dir, bbox=bbox,
                                      progress=lambda f: note("Downloading scene", 20 + int(50 * f)))
        scene = Scene(folder)
        note("Checking coverage", 75)
        if scene.valid_fraction(bbox) >= min_valid:
            return scene
        LOGGER.info("scene %s does not cover the bbox with valid pixels; trying the next one", item.id)
    raise BhoonidhiError(
        "The scenes found on Bhoonidhi do not fully cover this bbox with valid pixels (tilted scene edges)")
