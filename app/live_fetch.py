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

from app.bhoonidhi_client import BhoonidhiClient, BhoonidhiError, Item, footprint_contains_bbox
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
                                      base_url=os.environ.get("BHOONIDHI_API_URL", "https://bhoonidhi-api.nrsc.gov.in"))
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


def ensure_scene(bbox, wanted: date | None, data_dir: Path, progress=None, client: BhoonidhiClient | None = None,
                 min_valid: float = 0.95) -> Scene:
    """Make sure a local scene covers bbox (on `wanted`, if given) and return it.

    `progress(message, percent)` is called along the way. Raises BhoonidhiError with a message safe to show.
    """
    note = progress or (lambda message, percent: None)
    client = client or get_client()
    note("Searching Bhoonidhi", 10)
    options = candidates(bbox, wanted, client)
    if not options:
        raise BhoonidhiError(
            "No online Resourcesat LISS-III scene on Bhoonidhi covers this bbox"
            + (f" on {wanted.isoformat()}" if wanted else f" in the last {LOOKBACK_DAYS} days"))

    for n, item in enumerate(options[:MAX_DOWNLOADS], start=1):
        note(f"Downloading {item.id} ({n} of {min(len(options), MAX_DOWNLOADS)}); this can take minutes", 20)
        folder = client.download_scene(item, data_dir, progress=lambda f: note("Downloading scene", 20 + int(50 * f)))
        scene = Scene(folder)
        note("Checking coverage", 75)
        if scene.valid_fraction(bbox) >= min_valid:
            return scene
        LOGGER.info("scene %s does not cover the bbox with valid pixels; trying the next one", item.id)
    raise BhoonidhiError(
        "The scenes found on Bhoonidhi do not fully cover this bbox with valid pixels (tilted scene edges)")
