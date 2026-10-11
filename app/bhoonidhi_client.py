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
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit
from pathlib import Path

import requests

BASE_URL = "https://bhoonidhi-api.nrsc.gov.in"
# Resourcesat-2 / 2A LISS-III standard products (the *_BOA collections are the priced surface-reflectance products)
LISS3_COLLECTIONS = ("ResourceSat-2_LISS3_L2", "ResourceSat-2A_LISS3_L2")
SCENE_FILES = ("BAND2.tif", "BAND3.tif", "BAND4.tif", "BAND5.tif", "BAND_META.txt")
RETRY_STATUSES = {412, 429, 503, 504}
# How the access token is presented to /download. The specification says "bearer"; the server answered a Bearer
# request with 401 "Invalid session detected", and the unofficial portal client sends the token as ?token=...
API_AUTH_STYLES = ("bearer", "query", "token-header")  # ways to call the official API's /download
# "portal": download from the portal's own data path instead of the official API. The path layout, the satellite
# code and the ?token= convention come from the unofficial client geovicco-dev/bhoonidhi-downloader, NOT from NRSC's
# documentation, so they are inferred and may need adjusting (see portal_candidates).
DOWNLOAD_AUTH_STYLES = API_AUTH_STYLES + ("portal",)
PORTAL_URL = "https://bhoonidhi.nrsc.gov.in"
PORTAL_SATELLITE = {"ResourceSat-2_LISS3_L2": "RS2", "ResourceSat-2A_LISS3_L2": "R2A"}  # the portal's SATELLITE codes
PORTAL_LISS3_SENSOR = "3"  # the portal's short code for the LISS-III sensor in its data path
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
                 sleep=time.sleep, clock=time.monotonic, retry_waits=(5, 20, 60), download_auth: str = "bearer",
                 portal_url: str = PORTAL_URL):
        if download_auth not in DOWNLOAD_AUTH_STYLES:
            raise BhoonidhiError(f"unknown download auth style {download_auth!r}; use one of {DOWNLOAD_AUTH_STYLES}")
        self.download_auth = download_auth
        self.portal_url = portal_url.rstrip("/")
        if not user or not password:
            raise BhoonidhiError("Bhoonidhi credentials are not set (BHOONIDHI_USER / BHOONIDHI_PASSWORD)")
        self._user, self._password = user, password
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self._sleep, self._clock, self.retry_waits = sleep, clock, retry_waits
        self._access = self._refresh = None
        self._secrets = {password}  # every secret ever held, so none can leak through an old message
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
        self._secrets.update(t for t in (self._access, self._refresh) if t)
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

    def _scrub(self, text: str) -> str:
        """Remove anything secret from text that came back from the server before it is shown or logged."""
        for secret in self._secrets:
            if secret:
                text = text.replace(secret, "***")
        return text

    def _describe(self, r) -> str:
        """The server's own explanation of an error: the JSON Description if there is one, else the start of the body."""
        try:
            d = r.json()
            # the API's error object is {ErrorCode, Description, Action}; the Action is usually the useful part
            parts = [d.get("Description") or d.get("description"), d.get("Action") or d.get("action")]
            text = " - ".join(str(p) for p in parts if p) or str(d.get("ErrorCode") or "")
        except Exception:
            try:
                text = " ".join((r.text or "").split())
            except Exception:
                text = ""
        reason = getattr(r, "reason", "") or ""
        return self._scrub(f"[{reason}] {text}".strip() if text else f"[{reason}]" if reason else "")[:240]

    def _download_request(self, params: dict, style: str, **kwargs):
        """GET /download presenting the token in the given style (see DOWNLOAD_AUTH_STYLES)."""
        token = self._ensure_token()
        headers, query = {}, dict(params)
        if style == "bearer":
            headers["Authorization"] = f"Bearer {token}"
        elif style == "query":
            query["token"] = token
        elif style == "token-header":
            headers["token"] = token
        else:
            raise BhoonidhiError(f"unknown download auth style {style!r}")
        try:
            return self.session.request("GET", f"{self.base_url}/download", headers=headers, params=query, **kwargs)
        except requests.RequestException as e:
            raise BhoonidhiError(f"request to Bhoonidhi failed: {type(e).__name__}") from None

    def portal_candidates(self, item: Item) -> list[str]:
        """Candidate portal download addresses (without the token) for a scene.

        Layout from the unofficial client: <portal>/bhoonidhi/data/<satellite>/<sensor>/<year>/<month>/<file>.zip, where
        the year and month come from the product's directory on the portal. That is the acquisition month in the
        examples I know of, but it might be the month the product was generated, so the previous month is also tried.
        """
        satellite = PORTAL_SATELLITE.get(item.collection)
        if not satellite or not item.date:
            return []
        first = item.date.replace(day=1)
        months = [first, first - timedelta(days=1)]
        return [f"{self.portal_url}/bhoonidhi/data/{satellite}/{PORTAL_LISS3_SENSOR}/{m.year}/{m.month:02d}/{item.id}.zip"
                for m in months]

    def _portal_get(self, url: str, item: Item, **kwargs):
        token = self._ensure_token()
        try:
            return self.session.request("GET", url, params={"token": token, "product_id": item.id}, **kwargs)
        except requests.RequestException as e:
            raise BhoonidhiError(f"request to Bhoonidhi failed: {type(e).__name__}") from None

    def _portal_download(self, item: Item):
        """Open the download on the portal: the first candidate address that answers 200, else the last answer."""
        last = None
        for url in self.portal_candidates(item):
            r = self._portal_get(url, item, stream=True, timeout=(15, 300))
            if r.status_code == 200:
                return r
            last = r
        if last is None:
            raise BhoonidhiError(f"no portal download address is known for collection {item.collection!r}")
        return last

    def probe_portal_download(self, item: Item) -> list[dict]:
        """Ask the portal's data path for the scene, once per candidate address, reading only the first bytes.

        Uses the access token from the official API login. Stops at the first HTTP 200. Reports the address path (never
        the token) and what came back.
        """
        results = []
        for url in self.portal_candidates(item):
            r = self._portal_get(url, item, stream=True, timeout=(15, 60), allow_redirects=False)
            first = b""
            try:
                first = next(iter(r.iter_content(chunk_size=200)), b"")
            finally:
                close = getattr(r, "close", None)
                if close:
                    close()
            location = r.headers.get("Location")
            results.append({
                "path": urlsplit(url).path,
                "status": r.status_code,
                "content_type": r.headers.get("Content-Type"),
                "content_length": r.headers.get("Content-Length"),
                "location": (lambda u: f"{u.scheme}://{u.netloc}{u.path}")(urlsplit(location)) if location else None,
                "starts_with": "PK (a zip archive)" if first.startswith(b"PK")
                else self._scrub(" ".join(first[:160].decode("latin-1").split())),
            })
            if r.status_code == 200:
                break
        return results

    def probe_download_auth(self, item: Item) -> list[dict]:
        """Try each way of presenting the token to /download; stop at the first the server accepts (HTTP 200).

        Each attempt reads only the first bytes of the reply and closes it, so nothing is downloaded. Returns one
        non-secret summary per attempt.
        """
        results = []
        for style in API_AUTH_STYLES:
            r = self._download_request({"id": item.id, "collection": item.collection}, style, stream=True,
                                       timeout=(15, 60), allow_redirects=False)
            first = b""
            try:
                first = next(iter(r.iter_content(chunk_size=200)), b"")
            finally:
                close = getattr(r, "close", None)
                if close:
                    close()
            results.append({
                "style": style,
                "status": r.status_code,
                "content_type": r.headers.get("Content-Type"),
                "content_length": r.headers.get("Content-Length"),
                "starts_with": "PK (a zip archive)" if first.startswith(b"PK")
                else self._scrub(" ".join(first[:160].decode("latin-1").split())),
            })
            if r.status_code == 200:
                break
        return results

    def diagnose_download(self, item: Item) -> dict:
        """One request to /download that shows how the server answers, without downloading the file or leaking a secret.

        Redirects are NOT followed, so a redirect to another host (where `requests` would drop the Authorization
        header) is visible. Returns only non-secret facts: status, a few headers, the redirect target without its
        query string, and the first bytes of the body.
        """
        from urllib.parse import urlsplit

        r = self._call("GET", "/download", params={"id": item.id, "collection": item.collection}, stream=True,
                       timeout=(15, 60), allow_redirects=False)
        first = b""
        try:
            first = next(iter(r.iter_content(chunk_size=300)), b"")
        finally:
            close = getattr(r, "close", None)
            if close:
                close()
        h = r.headers
        location = h.get("Location")
        if location:
            u = urlsplit(location)
            location = f"{u.scheme}://{u.netloc}{u.path}"  # drop the query string: it may carry a signature
        return {
            "status": r.status_code,
            "reason": getattr(r, "reason", ""),
            "content_type": h.get("Content-Type"),
            "content_length": h.get("Content-Length"),
            "content_disposition": h.get("Content-Disposition"),
            "www_authenticate": h.get("WWW-Authenticate"),
            "location": location,
            "server": h.get("Server"),
            "starts_with": self._scrub(first[:120].decode("latin-1")) if not first.startswith(b"PK") else "PK (a zip archive)",
        }

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
            if self.download_auth == "bearer":
                r = self._call("GET", "/download", params=params, stream=True, timeout=(15, 300))
            elif self.download_auth == "portal":
                r = self._portal_download(item)
            else:
                r = self._download_request(params, self.download_auth, stream=True, timeout=(15, 300))
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
