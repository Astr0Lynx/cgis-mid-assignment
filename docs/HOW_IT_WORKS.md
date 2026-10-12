# How this system works

A guide to what is running, why, and how to operate it. No prior Docker, WPS or ngrok knowledge assumed.
(The README covers *what gap this fills*; this file covers *how the thing works*.)

## 1. The big picture

```
 Someone (a script, a WPS client, a browser)
        |   HTTPS request:  "give me NDVI for this box"   (the map page sends exactly such requests)
        v
 +---------------+      +--------------------------------------------------+
 |    ngrok      |      |  Docker container "bwps" (a mini Linux computer)   |
 | public URL -> | ---> |                                                    |
 | your laptop   |      |   gunicorn (web server)                            |
 +---------------+      |      -> Flask app (app/server.py)                  |
   (only for the demo)  |           -> PyWPS (the WPS standard engine)       |
                        |                -> our process "spectral-index"     |
                        |                     -> reads satellite bands       |
                        |                     -> computes the index          |
                        |                     -> GeoTIFF + colour PNG + stats|
                        +--------------------------------------------------+
```

- **The service** answers standard OGC WPS requests at one URL ending in `/wps`. It offers two processes, `ndvi` and
  `spectral-index` (NDVI, NDWI, MNDWI, NDBI).
- **Docker** is the box it runs in. The public instance runs on an **Azure VM** (Docker, with Caddy giving it HTTPS; see
  [AZURE.md](AZURE.md)). **ngrok** is a tunnel that gives a laptop copy a public web address, kept as a fallback.
- These are only *how it is delivered*. The actual idea is the code in `app/`.

## 2. Words you need

| Term | Meaning here |
|---|---|
| **WPS** (Web Processing Service) | An OGC standard for "run a computation over HTTP". A client can ask what the service offers (*GetCapabilities*), how to call one thing (*DescribeProcess*), and then run it (*Execute*). |
| **Process** | One computation the service offers. We have one: `ndvi`. |
| **KVP vs XML POST** | Two ways to send the same request. KVP puts everything in the URL (`?service=WPS&request=Execute&...`), easy to paste in a browser. XML POST sends an XML document in the body; OWSLib uses this. Both work. |
| **Synchronous / asynchronous** | Sync: you wait and get the answer in the reply. Async (`storeExecuteResponse=true&status=true`): you immediately get a *status URL*, and poll it until it says `ProcessSucceeded`. |
| **Reference vs raw** | By default the reply is an XML document containing a *link* to the result file (reference). `RawDataOutput=ndvi` skips the XML and returns the file itself. |
| **Lineage** | `lineage=true` makes the XML reply also echo back the inputs you sent and the outputs you asked for, so a saved reply is self-describing. |
| **Scene** | One satellite image file set from Bhoonidhi: four band GeoTIFFs plus `BAND_META.txt`. About 450 MB. |
| **DN** | "Digital number": the raw integer in each pixel (0 to 1023 for LISS-III). Not yet a physical quantity. |
| **Radiance** | DN converted to physical units using `Lmin`/`Lmax` from `BAND_META.txt`. What v1 uses. |
| **NDVI** | (NIR - Red) / (NIR + Red). High for healthy vegetation, near 0 for bare ground, negative for water. |
| **COG** | Cloud Optimized GeoTIFF: a GeoTIFF laid out so readers can fetch just the part they need. Our outputs, and our cache, are COGs. |
| **Cache** | Small pre-cut copies of the scenes (Hyderabad area only), so the public service needs no Bhoonidhi login. |

## 3. The files

