# Later

Deferred work. Do not forget these; revisit once the NDVI WPS is solid.

## Priority order
1. Radiometry: TOA reflectance (first item after the WPS works end to end)
2. Expand beyond NDVI
3. Other open items / stretch
4. Always-on hosted deployment (last): push the pre-built image (cache baked in, since `cache/` is git-ignored) to a registry and run it on a free host (Render/Fly). Free tiers sleep when idle, so wake it before any demo. Until then the public URL is ngrok over the local Docker container.

## 1. Radiometry: TOA reflectance
- Why: radiance-based NDVI is biased low (red solar irradiance > NIR); Hyderabad 23 May clip gave median NDVI about -0.05. Primary goal stays the WPS gap; fix accuracy after the service is live.
- v1 computes indices on **radiance**: `L = Lmin + DN * (Lmax - Lmin) / 1023` using the per-band `Lmin`/`Lmax` from `BAND_META.txt` (10-bit data stored as uint16).
- Upgrade to **TOA reflectance**: `rho = pi * L * d^2 / (ESUN * sin(sun_elevation))`. Needs the LISS-III ESUN per band (not in `BAND_META.txt`; source from the Resourcesat handbook/NRSC doc and cite it) and `SunElevationAtCenter` plus Earth-Sun distance from the pass date.
- Keep the radiometry step in one module with a `mode` parameter (`radiance` | `toa`) so the WPS layer does not change. Expose `mode` as an optional WPS input once both exist.
- Document in the README which one v1 used and the limitation (no atmospheric correction; the priced BOA product is the real surface reflectance).

## 2. Expand beyond NDVI (after TOA reflectance)
- Add a registry-based `spectral-index` process: formulas in one dict, `index` as a LiteralInput with allowed values.
- LISS-III bands: B2 Green, B3 Red, B4 NIR, B5 SWIR. Supported: NDVI, SAVI `1.5*(NIR-Red)/(NIR+Red+0.5)`, NDWI `(Green-NIR)/(Green+NIR)`, MNDWI `(Green-SWIR)/(Green+SWIR)`, NDBI `(SWIR-NIR)/(SWIR+NIR)`.
- Not possible: EVI (no Blue band).
- Keep a plain `ndvi` process as the headline for the demo.
- Stretch only: `slope` (needs a DEM), `scene-search` helper, mosaic across the path 99/100 boundary, temporal change between the 18 May and 23 May scenes.

## Other open items
- `nodata` is NOT set in the tifs. Assume DN 0 is background, but verify with a small window read at the scene edge; mask it explicitly in code.
- The two scenes use different CRSs (zone 43N vs 44N). Any cross-scene work (mosaic, temporal diff) must reproject; a bbox in lat/lon must be transformed per scene.
- Cloud cover is empty in `BAND_META.txt`; scene choice must rely on STAC metadata (or a simple NIR/Red brightness mask).
- Confirm licence/attribution wording for redistributing derived data ("ISRO-IRS" credit).
- Wake the free-tier host before the demo; keep an ngrok fallback.

## COGs
- Do NOT convert the source scenes (uncompressed, row-striped, clip reads are already cheap).
- DO use COG for (a) the pre-clipped band cache shipped with the public deployment and (b) WPS GeoTIFF outputs (`rasterio.shutil.copy(..., driver="COG")`).
