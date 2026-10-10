"""Client for the Bhoonidhi API (https://bhoonidhi-api.nrsc.gov.in): authenticate, STAC search, download a scene.

Written from NRSC's published API specification (https://bhoonidhi.nrsc.gov.in/bhoonidhi-api/). It has been tested
against fakes of that specification, NOT against the real service (that needs a personal Bhoonidhi account); run
`python scripts/fetch_scene.py --dry-run ...` with your own credentials to check authentication and search first.

Facts taken from the specification, and what this code does about them:
  auth       POST /auth/token {userId, password, grant_type}; tokens last about 20 min; 20 auth calls per hour per IP
             -> the token is cached and refreshed, never requested per call
  search     POST /data/search (STAC), 3 requests per second per IP -> calls are spaced out
  download   GET /download?id=&collection=; 5 concurrent downloads; 412/429/503/504 mean "wait and retry"
             The specification does not say what the response body is. This code accepts a zip containing the band
             GeoTIFFs and BAND_META.txt and fails with a clear message for anything else.
  The credentials are read from the environment by the caller and are never logged or put in error messages.
"""
import re
import shutil
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import requests

BASE_URL = "https://bhoonidhi-api.nrsc.gov.in"
# Resourcesat-2 / 2A LISS-III standard products (the *_BOA collections are the priced surface-reflectance products)
LISS3_COLLECTIONS = ("ResourceSat-2_LISS3_L2", "ResourceSat-2A_LISS3_L2")
SCENE_FILES = ("BAND2.tif", "BAND3.tif", "BAND4.tif", "BAND5.tif", "BAND_META.txt")
RETRY_STATUSES = {412, 429, 503, 504}
AUTH_ERRORS = {
    400: "the request keys were rejected",
    401: "the user id or password was rejected",
    403: "the maximum number of sessions is already active for this account (log out elsewhere)",
    429: "too many authentication attempts (the limit is 20 per hour per IP address)",
}


class BhoonidhiError(RuntimeError):
    """Anything that went wrong talking to Bhoonidhi. The message is safe to show to a caller."""


@dataclass(frozen=True)
class Item:
    id: str
    collection: str
    date: date | None
    geometry: dict | None
    online: bool | None


def date_from_id(item_id: str) -> date | None:
    """Acquisition date from an ISRO product id such as R2318MAY2026078246009900060PSANSTUC00GTDF -> 2026-05-18."""
    m = re.search(r"(\d{2})([A-Z]{3})(\d{4})", item_id)
    if not m:
        return None
    try:
        return datetime.strptime("".join(m.groups()), "%d%b%Y").date()
    except ValueError:
        return None


def _rings(geometry: dict) -> list[list[tuple[float, float]]]:
    """Outer rings of a GeoJSON Polygon/MultiPolygon as (lon, lat) lists."""
    kind, coords = geometry.get("type"), geometry.get("coordinates")
    if kind == "Polygon":
        return [[(p[0], p[1]) for p in coords[0]]]
    if kind == "MultiPolygon":
        return [[(p[0], p[1]) for p in poly[0]] for poly in coords]
    return []


