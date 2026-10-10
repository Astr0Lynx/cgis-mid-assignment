"""Test doubles for the Bhoonidhi API (built from NRSC's published specification) and a synthetic LISS-III scene."""
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.transform import from_origin
from rasterio.warp import transform

CENTER = (77.50, 17.50)  # lon, lat; UTM zone 43N
SMALL_BBOX_LATLON = "17.49,77.49,17.51,77.51"  # miny,minx,maxy,maxx (OGC order) well inside the synthetic scene


def make_scene(folder: Path, scene_id: str, date_text: str = "23-MAY-2026", center=CENTER) -> Path:
    """A tiny 300x300 pixel (7.2 km) LISS-III scene in UTM 43N with constant, non-zero band values."""
    folder.mkdir(parents=True, exist_ok=True)
    x, y = (v[0] for v in transform("EPSG:4326", "EPSG:32643", [center[0]], [center[1]]))
    profile = dict(driver="GTiff", height=300, width=300, count=1, dtype="uint16", crs="EPSG:32643",
                   transform=from_origin(x - 3600, y + 3600, 24, 24))
    values = {2: 400, 3: 300, 4: 700, 5: 200}  # green, red, nir, swir DN
    for band, dn in values.items():
        with rasterio.open(folder / f"BAND{band}.tif", "w", **profile) as ds:
            ds.write(np.full((300, 300), dn, dtype="uint16"), 1)
    lines = [f"OTSProductID= {scene_id}", f"DateOfPass= {date_text}", "ZoneNo= 43", "SunElevationAtCenter= 65.0"]
    lines += [f"B{b}_Lmin= 0.0\nB{b}_Lmax= {lmax}" for b, lmax in {2: 52.0, 3: 47.0, 4: 31.5, 5: 7.5}.items()]
    (folder / "BAND_META.txt").write_text("\n".join(lines) + "\n")
    return folder


def make_zip(tmp: Path, scene_id: str, date_text: str = "23-MAY-2026", center=CENTER, nested=True) -> bytes:
    """Zip of a synthetic scene, with files inside a sub-folder (like a real product may be) and extra files."""
    src = make_scene(tmp / f"src_{scene_id}", scene_id, date_text, center)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(src.iterdir()):
            zf.write(f, f"{scene_id}/{f.name}" if nested else f.name)
        zf.writestr(f"{scene_id}/preview.jpg", b"not really a jpeg")
        zf.writestr("../evil.txt", b"zip-slip attempt")  # must never be written outside the scene folder
    return buf.getvalue()


def footprint(center=CENTER, half=0.05) -> dict:
    lon, lat = center
    ring = [[lon - half, lat - half], [lon + half, lat - half], [lon + half, lat + half], [lon - half, lat + half],
            [lon - half, lat - half]]
    return {"type": "Polygon", "coordinates": [ring]}


def feature(scene_id: str, collection="ResourceSat-2_LISS3_L2", online="Y", geometry=None, **props) -> dict:
    return {"id": scene_id, "collection": collection, "geometry": geometry or footprint(),
            "properties": {"Online": online, **props}, "links": []}


class FakeResponse:
    def __init__(self, status=200, body=None, chunks=None, headers=None, fail_after=None, reason=""):
        self.status_code = status
        self._body = body if body is not None else {}
        self._chunks = chunks or []
        self.headers = headers or {}
        self._fail_after = fail_after
        self.reason = reason
        self.closed = False

    def json(self):
        if isinstance(self._body, (bytes, str)):
            raise ValueError("not json")
        return self._body

    @property
    def text(self):
        return self._body.decode() if isinstance(self._body, bytes) else self._body if isinstance(self._body, str) else json.dumps(self._body)

    def close(self):
        self.closed = True

    def iter_content(self, chunk_size=1):
        for n, chunk in enumerate(self._chunks):
            if self._fail_after is not None and n >= self._fail_after:
                raise requests.exceptions.ChunkedEncodingError("connection broken")
            yield chunk


class FakeBhoonidhi:
    """Stands in for requests.Session: records every call and answers like the documented API."""

    def __init__(self, features=None, archives=None, expires_in=1200, password="correct-horse"):
        self.features = features or []
        self.archives = archives or {}  # item id -> zip bytes
        self.expires_in = expires_in
        self.password = password
        self.calls = []  # (method, path, kwargs)
        self.download_statuses = {}  # item id -> list of statuses to return before success
        self.download_response = None  # optional callable(kwargs) -> FakeResponse, overrides /download entirely
        self.valid_tokens = set()
        self._n = 0

    def request(self, method, url, **kw):
        path = url.split("nrsc.gov.in", 1)[-1]
        self.calls.append((method, path, kw))
        if path == "/auth/token":
            return self._auth(kw["json"])
        auth = (kw.get("headers") or {}).get("Authorization", "")
        if auth.removeprefix("Bearer ") not in self.valid_tokens:
            return FakeResponse(401, {"ErrorCode": "401", "Description": "session expired"})
        if path == "/data/search":
            return FakeResponse(200, {"type": "FeatureCollection", "features": self.features,
                                      "context": {"limit": 100, "returned": len(self.features)}})
        if path == "/download":
            if self.download_response is not None:  # a test script for an unusual server behaviour
                return self.download_response(kw)
            item_id = kw["params"]["id"]
            queue = self.download_statuses.get(item_id, [])
            if queue:
                return FakeResponse(queue.pop(0), {"ErrorCode": "x", "Description": "busy"})
            if item_id not in self.archives:
                return FakeResponse(404, {"Description": "not on online storage"})
            data = self.archives[item_id]
            return FakeResponse(200, chunks=[data[i:i + 4096] for i in range(0, len(data), 4096)],
                                headers={"Content-Length": str(len(data))})
        return FakeResponse(404)

    def _auth(self, body):
        if body.get("grant_type") == "password":
            if body.get("password") != self.password:
                return FakeResponse(401, {"Description": "Incorrect password"})
        elif body.get("grant_type") != "refresh_token":
            return FakeResponse(400)
        self._n += 1
        token = f"token-{self._n}"
        self.valid_tokens.add(token)
        return FakeResponse(200, {"userId": body["userId"], "access_token": token, "token_type": "Bearer",
                                  "expires_in": self.expires_in, "refresh_token": f"refresh-{self._n}"})

    def count(self, path):
        return sum(1 for _, p, _ in self.calls if p == path)
