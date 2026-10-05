# Bhoonidhi WPS: on-demand NDVI over ISRO's EO archive

CyberGIS mid-sem assignment. An **OGC Web Processing Service (WPS 1.0.0, PyWPS)** that computes NDVI for any
bounding box from Resourcesat-2 LISS-III scenes published on [Bhoonidhi](https://bhoonidhi.nrsc.gov.in), instead of
making the user download a whole scene and compute it by hand.

## 1. The gap

### What Bhoonidhi offers today
- A web portal (Browse & Order) for humans.
- A [Bhoonidhi API](https://bhoonidhi.nrsc.gov.in/bhoonidhi-api/): JWT authentication, a **STAC** search
  (`/data/collections`, `/data/search`) and a `/download` endpoint that returns the full product archive.
- Data is raw: a LISS-III product is a zip of four band GeoTIFFs (`BAND2..5.tif`, ~110 MB each) plus `BAND_META.txt`.

### What does not exist
Nothing in the API specification computes anything from the data. Probing the portal host for OGC endpoints
(unauthenticated `GET`, 2026-10-05):

| Path on `bhoonidhi.nrsc.gov.in` | Result |
|---|---|
| `/wms`, `/wcs`, `/csw`, `/wps`, `/processes`, `/ogcapi`, `/conformance` | 404 |
| `/bhoonidhi-api/wps`, `/bhoonidhi-api/processes`, `/bhoonidhi-api/conformance` | 404 |

The API documentation lists STAC search and download only. This is absence of evidence, not a proof; I found no
Bhoonidhi WPS / OGC API - Processes service in the documentation or in searches.

### What a developer has to do today for "NDVI over my district"
1. Get an account and authenticate; search STAC; pick a scene.
2. Download the whole multi-band scene (~450 MB), unzip it.
3. Clip the Red and NIR bands to the area, handle the background and scaling, compute (NIR-Red)/(NIR+Red).
4. Repeat per date and per area.

Everything happens on the user's machine, so no web app, QGIS plugin or pipeline can simply *ask* for NDVI.

### Gaps I considered and rejected
| Idea | Verdict |
|---|---|
| CSW over scene metadata | Weak: discovery already exists as a STAC API (plus an unofficial public STAC mirror). A CSW would be a protocol shim. |
| WCS/WFS for raw imagery | Partly true, but serving whole licensed scenes needs heavy hosting and raises redistribution questions. |
| SOS / SensorThings | Bhoonidhi is a scene archive; no station/time-series observations were found. |
| **WPS for derived products** | **Chosen.** No processing interface exists. |

### Prior art, stated honestly
NRSC publishes a precomputed **AWiFS NDVI time-composite** product (100 m, 10x10 degree tiles, fixed 15-day windows,
whole-tile download). This service is different: on-demand, any bounding box and date, 24 m LISS-III resolution,
and a standard OGC interface that clients can discover and call.

## 2. The service

Endpoint (local): `http://localhost:5000/wps`

| Operation | Support |
|---|---|
| `GetCapabilities` | yes |
| `DescribeProcess` | yes |
| `Execute` | yes: KVP (GET) and XML (POST), synchronous and asynchronous (`storeExecuteResponse=true&status=true`) |

### Process `ndvi`
| | Identifier | Type | Notes |
|---|---|---|---|
| Input | `bbox` | BoundingBox, `EPSG:4326` | OGC axis order for EPSG:4326: `miny,minx,maxy,maxx` (lat,lon). Required. |
| Input | `date` | Literal (string) | `YYYY-MM-DD`. Optional; default is the newest scene that fully covers the bbox. |
| Output | `ndvi` | Complex, `image/tiff; subtype=geotiff` | Float32 Cloud Optimized GeoTIFF, NaN = no data, in the scene's UTM CRS. Returned by reference. |
| Output | `stats` | Complex, `application/json` | scene id, date, bbox, shape, min/mean/max, valid fraction, radiometry mode. |

Errors are returned as proper OGC `ExceptionReport`s (unknown process, invalid or inverted bbox, bbox over the size
limit, bad date, no scene covering the request).

### Examples
```bash
# capabilities and process description
curl "http://localhost:5000/wps?service=WPS&request=GetCapabilities"
curl "http://localhost:5000/wps?service=WPS&version=1.0.0&request=DescribeProcess&identifier=ndvi"

# synchronous Execute (central Hyderabad, ~10 x 15 km)
curl "http://localhost:5000/wps?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326"

# asynchronous Execute: returns ProcessAccepted + statusLocation; poll that URL for ProcessSucceeded
curl "http://localhost:5000/wps?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326&storeExecuteResponse=true&status=true"
```
Python client: `python scripts/owslib_check.py [wps_url]` drives all operations with OWSLib. In QGIS use the
Processing Toolbox WPS client (or any WPS 1.0.0 client) with the same URL.

## 3. Design and implementation choices
- **PyWPS 4.8 on Flask + gunicorn, in Docker.** PyWPS asynchronous execution uses `multiprocessing` and does not work
  on Windows, so the service is run in Docker (Linux).
- **Pipeline** (`app/`): find scenes whose *actual pixels* cover the bbox -> reproject the bbox into the scene's UTM zone
  -> read only the needed rows of the Red and NIR bands (windowed read) -> DN to radiance using the per-band
  `Lmin/Lmax` in `BAND_META.txt` -> NDVI -> write a COG.
- **Scene selection uses real pixel coverage.** Scenes are tilted, so a bounding rectangle overstates coverage; the
  18 May scene's rectangle contains central Hyderabad but its slanted edge cuts through it (45% valid), so it is rejected.
- **COG for outputs and for the cache, not for the sources.** The source tifs are uncompressed and row-striped, so a
  bbox clip already reads little; converting them would gain nothing.
- **Two data modes, one code path.** Full scenes (local, from `data/`) or a pre-clipped cache (public). The cache has
  the same folder layout (`BAND2..5.tif` + `BAND_META.txt`) with small compressed COG bands of the *original* DN values;
  NDVI from the cache is bit-identical to NDVI from the full scene. `scripts/build_cache.py` builds it from scenes you
  downloaded yourself; the public image contains only this cache (~38 MB for both scenes) and no credentials.
- **Axis order.** PyWPS 4.8 delivers XML-POST bounding boxes as lon,lat but KVP as lat,lon. `app/pywps_compat.py`
  makes both follow the OGC convention; regression tests cover it.
- **Strict MIME type** `image/tiff; subtype=geotiff` on the output, and `/outputs/` is served statically so async
  status documents and results are reachable.
- **Bounded cost:** requests above 16 Mpixel (~96 x 96 km at 24 m, `MAX_PIXELS`) are rejected.

## 4. Run it
```bash
# one-off: scenes you downloaded from Bhoonidhi go in data/ (git-ignored), one folder per scene
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python scripts/build_cache.py            # data/ -> cache/ (Hyderabad area)

docker build -t bhoonidhi-wps .                        # image bakes in cache/
docker run -p 5000:5000 bhoonidhi-wps                  # public-style, cache only
# or with full scenes:  docker run -p 5000:5000 -v "$PWD/data:/srv/data:ro" bhoonidhi-wps
# behind a tunnel, tell the service its public URL so status/output links are correct:
#   docker run -p 5000:5000 -e PUBLIC_BASE_URL=https://<your-tunnel> bhoonidhi-wps
```
Tests: `.venv/Scripts/python -m pytest` (the real-data tests are skipped if `data/` is empty).
`scripts/inspect_scene.py <scene folder or zip>` prints tif header metadata.

## 5. Known limitations
- **Radiance, not reflectance (v1).** Indices use at-sensor radiance. Without a solar-irradiance correction the NDVI is
  biased low (red solar irradiance exceeds NIR); the Hyderabad 23 May clip has a median NDVI of about -0.05. Treat values
  as relative. TOA reflectance is the next planned change (`later.md`). The priced BOA surface-reflectance product is the
  proper alternative.
- **Public instance serves only the cached area and scenes** (Hyderabad, 18 and 23 May 2026); anything else returns a
  clean "no scene covers this request" exception. Live fetching from Bhoonidhi is deliberately not exposed publicly,
  because it would act on a personal Bhoonidhi account and its download quota.
- No cloud masking (cloud percentage is empty in the scene metadata); single-scene results, no mosaicking or
  time-series; the two scenes use different UTM zones so combining them would need reprojection.
- Only NDVI so far; further indices are planned after the radiometry fix.
- Not independently validated against a reference NDVI product.

## 6. Data and attribution
Contains data from Resourcesat-2 LISS-III, **ISRO-IRS**, distributed through Bhoonidhi (NRSC/ISRO). Scenes are not
redistributed in this repository (`data/` and `cache/` are git-ignored). Check the Bhoonidhi end-user licence before
publishing derived products beyond this coursework.

## 7. Repository layout
```
app/            service code (radiometry, indices, scene clipping, WPS process, Flask server)
scripts/        inspect_scene.py, build_cache.py, owslib_check.py
tests/          unit tests and WPS protocol tests
Dockerfile, pywps.cfg, requirements.txt
later.md        deferred work and priorities
```