def _point_in_ring(x: float, y: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def footprint_contains_bbox(geometry: dict | None, bbox) -> bool:
    """True if all four corners of the (minx, miny, maxx, maxy) bbox lie inside the scene footprint polygon."""
    if not geometry:
        return True  # no footprint to judge by; the downloaded pixels are checked afterwards
    minx, miny, maxx, maxy = bbox
    corners = [(minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy)]
    return any(all(_point_in_ring(x, y, ring) for x, y in corners) for ring in _rings(geometry))


class BhoonidhiClient:
    def __init__(self, user: str, password: str, base_url: str = BASE_URL, session=None,
                 sleep=time.sleep, clock=time.monotonic, retry_waits=(5, 20, 60)):
        if not user or not password:
            raise BhoonidhiError("Bhoonidhi credentials are not set (BHOONIDHI_USER / BHOONIDHI_PASSWORD)")
        self._user, self._password = user, password
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self._sleep, self._clock, self.retry_waits = sleep, clock, retry_waits
        self._access = self._refresh = None
        self._expires_at = 0.0
        self._last_search = 0.0

    # ---- authentication ------------------------------------------------------------------------------------

    def _token_request(self, body: dict) -> None:
        try:
            r = self.session.request("POST", f"{self.base_url}/auth/token", json=body, timeout=30)
        except requests.RequestException as e:
            raise BhoonidhiError(f"could not reach Bhoonidhi to authenticate: {type(e).__name__}") from None
        if r.status_code != 200:
            reason = AUTH_ERRORS.get(r.status_code, f"HTTP {r.status_code}")
            raise BhoonidhiError(f"Bhoonidhi authentication failed: {reason}")
        data = r.json()
        self._access = data["access_token"]
        self._refresh = data.get("refresh_token")
        self._expires_at = self._clock() + float(data.get("expires_in", 1200)) - 60  # renew a minute early

    def _ensure_token(self) -> str:
        if self._access and self._clock() < self._expires_at:
            return self._access
        if self._refresh:
            try:
                self._token_request({"userId": self._user, "refresh_token": self._refresh,
                                     "grant_type": "refresh_token"})
                return self._access
            except BhoonidhiError:
                self._refresh = None  # fall back to a password login
        self._token_request({"userId": self._user, "password": self._password, "grant_type": "password"})
        return self._access

    def _call(self, method: str, path: str, **kwargs):
        """Authenticated request; one transparent re-login if the server says the session expired."""
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._ensure_token()}"}
            try:
                r = self.session.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
            except requests.RequestException as e:
                raise BhoonidhiError(f"request to Bhoonidhi failed: {type(e).__name__}") from None
            if r.status_code == 401 and attempt == 1:
                self._access, self._expires_at = None, 0.0
                continue
            return r

    # ---- search --------------------------------------------------------------------------------------------

    def search(self, bbox, start: date | None = None, end: date | None = None,
               collections=LISS3_COLLECTIONS, limit: int = 100) -> list[Item]:
        """Online LISS-III scenes intersecting bbox (minx, miny, maxx, maxy in lon/lat), one page of results."""
        body = {
            "collections": list(collections),
            "bbox": [str(v) for v in bbox],
            "limit": limit,
            "filter": {"op": "eq", "args": [{"property": "Online"}, "Y"]},  # offline products cannot be downloaded
            "filter-lang": "cql2-json",
        }
        if start or end:
            body["datetime"] = f"{(start or end).isoformat()}T00:00:00Z/{(end or start).isoformat()}T23:59:59Z"

        wait = 0.4 - (self._clock() - self._last_search)  # the limit is 3 searches per second
        if wait > 0:
            self._sleep(wait)
        self._last_search = self._clock()

        r = self._call("POST", "/data/search", json=body, timeout=60)
        if r.status_code != 200:
            raise BhoonidhiError(f"Bhoonidhi search failed: HTTP {r.status_code} {self._describe(r)}")
        items = []
        for f in r.json().get("features", []):
            props = f.get("properties") or {}
            when = None
            if props.get("datetime"):
                try:
                    when = datetime.fromisoformat(props["datetime"].replace("Z", "+00:00")).date()
                except ValueError:
                    pass
            online = props.get("Online")
            items.append(Item(id=f["id"], collection=f.get("collection", ""), date=when or date_from_id(f["id"]),
                              geometry=f.get("geometry"), online=None if online is None else online == "Y"))
        return items

    @staticmethod
    def _describe(r) -> str:
        try:
            d = r.json()
            return str(d.get("Description") or d.get("description") or "")[:200]
        except Exception:
            return ""

    # ---- download ------------------------------------------------------------------------------------------

    def download_scene(self, item: Item, data_dir: Path, min_free_gb: float = 2.0, progress=None) -> Path:
        """Download one scene and unpack only what the service needs into data_dir/<item.id>/. Returns that folder."""
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        final = data_dir / item.id
        if (final / "BAND_META.txt").exists():
            return final
        if shutil.disk_usage(data_dir).free < min_free_gb * 1e9:
            raise BhoonidhiError(f"less than {min_free_gb:g} GB free in {data_dir}; refusing to download a scene")

        lock = data_dir / f"{item.id}.lock"
        deadline = self._clock() + 900
        while True:
            try:
                lock.mkdir()
                break
            except FileExistsError:  # another request is downloading the same scene: wait for it
                if (final / "BAND_META.txt").exists():
                    return final
                if self._clock() > deadline:
                    raise BhoonidhiError("another download of this scene did not finish in time")
                self._sleep(5)
        part = data_dir / f"{item.id}.zip.part"
        staging = data_dir / f"{item.id}.staging"
        try:
            self._fetch_zip(item, part, progress)
            self._unpack(part, staging)
            if final.exists():
                shutil.rmtree(final)
            staging.rename(final)
            return final
        finally:
            part.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(lock, ignore_errors=True)

    def _fetch_zip(self, item: Item, dest: Path, progress=None) -> None:
        params = {"id": item.id, "collection": item.collection}
        for attempt, wait in enumerate((0, *self.retry_waits)):
            if wait:
                self._sleep(wait)
            r = self._call("GET", "/download", params=params, stream=True, timeout=(15, 300))
            if r.status_code in RETRY_STATUSES and attempt < len(self.retry_waits):
                continue
            if r.status_code == 404:
                raise BhoonidhiError("Bhoonidhi says this product is not available on online storage")
            if r.status_code != 200:
                raise BhoonidhiError(f"Bhoonidhi download failed: HTTP {r.status_code} {self._describe(r)}")
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            try:
                with open(dest, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
                        done += len(chunk)
                        if progress and total:
                            progress(done / total)
            except requests.RequestException as e:
                if attempt < len(self.retry_waits):  # "download interrupted, download again"; no HTTP range support
                    continue
                raise BhoonidhiError(f"download interrupted: {type(e).__name__}") from None
            return
        raise BhoonidhiError("Bhoonidhi kept refusing the download (concurrent limit or rate limit); try again later")

    @staticmethod
    def _unpack(archive: Path, staging: Path) -> None:
        if not zipfile.is_zipfile(archive):
            head = archive.read_bytes()[:40]
            raise BhoonidhiError(
                f"the download is not a zip archive (starts with {head!r}); the response format may differ from "
                "what this client expects"
            )
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        wanted = {n.lower(): n for n in SCENE_FILES}
        found = set()
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                name = Path(info.filename).name  # basename only: never trust paths inside an archive
                canonical = wanted.get(name.lower())
                if canonical and not info.is_dir():
                    with zf.open(info) as src, open(staging / canonical, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    found.add(canonical)
        missing = [n for n in SCENE_FILES if n not in found]
        if missing:
            raise BhoonidhiError(f"the archive is missing expected files: {', '.join(missing)}")


def load_env_file(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE lines from a .env file into os.environ without overriding existing variables."""
    import os

    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
