# Bhoonidhi WPS: on-demand NDVI over ISRO's EO archive

CyberGIS mid-sem assignment. An **OGC Web Processing Service (WPS 1.0.0, PyWPS)** that computes NDVI for a
bounding box (anywhere the available scenes cover) from Resourcesat-2 LISS-III scenes published on [Bhoonidhi](https://bhoonidhi.nrsc.gov.in), instead of
making the user download a whole scene and compute it by hand. It offers NDVI, NDWI, MNDWI and NDBI.

**Live endpoint:** `https://triangle-overpass-flirt.ngrok-free.dev/wps`
([landing page](https://triangle-overpass-flirt.ngrok-free.dev/), [GetCapabilities](https://triangle-overpass-flirt.ngrok-free.dev/wps?service=WPS&request=GetCapabilities)).
It runs on the author's machine behind an ngrok tunnel, so it is up only while that machine is running and the
address changes if the tunnel is restarted; see [docs/DEMO.md](docs/DEMO.md). The public instance serves the cached
Hyderabad area only (section 5). Everything below can also be run locally (section 4).

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
whole-tile download). This service is different: on-demand for a caller-chosen bounding box and scene date (limited to the scenes loaded
into the service), 24 m LISS-III resolution, and a standard OGC interface that clients can discover and call.

## 2. The service

Endpoint (local): `http://localhost:5000/wps`

| Operation | Support |
|---|---|
| `GetCapabilities` | yes |
| `DescribeProcess` | yes |
| `Execute` | yes: KVP (GET) and XML (POST), synchronous and asynchronous (`storeExecuteResponse=true&status=true`) |

### Processes
| Identifier | What it does |
|---|---|
| `ndvi` | NDVI for a bbox (the headline process). Output `ndvi`. |
| `spectral-index` | Same pipeline with a required `index` input: `ndvi`, `ndwi`, `mndwi` or `ndbi`. Output `index`. |

Both take the same other inputs and return the same two outputs (a GeoTIFF and a stats JSON). Indices (LISS-III bands:
B2 green, B3 red, B4 NIR, B5 SWIR), each a normalised difference `(a-b)/(a+b)`:

| `index` | Formula | Highlights |
|---|---|---|
| `ndvi` | (nir-red)/(nir+red) | vegetation vigour |
| `ndwi` | (green-nir)/(green+nir) | open water (McFeeters) |
| `mndwi` | (green-swir)/(green+swir) | water vs built-up (Xu) |
| `ndbi` | (swir-nir)/(swir+nir) | built-up land |

Not offered: **EVI** (needs a Blue band, which LISS-III lacks) and **SAVI** (its soil constant 0.5 is defined for
reflectance and is meaningless on radiance; it waits for TOA reflectance).

### Process `ndvi` (inputs and outputs; `spectral-index` adds an `index` input and names its output `index`)
| | Identifier | Type | Notes |
|---|---|---|---|
| Input | `bbox` | BoundingBox, `EPSG:4326` | OGC axis order for EPSG:4326: `miny,minx,maxy,maxx` (lat,lon). Required. |
| Input | `date` | Literal (string) | `YYYY-MM-DD`. Optional; default is the newest scene that fully covers the bbox. |
| Input | `radiometry` | Literal, `radiance` \| `toa` | Optional, default `radiance`. `toa` = top-of-atmosphere reflectance; it is implemented but **refused with an explanatory error until a cited per-band solar-irradiance (ESUN) table is configured** (see section 5). |
| Output | `ndvi` | Complex, `image/tiff; subtype=geotiff` | Float32 Cloud Optimized GeoTIFF, NaN = no data, in the scene's UTM CRS. Returned by reference. |
| Output | `stats` | Complex, `application/json` | scene id, date, bbox, shape, min/mean/max, valid fraction, radiometry mode. |

Errors are returned as proper OGC `ExceptionReport`s (unknown process, invalid or inverted bbox, bbox over the size
limit, bad date, no scene covering the request).

### Response options
| Option (KVP / XML) | Effect |
|---|---|
| default | XML `ExecuteResponse` with a link to the GeoTIFF and the stats JSON inline |
| `RawDataOutput=ndvi` / `<wps:RawDataOutput>` | Returns the GeoTIFF itself (`image/tiff; subtype=geotiff`), no XML |
| `RawDataOutput=stats` | Returns the statistics JSON itself (`application/json`) |
| `ResponseDocument=ndvi@asReference=true` | Choose which outputs appear, and whether as a link |
| `lineage=true` / `lineage="true"` | The response also echoes the inputs received and the outputs requested |
| `storeExecuteResponse=true&status=true` | Asynchronous: immediate `ProcessAccepted` + status URL to poll |
| unknown output name | OGC `ExceptionReport` (`InvalidParameterValue`, HTTP 400) listing the valid names |

### Examples
```bash
# capabilities and process description
curl "http://localhost:5000/wps?service=WPS&request=GetCapabilities"
curl "http://localhost:5000/wps?service=WPS&version=1.0.0&request=DescribeProcess&identifier=ndvi"

# synchronous Execute (central Hyderabad, ~10 x 15 km)
curl "http://localhost:5000/wps?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326"

# raw GeoTIFF straight to a file, and raw statistics
curl -o ndvi.tif "http://localhost:5000/wps?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326&RawDataOutput=ndvi"
curl "http://localhost:5000/wps?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326&RawDataOutput=stats"

# asynchronous Execute: returns ProcessAccepted + statusLocation; poll that URL for ProcessSucceeded
curl "http://localhost:5000/wps?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326&storeExecuteResponse=true&status=true"
```
Python client: `python scripts/owslib_check.py [wps_url]` drives all operations with OWSLib (tested). Results are
ordinary Cloud Optimized GeoTIFFs and load in QGIS as raster layers (Layer > Add Raster Layer, local file or URL).
QGIS 3 has no built-in WPS client, and I have not tested a WPS plugin; any WPS 1.0.0 client should work with the same URL.

### Conformance verification
- **Schema validation.** Responses are validated against the official OGC WPS 1.0.0 and OWS 1.1 XSDs (vendored in
  `tests/schemas/`): GetCapabilities, DescribeProcess, synchronous Execute, `ExceptionReport`, the asynchronous
  `ProcessAccepted` response and the final `ProcessSucceeded` / `ProcessFailed` status documents.
  `pytest tests/test_schema.py` covers the synchronous documents; `python scripts/validate_schema.py [wps_url]`
  runs the full asynchronous lifecycle against a running server.
- **Independent client.** `python scripts/owslib_check.py [wps_url]` drives GetCapabilities, DescribeProcess and an
  asynchronous Execute with status polling through OWSLib.
- The suite found several places where PyWPS 4.8 violates the schemas or the OGC axis-order convention; these are
  fixed in `app/pywps_compat.py` (see section 3). The response options above (`lineage`, `RawDataOutput`,
  `ResponseDocument`) are tested too, over both KVP and XML POST. Not covered: WPS 2.0, `Reference` inputs, and
  every possible request variant.

## 3. Design and implementation choices
- **PyWPS 4.8 on Flask + gunicorn, in Docker.** PyWPS asynchronous execution uses `multiprocessing` and does not work
  on Windows, so the service is run in Docker (Linux).
- **Pipeline** (`app/`): find scenes whose *actual pixels* cover the bbox -> reproject the bbox into the scene's UTM zone
  -> read only the needed rows of the two bands the chosen index uses (windowed read) -> DN to radiance using the
  per-band `Lmin/Lmax` in `BAND_META.txt` -> index -> write a COG.
- **Scene selection uses real pixel coverage.** Scenes are tilted, so a bounding rectangle overstates coverage; the
  18 May scene's rectangle contains central Hyderabad but its slanted edge cuts through it (45% valid), so it is rejected.
- **COG for outputs and for the cache, not for the sources.** The source tifs are uncompressed and row-striped, so a
  bbox clip already reads little; converting them would gain nothing.
- **Two data modes, one code path.** Full scenes (local, from `data/`) or a pre-clipped cache (public). The cache has
  the same folder layout (`BAND2..5.tif` + `BAND_META.txt`) with small compressed COG bands of the *original* DN values;
  NDVI from the cache was checked to be bit-identical to NDVI from the full scene for the Hyderabad demo window. `scripts/build_cache.py` builds it from scenes you
  downloaded yourself; the public image contains only this cache (~38 MB for both scenes) and no credentials.
- **PyWPS conformance fixes** (`app/pywps_compat.py`, applied at import so they survive reinstalls):
  - Axis order: PyWPS 4.8 delivers XML-POST bounding boxes as lon,lat but KVP as lat,lon. Both now follow the OGC
    convention (lat,lon for EPSG:4326); regression tests cover both encodings.
  - A failed process was reported as `wps:ExceptionReport` (the schema requires `ows:ExceptionReport` with a `version`
    attribute), and `ProcessAccepted` carried a `percentCompleted` attribute the schema does not allow.
  - `serviceInstance` was double-escaped (`&amp;amp;`), advertising a wrong URL.
  - Process keywords and an input without an `AnyValue` also made capabilities/DescribeProcess schema-invalid; fixed in
    the process definition.
- **Shared job database.** PyWPS tracks running async jobs in a database that defaults to `sqlite:///:memory:`, which is
  private to each process. Async jobs run in forked children, so the server never saw them finish and, after
  `parallelprocesses` (2) jobs, queued every new async job forever. `app/server.py` points it at a file database
  (reset at start-up; one gunicorn worker).
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
A step-by-step live demo script: **[docs/DEMO.md](docs/DEMO.md)**.

How the pieces fit together, Docker/ngrok basics and troubleshooting: **[docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md)**.

Tests: `.venv/Scripts/python -m pytest` (the real-data tests are skipped if `data/` is empty).
`scripts/inspect_scene.py <scene folder or zip>` prints tif header metadata.

## 5. Known limitations
- **Radiance by default; TOA reflectance is built but disabled.** Indices are computed on at-sensor radiance.
  `radiometry=toa` is implemented (formula from NRSC's own surface-reflectance guide, tested against hand calculations)
  but needs the per-band solar irradiance (ESUN). I could not find a citable open source for the LISS-III values and did
  not guess them, so the service refuses `toa` until `app/esun_liss3.json` is filled from a publication (`later.md` says
  how). Radiance carries no solar-irradiance correction; since red sunlight is stronger than NIR, NDVI is expected to be
  biased low (the Hyderabad 23 May clip has a median NDVI of about -0.05), but I could not quantify the bias without
  the ESUN values. Treat values as relative. The priced BOA surface-reflectance product is the proper alternative.
- **Public instance serves only the cached area and scenes** (Hyderabad, 18 and 23 May 2026; the 18 May scene covers only the western part of the city centre, so
  most requests resolve to 23 May); anything else returns a
  clean "no scene covers this request" exception.
- **No Bhoonidhi API integration: data preparation is manual.** The service never talks to Bhoonidhi. Scenes are
  downloaded by hand (with a Bhoonidhi account) into `data/` and, for the public instance, cut down by
  `scripts/build_cache.py`. A client that searches the STAC API and downloads scenes on demand would make "fetch the
  bands on the backend" literal, but it needs a personal account (credentials on a public server would expose that
  account and its download quota) and I could not test it against the real API, so it is future work.
- No cloud masking (cloud percentage is empty in the scene metadata); single-scene results, no mosaicking or
  time-series; the two scenes use different UTM zones so combining them would need reprojection.
- **On radiance, only relative contrast is meaningful, not absolute values or standard thresholds.** Real example
  (Hyderabad, 23 May): every pixel has a positive MNDWI (minimum 0.61) and the median NDBI is -0.66, most likely because the
  SWIR band's radiance range (Lmax 7.5) is tiny next to green's (Lmax 52); not verified. The indices do pick out the right places: Hussain Sagar lake sits at
  the 99th percentile of NDWI/MNDWI and the 1st percentile of NDVI/NDBI in the window (checked by an automated test, which
  also confirms band wiring and geolocation). Do not apply "NDWI > 0 means water" until reflectance is available.
- Four indices; SAVI and EVI are not offered (see Processes).
- Not independently validated against a reference NDVI product. Manual checks done by the author: the GeoTIFF output
  loads in QGIS over central Hyderabad with the expected CRS and layout, and the public URL answers from a phone on
  mobile data. The automated lake test is a relative check only.

## 6. Data and attribution
Contains data from Resourcesat-2 LISS-III, **ISRO-IRS**, distributed through Bhoonidhi (NRSC/ISRO). Scenes are not
redistributed in this repository (`data/` and `cache/` are git-ignored). Check the Bhoonidhi end-user licence before
publishing derived products beyond this coursework.

## 7. Repository layout
```
app/            service code (radiometry, indices, scene clipping, WPS process, Flask server)
scripts/        inspect_scene.py, build_cache.py, owslib_check.py, validate_schema.py
tests/          unit, WPS protocol and schema-validation tests (tests/schemas = official OGC XSDs)
docs/           HOW_IT_WORKS.md (architecture, Docker/ngrok guide), DEMO.md (live demo script)
Dockerfile, pywps.cfg, requirements.txt
later.md        deferred work and priorities
```