```
app/
  __init__.py       Points PROJ/GDAL at rasterio's own data (a PostGIS install on this PC breaks it otherwise).
  radiometry.py     Reads BAND_META.txt; converts DN -> radiance, or -> TOA reflectance (needs esun_liss3.json filled in).
  esun_liss3.json   The per-band solar irradiance table. Empty on purpose: see section 10.
  indices.py        The index registry: NDVI, NDWI, MNDWI, NDBI, each a band pair (a-b)/(a+b). Add one line to add an index.
  scene.py          Opens a scene folder; turns a lat/lon box into pixel rows; reads only those rows;
                    decides which scenes truly cover the box; writes the result as a COG.
  processes/ndvi.py The WPS processes `ndvi` and `spectral-index` (one shared class): inputs/outputs, checks, handler.
  pywps_compat.py   Fixes for places where PyWPS 4.8 breaks the OGC standard (see section 8).
  server.py         The web app: /wps (the service), /outputs/ (result files), / (the map page), /api/info, /api/coverage
                    (outlines of the area actually on disk), plus the login and fetch routes below.
  webauth.py        Browser login (only if BROWSER_LOGIN=1): /api/login, /api/otp, /api/session, /api/logout. Keeps each
                    visitor's logged-in Bhoonidhi client in memory behind an HttpOnly cookie; never stores the password.
  fetchjobs.py      /api/scenes (list the Bhoonidhi scenes over an area for a date range) and /api/fetch (download one scene in
                    a background thread for a logged-in visitor; the page polls it).
  clouds.py         A rough "cloud or haze may be present" hint from green-band brightness, added to every result's statistics.
  preview.py        Colour PNG preview of a result (reprojected to lat/lon, percentile stretch), built with numpy + zlib.
  static/           The interactive map page: index.html, app.js, style.css and a vendored copy of Leaflet.
  cleanup.py        Deletes stored results older than OUTPUT_TTL_HOURS so the disk cannot fill up.
  bhoonidhi_client.py  Talks to Bhoonidhi: API login + STAC search, the portal's own login (emailed one-time code),
                    the portal's search and download route, safe unpacking. Verified on the real service (README section 5).
  live_fetch.py     Fetching glue used by the page, the script and (off by default) request-time mode. Searches are split
                    until no answer fills a page of 100 (so none are cut off); before a download it reads the scene's
                    small public .meta file so a tilted scene that misses the area is skipped.
scripts/
  build_cache.py    data/ -> cache/ (cuts the scenes down to Hyderabad).
  fetch_scene.py    Fetches a scene from Bhoonidhi into data/ (--dry-run only searches; --probe-portal-download checks the
                    download path without downloading). Needs your own account.
  dev_fake_bhoonidhi.py  The real app against a made-up Bhoonidhi, to try login and fetching with no account.
  inspect_scene.py  Prints a scene's file headers.
  owslib_check.py   Drives the service with a third-party client (OWSLib).
  validate_schema.py  Checks every kind of reply against the official OGC XML schemas.
tests/              Automated tests (260+). tests/schemas/ holds the official OGC XSD files; tests/e2e/ has the browser tests.
deploy/             docker-compose.yml + Caddyfile for the Azure VM (see AZURE.md).
Dockerfile          Recipe for the container image.        pywps.cfg   PyWPS settings.
data/   cache/   outputs/   workdir/   state/    -> created at run time, NOT in git (see .gitignore).
```

## 4. What happens when you call `Execute`

1. **Request arrives** at `/wps`. `server.py` first peeks at which outputs you asked for; an unknown name gets a
   clean OGC error instead of a crash.
2. **PyWPS parses it** (KVP or XML), checks the inputs against the process definition, and calls our handler.
   `pywps_compat.py` makes sure a bounding box means the same area regardless of how it was sent.
3. **Checks** (`processes/ndvi.py`): box is valid; box is not bigger than 16 Mpixel (~96 x 96 km); `date`, if given, is `YYYY-MM-DD`.
4. **Find a scene** (`scene.py`): for every scene folder, convert the box into that scene's map projection and check
   that real pixels cover it (satellite scenes are tilted, so a rectangle test is not enough). Pick the newest match,
   or the one with your `date`. None -> error "No available scene fully covers this bbox".
5. **Read only what is needed:** the Red and NIR bands, just the rows/columns of your box.
6. **Radiometry:** DN -> radiance; background (DN 0) becomes "no data". With `radiometry=toa` the radiance is further
   converted to reflectance (needs the solar irradiance table, otherwise a clear error).
7. **Index:** per pixel, `(a-b)/(a+b)` of the two bands the chosen index needs; invalid pixels stay "no data".
8. **Package the result:** a COG (`ndvi`) plus a small JSON (`stats`: scene id, date, min/mean/max, valid fraction).
9. **Reply:** an XML document with a link to the file (default), or the raw file / JSON (`RawDataOutput`),
   with the echoed inputs if `lineage=true`.

**Async** differs only in step 2: PyWPS answers `ProcessAccepted` at once, runs steps 3-8 in a background process, and
keeps updating a status file at `/outputs/<id>.xml`. The result file ends up at `/outputs/<id>/ndvi.tif`.

## 5. Docker, for someone who has never used it

- An **image** is a frozen recipe plus all the software (Python, our code, the libraries, the cache). Built once from the
  `Dockerfile`. Here it is called `bhoonidhi-wps`.
