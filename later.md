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
- PARTLY DONE: README section 6 states the licence position carefully. The EULA text itself was NOT read (only on the portal login page); read it, and ask NRSC if redistributing outputs beyond coursework.
- Wake the free-tier host before the demo; keep an ngrok fallback.
- DONE (2026-10-09): service metadata in `pywps.cfg`, expiry of stored results (`app/cleanup.py`), CI workflow, `build_cache.py` test, README/DEMO corrected (no live-fetch feature exists).
- NOT DONE, on purpose: an on-demand Bhoonidhi API client (needs a personal account, cannot be tested without credentials).

## COGs
- Do NOT convert the source scenes (uncompressed, row-striped, clip reads are already cheap).
- DO use COG for (a) the pre-clipped band cache shipped with the public deployment and (b) WPS GeoTIFF outputs (`rasterio.shutil.copy(..., driver="COG")`).
