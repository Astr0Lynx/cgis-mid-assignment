# Later

Deferred work. Do not forget these; revisit once the NDVI WPS is solid.

## Priority order
1. Radiometry: TOA reflectance (first item after the WPS works end to end)
2. Expand beyond NDVI
3. Other open items / stretch
4. Always-on hosted deployment (last): push the pre-built image (cache baked in, since `cache/` is git-ignored) to a registry and run it on a free host (Render/Fly). Free tiers sleep when idle, so wake it before any demo. Until then the public URL is ngrok over the local Docker container.

## 1. Radiometry: TOA reflectance  (STATUS: code done, BLOCKED on ESUN numbers)
- DONE: `app/radiometry.py` implements TOA (`radiometry=toa`, optional WPS input, default `radiance`); Earth-Sun distance from the pass date; sun elevation from `BAND_META.txt`; tested against hand-computed values.
- BLOCKED: no open source found for the LISS-III per-band ESUN (NRSC handbook, NRSC product guides, two arXiv papers checked). The service refuses `toa` until `app/esun_liss3.json` has a `source` and a value for every band. Do NOT enter values from memory.
- TO UNBLOCK: get the table from Pandya et al. 2013, Remote Sensing Letters 4(3), doi:10.1080/2150704X.2012.727491 (via institutional access), mind the units (W/m2/um -> divide by 10 for mW/cm2/um), fill the JSON, rebuild the image, then compare NDVI radiance vs TOA on Hyderabad and add the result to the README.
- Original notes below (partly superseded):
- Why: radiance-based NDVI is biased low (red solar irradiance > NIR); Hyderabad 23 May clip gave median NDVI about -0.05. Primary goal stays the WPS gap; fix accuracy after the service is live.
- v1 computes indices on **radiance**: `L = Lmin + DN * (Lmax - Lmin) / 1023` using the per-band `Lmin`/`Lmax` from `BAND_META.txt` (10-bit data stored as uint16).
- Upgrade to **TOA reflectance**: `rho = pi * L * d^2 / (ESUN * sin(sun_elevation))`. Needs the LISS-III ESUN per band (not in `BAND_META.txt`; source from the Resourcesat handbook/NRSC doc and cite it) and `SunElevationAtCenter` plus Earth-Sun distance from the pass date.
- Keep the radiometry step in one module with a `mode` parameter (`radiance` | `toa`) so the WPS layer does not change. Expose `mode` as an optional WPS input once both exist.
- Document in the README which one v1 used and the limitation (no atmospheric correction; the priced BOA product is the real surface reflectance).

## 2. Expand beyond NDVI  (STATUS: DONE for NDVI/NDWI/MNDWI/NDBI via process `spectral-index`)
- DONE: registry in `app/indices.py`, process `spectral-index`, tests incl. a real-data lake check. Decided NOT to wait for TOA.
- STILL TO DO: SAVI (only valid on reflectance; add once `radiometry=toa` is unblocked), then document that thresholds become usable.
- Original notes below (partly superseded):
- Add a registry-based `spectral-index` process: formulas in one dict, `index` as a LiteralInput with allowed values.
- LISS-III bands: B2 Green, B3 Red, B4 NIR, B5 SWIR. Supported: NDVI, SAVI `1.5*(NIR-Red)/(NIR+Red+0.5)`, NDWI `(Green-NIR)/(Green+NIR)`, MNDWI `(Green-SWIR)/(Green+SWIR)`, NDBI `(SWIR-NIR)/(SWIR+NIR)`.
- Not possible: EVI (no Blue band).
- Keep a plain `ndvi` process as the headline for the demo.
- Stretch only: `slope` (needs a DEM), `scene-search` helper, mosaic across the path 99/100 boundary, temporal change between the 18 May and 23 May scenes.

