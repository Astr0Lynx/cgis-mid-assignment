# Live demo script

About 8 minutes. Everything is a real HTTP request to the public endpoint; nothing is mocked.

## Before you start (5 minutes earlier)
1. Docker Desktop running; `docker ps` shows `bwps` as **Up**.
2. `ngrok http 5000` running in its own terminal. Copy the `https://….ngrok-free.dev` address.
3. If that address is different from the one the container was started with, restart the container with it
   (see docs/HOW_IT_WORKS.md section 5): `docker rm -f bwps` then `docker run -d --name bwps -p 5000:5000 -e PUBLIC_BASE_URL=https://YOUR-ADDRESS bhoonidhi-wps`.
4. Set the address once per terminal so the commands below can be pasted as they are:
   - PowerShell: `$W = "https://YOUR-ADDRESS/wps"`
   - bash: `W="https://YOUR-ADDRESS/wps"`
5. Do **one** dry run of step 3 and step 5, then stop. Do not loop; the free ngrok plan limits connections.
6. Have QGIS open with a basemap centred on Hyderabad.

The area used everywhere: central Hyderabad, `miny,minx,maxy,maxx = 17.35,78.40,17.45,78.55`
(OGC order for EPSG:4326 is latitude first).

## 1. The problem (30 s, no commands)
"Bhoonidhi has a STAC search and a download button. To get NDVI for one area you download a 450 MB scene, unzip it,
clip two bands and compute it yourself. There is no processing service. We built one, as an OGC WPS."

## 2. Discovery: what does this service offer?  (GetCapabilities, DescribeProcess)
```
curl "$W?service=WPS&request=GetCapabilities"
curl "$W?service=WPS&version=1.0.0&request=DescribeProcess&identifier=ndvi"
```
Point out: process `ndvi`; inputs `bbox` (EPSG:4326) and optional `date`; outputs `ndvi` (GeoTIFF) and `stats` (JSON).
A client can learn all of this without reading any documentation. That is the point of the standard.

## 3. Execute, synchronous
```
curl "$W?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326"
```
Show the XML: `ProcessSucceeded`, the `stats` inline (scene id, date 2026-05-23, min/mean/max), and a **link** to
`ndvi.tif`. Open the link in QGIS (Layer > Add Raster Layer, or drag the URL in). It should sit over Hyderabad.
Style it as singleband pseudocolor.

## 4. Execute, asynchronous (how a long job behaves)
```
curl "$W?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326&storeExecuteResponse=true&status=true"
```
It returns `ProcessAccepted` immediately with a `statusLocation`. Open that URL; it says `ProcessSucceeded` and
carries the result link. (Say: for big areas on the full-scene server this takes longer, which is why async exists.)

## 4b. More than NDVI: the lake test (optional, 1 minute)
```
curl "$W?service=WPS&version=1.0.0&request=DescribeProcess&identifier=spectral-index"
curl -o ndwi.tif "$W?service=WPS&version=1.0.0&request=Execute&identifier=spectral-index&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326;index=ndwi&RawDataOutput=index"
```
DescribeProcess lists the four allowed `index` values. Load `ndwi.tif` in QGIS and zoom to Hussain Sagar (17.424 N,
78.474 E): the lake should be the brightest feature. Say the caveat: absolute values are not meaningful on radiance
(MNDWI is positive over the whole city), only the contrast is.

## 5. Raw output and lineage
```
curl -o ndvi.tif "$W?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326&RawDataOutput=ndvi"
curl "$W?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326&RawDataOutput=stats"
curl "$W?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326&lineage=true"
```
Raw gives the file / JSON with no XML wrapper. Lineage echoes what was asked, so a saved response documents itself.

## 6. A third-party client
```
python scripts/owslib_check.py "$W"
```
"OWSLib is an independent WPS client. It discovers the process, runs it asynchronously and polls it, with no code
written for our service."

## 7. Correct failure
```
# an area outside what the public instance holds (Delhi)
curl "$W?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=28.5,77.1,28.6,77.3,urn:ogc:def:crs:EPSG::4326"
# an unknown output name
curl "$W?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326&RawDataOutput=nope"
```
Both return a standard OGC `ExceptionReport` with a readable message, not a crash.

## 8. Standards evidence
```
python scripts/validate_schema.py "$W"
```
Every reply type (capabilities, describe, execute, errors, async status) is validated against the official OGC XML
schemas. Say what it found: PyWPS 4.8 produced schema-invalid XML in several places and we corrected them
(README section 3).

## 9. Optional: the client really talks to Bhoonidhi (local terminal only)
```
python scripts/fetch_scene.py --bbox 17.35,78.40,17.45,78.55 --dry-run
```
This logs in to Bhoonidhi's real API with your own account (read from your git-ignored `.env`; nothing secret is
printed) and runs a STAC search, listing the online LISS-III scenes that cover the area, with their dates and footprints.
Say what it proves and what it does not: login and search are verified against the real API; the **download** is not
(the server answers `401 Invalid session detected` to `/download`), which is why the public service serves scenes that
were downloaded by hand and prepared in advance. Do not run it more than once or twice (login is limited to 20 per
hour), do not run it without `--dry-run`, and skip this step entirely if you are short of time.

## Honest limitations to state yourself
- Indices use at-sensor radiance, not reflectance, so absolute values are biased and only contrast is meaningful.
  Reflectance (`radiometry=toa`) is built but disabled until a cited solar-irradiance table is available.
- The public instance serves only the cached Hyderabad area and two dates; it has no Bhoonidhi credentials. A local
  "live mode" can fetch scenes on demand from your own account. Login and search have been checked against the real
  API; the download itself has not, so do not demo live mode until `scripts/fetch_scene.py` (without --dry-run) has
  fetched a scene successfully on your machine.
- Four indices (NDVI, NDWI, MNDWI, NDBI); no SAVI/EVI, no cloud masking, one scene per answer.

## If something goes wrong
| Symptom | Do this |
|---|---|
| `curl` hangs or the browser shows an ngrok error page | ngrok is down or rate limited: wait a minute; restart ngrok; restart the container with the new address |
| Links in responses say `localhost` | container started without `PUBLIC_BASE_URL`; restart it with the ngrok address |
| Async stays on `ProcessAccepted` | `docker logs bwps`; restart the container (`docker rm -f bwps`, `docker run ...`) |
| Everything fails | run steps 2, 3 and 7 against `http://localhost:5000/wps` instead; the behaviour is identical |