- A **container** is a running copy of an image: a small Linux computer inside your PC. Here it is called `bwps`.
- **Why we need it:** the WPS standard's *asynchronous* mode does not work on Windows, but works on Linux; the
  container gives us Linux. It also means the exact same package can be run on any other machine.
- **Port mapping `-p 5000:5000`:** "traffic to my PC's port 5000 goes to port 5000 in the container".
- The container's disk is **throw-away**: when you remove the container, its result files disappear. That is fine.

Commands (run in a terminal in the repository folder; Docker Desktop must be running):

| I want to... | Command |
|---|---|
| Build the image (after changing code) | `docker build -t bhoonidhi-wps .` |
| Start it, local only | `docker run -d --name bwps -p 5000:5000 bhoonidhi-wps` |
| Start it behind ngrok | `docker run -d --name bwps -p 5000:5000 -e PUBLIC_BASE_URL=https://YOUR-NGROK-URL bhoonidhi-wps` |
| See if it is running | `docker ps` |
| Read its log | `docker logs bwps` |
| Stop and remove it | `docker rm -f bwps` |
| **Restart with new code** | `docker rm -f bwps`, then build, then run (above) |
| Use the full scenes instead of the cache | add `-v "%CD%\data:/srv/data:ro"` to the run command (PowerShell: `-v "${PWD}/data:/srv/data:ro"`) |

You cannot have two containers named `bwps`, which is why you `docker rm -f bwps` before starting a new one.

## 6. ngrok

Your laptop is not reachable from the internet. ngrok connects *out* to its servers and gives you a public HTTPS
address that forwards to `localhost:5000`. Run it in its own terminal: `ngrok http 5000`, and keep it open.

- On the free plan the address **changes every time you restart ngrok**.
- **`PUBLIC_BASE_URL`** tells the service its own public address. It is needed because replies contain links
  ("download the result here", "poll this status URL"). Without it those links say `localhost` and only work on your PC.
  If the ngrok address changes, restart the container with the new one.
- The free plan limits connections. Do not run polling loops against the public address.