## Other open items
- DONE (verified 2026-10-09): `nodata` is not set in the tifs, but DN 0 is exactly the background. 20.6% of each scene is zero in all four bands (one tilted wedge); the ~2,600 pixels per scene that are zero in only some bands are all within 6 px of that wedge (edge slop); there are no zeros inside the image. Masking per band in `radiometry.dn_to_radiance` is correct.
- The two scenes use different CRSs (zone 43N vs 44N). Any cross-scene work (mosaic, temporal diff) must reproject; a bbox in lat/lon must be transformed per scene.
- Cloud cover is empty in `BAND_META.txt`; scene choice must rely on STAC metadata (or a simple NIR/Red brightness mask).
- DONE (2026-10-11): the EULA text was read (user pasted it) and README section 6 compares each clause with the service. Attribution "ISRO-IRS" is in the page footer, GeoTIFF tags and stats JSON. Open points (not asked of NRSC): derived outputs shown to unregistered visitors; relaying a visitor login / using the undocumented portal route.
- Wake the free-tier host before the demo; keep an ngrok fallback.
- DONE (2026-10-09): service metadata in `pywps.cfg`, expiry of stored results (`app/cleanup.py`), CI workflow, `build_cache.py` test, README/DEMO corrected (no live-fetch feature exists).
- DONE (2026-10-10; login + search VERIFIED on the real API by the user's --dry-run: 18 online scenes, footprints/dates parsed; DOWNLOAD STILL UNVERIFIED): on-demand Bhoonidhi client + live mode (`app/bhoonidhi_client.py`, `app/live_fetch.py`, `scripts/fetch_scene.py`), tested with a fake of the documented API. FIRST DOWNLOAD ATTEMPT (2026-10-10): HTTP 401 {"Description":"UNAUTHORIZED","Action":"Invalid session detected! Please login."} from an APISIX gateway, with a fresh valid token that search accepts; no redirect, no WWW-Authenticate. The unofficial SDK (geovicco-dev/bhoonidhi-downloader) uses the PORTAL (bhoonidhi.nrsc.gov.in/bhoonidhi/LoginServlet, then GET /bhoonidhi/data/<satellite>/<sensor>/<year>/<month>/<file>.zip?token=<jwt>&product_id=<file>), not the official API. NEXT: run `python scripts/fetch_scene.py --bbox 17.35,78.40,17.45,78.55 --date 2026-10-09 --probe-download-auth` (bearer / ?token= / token header), set BHOONIDHI_DOWNLOAD_AUTH to the one that returns 200. If none does, the portal route would be needed.
- (older note) TO VERIFY NEXT: run `python scripts/fetch_scene.py --bbox 17.35,78.40,17.45,78.55 --date 2026-10-09` (one scene, a few hundred MB, uses download quota) and paste the output. Remaining unknown: what /download returns (client expects a zip with BAND2-5.tif + BAND_META.txt). Answered: no emailed OTP needed; dates from product ids are correct.
- IDEA: persist the access token across processes (async jobs are forked and each authenticates; limit is 20 logins/hour).

## COGs
- Do NOT convert the source scenes (uncompressed, row-striped, clip reads are already cheap).
- DO use COG for (a) the pre-clipped band cache shipped with the public deployment and (b) WPS GeoTIFF outputs (`rasterio.shutil.copy(..., driver="COG")`).

## Status at submission (2026-10-12)
- DONE and verified on the real Bhoonidhi (by the account owner): API login + STAC search; portal login with the emailed
  one-time code; portal search; 141 MB download + unpack; real-pixel coverage check (accepted 2 Oct 2026 Resourcesat-2A,
  refused 9 Oct 2026). The official API `/download` was refused for every token form ("Invalid session detected").
- DONE: interactive map page (draw an area, pick date + index, overlay, legend, WPS request panel, light/dark), colour
  `preview` output, `/api/info`, `/api/coverage`.
- DONE (2026-10-11): log in to Bhoonidhi from the map page (user id, password, then the emailed code, inside our page) and
  fetch a scene on demand; the pending Run continues automatically, logging in by choice starts nothing. Enabled with
  `BROWSER_LOGIN=1`, off on the public ngrok address. Fetched scenes are shared and expire (`FETCH_TTL_HOURS`,
  `FETCH_MAX_GB`). Tested with a fake Bhoonidhi in unit tests and in real Chrome (`tests/e2e`). `/api/coverage` outlines
  now show only the area actually on disk. Pre-download footprint check via the public `.meta` file.
- VERIFIED with the real Bhoonidhi by the account owner (2026-10-11): page-based login with the emailed code + fetch outside
  the prepared area; one code serves every Run in the session (30 min idle / 2 h max).
- VERIFIED with the real Bhoonidhi (2026-10-11, `fetch_scene.py --probe-portal-download`): the `.meta` file is public for
  open-data scenes and gives the true image corners; for the 9 Oct scene it correctly reported NOT covering the Hyderabad bbox,
  so the pre-download check skips it. The portal data path answered 200 with a 141 MB zip.
- DONE (2026-10-11): Azure VM (Malaysia West) with HTTPS via Caddy + Let's Encrypt; browser login enabled there. See docs/AZURE.md.
- NOT DONE: reusing the portal session so the service itself could fetch scenes during a request (the emailed code is
  needed at every login; the unofficial client's VALIDATE_SESSION call suggests a session can be refreshed, untested).
- NOT DONE: TOA reflectance (blocked on a cited ESUN table), SAVI (needs reflectance), an OGC API - Processes front end (pygeoapi) as a follow-up.