### Settings you can change (environment variables on `docker run -e NAME=value`)
| Name | Default | Meaning |
|---|---|---|
| `PUBLIC_BASE_URL` | `http://localhost:5000` | the address clients use; goes into links in replies |
| `MAX_PIXELS` | 16000000 | largest request accepted, in output pixels (about 96 x 96 km) |
| `OUTPUT_TTL_HOURS` | 24 | stored results older than this are deleted; `0` keeps everything |
| `OUTPUT_PURGE_MINUTES` | 30 | how often the cleanup runs |
| `BHOONIDHI_DATA_DIR` | `/srv/data` in Docker | folder holding the scene folders |
| `BHOONIDHI_LIVE`, `BHOONIDHI_USER`, `BHOONIDHI_PASSWORD` | unset | live mode (all three needed). Your own account; **local use only** |
| `BHOONIDHI_LOOKBACK_DAYS`, `BHOONIDHI_MAX_DOWNLOADS` | 60, 2 | live mode: search window when no date is given; downloads per request |
| `TRUST_PROXY` | unset | `1` behind Caddy: rate limits use the visitor's address from `X-Forwarded-For` |
| `PORT` | 5000 | port for `python -m app.server` (use another to run beside the container) |
| `BROWSER_LOGIN` | unset | `1` lets visitors log in to Bhoonidhi from the page and fetch scenes. **Not on a public tunnel**; see README section 3 |
| `FETCH_TTL_HOURS`, `FETCH_MAX_GB` | 6, 3 | fetched scenes (`data/_fetched`) are shared, then deleted after this age or above this total size |
| `BHOONIDHI_DOWNLOAD_AUTH` | `bearer` | how a scene is downloaded: `portal` (works: the portal's own route; asks for an emailed code) or an official-API form `bearer`/`query`/`token-header` (all refused by the server) |

## 7. Data modes

- **Public / default:** the image contains `cache/` (about 70 MB: four scenes around Hyderabad, each cut to a ~65 x 55 km
  area). No Bhoonidhi account is involved. A box outside the prepared scenes gets "no scene covers this bbox", and the
  map page only lists scenes that fully cover the box you drew.
- **Full scenes (local):** mount your downloaded `data/` folder over it (table above). Same code, any area those
  scenes cover. Build the cache with `python scripts/build_cache.py` after adding scenes.
- **Getting scenes from Bhoonidhi (local, needs your own account):** `python scripts/fetch_scene.py --bbox ... --date ... --download-auth portal`.
  It logs in to the API and searches the STAC catalogue, logs in to the portal (Bhoonidhi emails a 6-digit code, which the
  script asks you to type), asks the portal's search where the scene lives, downloads about 140 MB, unpacks only the four
  bands and `BAND_META.txt`, and checks that real pixels cover your area (it refuses scenes whose tilted image misses it).
  Then `build_cache.py` and a rebuilt image serve it. Credentials must come from the environment or a git-ignored `.env`,
  never from the image.
- **Getting scenes from the page (the Azure instance, or local with `BROWSER_LOGIN=1`):** the same steps, driven by the visitor.
  Press Run on an area no scene covers (or use **Find Bhoonidhi scenes** to choose a date); a dialog takes the user id and
  password, then the emailed code; the pending Run continues by itself.
  The scene lands in `data/_fetched`, is visible to everyone (`/api/coverage` re-reads the disk), and expires after
  `FETCH_TTL_HOURS`. To try it with no account: `python scripts/dev_fake_bhoonidhi.py`. Automated browser test: `tests/e2e/`.

## 8. Problems found while building (worth knowing)

| Symptom | Cause | Fix |
|---|---|---|
| Async jobs hung at `ProcessAccepted` after two runs | PyWPS counts running jobs in an in-memory database; jobs run in separate processes, so the count never went down. At the limit (2) every new job queued forever. | File-based job database in `server.py` |
| Same box gave different areas via POST vs KVP | PyWPS flips latitude/longitude for POST only | `pywps_compat.py` |
| Strict clients rejected some replies | PyWPS output violated the WPS XML schema in several places (error element, attributes, escaping, lineage values) | `pywps_compat.py`, process definition |
| `RawDataOutput=ndvi` gave HTTP 500 | PyWPS deletes the working folder before reading the result file | Return the result as bytes, not a path |
| Unknown output name gave HTTP 500 and an HTML page | PyWPS raised an unhandled error | Check in `server.py`, return an OGC exception |
| Hundreds of `404` lines in ngrok | A leftover test script kept polling a job that belonged to a destroyed container; OWSLib's own polling loop has no limit and no pause | Our check script now has a deadline and a delay |
| NDVI from rasterio failed on this PC | Environment variables from a PostGIS install pointed at an incompatible PROJ | `app/__init__.py` |

## 9. Tests: what each file proves

- `tests/test_core.py`: the maths and the scene logic on synthetic and real data.
- `tests/test_wps.py`: the service behaves (capabilities, describe, execute, dates, size limit, raw output, errors).
- `tests/test_schema.py`: replies validate against the **official OGC schemas**.
- `tests/test_cleanup.py`, `tests/test_build_cache.py`: result expiry, and that the cache gives identical indices.
- `tests/test_bhoonidhi_client.py`, `test_live_mode.py`, `test_fetch_script.py`, `test_precheck.py`, `test_scene_choice.py`,
  `test_webauth.py`, `test_fetchjobs.py`: the Bhoonidhi client, live mode, browser login, scene listing and fetching, all against a
  **fake** Bhoonidhi (`tests/live_helpers.py`) that copies behaviours seen on the real one (404 for an empty search, a full page of
  100, the "permission denied" page). They prove the logic; only a real account proves the real service (login, search, the
  download and the footprint check have been verified that way).
- `tests/test_clouds.py`, `test_absolute.py`, `test_versions.py`, `test_attribution.py`: the cloud hint, the absolute colour scale and
  reflectance option, WPS version handling, and the ISRO-IRS attribution.
- `tests/e2e/*.js`: the map page in a real Chrome (login, fetch, scene choice, saved layers); not part of CI.
- `scripts/validate_schema.py URL`: same, including async, against a *running* server.
- `scripts/owslib_check.py URL`: a third-party WPS client can use it end to end.

Run the tests with `.venv\Scripts\python -m pytest`. They need no Docker (async cannot run on Windows, so async is
checked by the two scripts against the container).

## 10. Known limits

Radiance by default, so index values are biased and only relative contrast is meaningful (see README section 5). TOA reflectance is implemented but switched off until someone fills `app/esun_liss3.json` from a published source (the numbers are in a paywalled paper; they must not be guessed); four indices (no SAVI/EVI); the prepared scenes are four around Hyderabad (others can be fetched by a visitor with a Bhoonidhi account); no cloud masking, only a
rough warning; one scene per answer; Bhoonidhi refuses some 2021-2023 downloads. See README section 5.
