# Demo script

About 10 minutes. Everything is a real request to the public service; nothing is mocked. The map page comes first
(it is what people remember); the terminal steps afterwards prove it is a standards-compliant OGC WPS underneath.

## Before you start (10 minutes earlier)
1. Restart the laptop if it has been up for days, then start Docker Desktop and wait for it to say it is running.
   The container is created with `--restart unless-stopped`, so `docker ps` should show `bwps` as **Up**; if not:
   `docker start bwps`.
2. `ngrok http 5000` in its own terminal. Copy the `https://….ngrok-free.dev` address.
3. If it differs from the address the container was started with, restart the container with it:
   `docker rm -f bwps` then `docker run -d --name bwps --restart unless-stopped -p 5000:5000 -e PUBLIC_BASE_URL=https://YOUR-ADDRESS bhoonidhi-wps`.
4. In a terminal set the address once so the commands below can be pasted: bash `W="https://YOUR-ADDRESS/wps"`,
   PowerShell `$W = "https://YOUR-ADDRESS/wps"`.
5. Open `https://YOUR-ADDRESS/` in the browser (this is the map page). If ngrok shows a "Visit Site" page, click through
   once beforehand. Keep a second tab on `http://localhost:5000/` as a fallback. QGIS open with an OpenStreetMap basemap
   on Hyderabad (optional step 5).
6. Do the whole script **once** beforehand, not repeatedly: the free ngrok plan limits connections.

The demo area, central Hyderabad: `miny,minx,maxy,maxx = 17.35,78.40,17.45,78.55` (OGC order for EPSG:4326 is latitude first).

## 1. The problem (30 s, no screen)
"Bhoonidhi has a STAC search and a download button. To get NDVI for one area you download a 450 MB scene, unzip it,
clip two bands and compute it yourself. There is no processing service. We built one as an OGC WPS, and this page is
just one client of it."

## 2. The map page (about 4 minutes)
1. The dashed outlines are the scenes prepared on this instance (their true, tilted image footprints).
2. Click **Draw area**, then click two opposite corners over central Hyderabad inside an outline. (Or press **Use central
   Hyderabad**.) The page shows the box size and which scene dates cover it.
3. Date `2026-10-02`, index **NDVI**, press **Run WPS process**. The result is overlaid on the map. Point out: the lake
   (Hussain Sagar) is the darkest feature (no vegetation), parks and gardens are green; legend, statistics, download links.
4. Switch the date to `2026-05-23` and run again: the pattern changes (the 2 Oct scene, after the monsoon, is greener).
   Say: "the two scenes come from different satellites, so compare patterns, not absolute numbers."
5. Switch the index to **NDWI** and run: now the lake is the strongest feature.
6. Open **The WPS request behind this**: "This is the exact OGC request the page sent. Any WPS client can send it."
7. Toggle **Dark mode**. Then draw a box outside every outline and show the page refusing it with an explanation.
8. Say the caveat out loud: "The colours are a stretch of this result, so they show relative contrast. Values are
   computed on at-sensor radiance, not reflectance, so thresholds like 'NDWI > 0 means water' do not apply yet."

## 3. It is a standard WPS underneath (terminal, about 4 minutes)
**Discovery:**
```
curl "$W?service=WPS&request=GetCapabilities"
curl "$W?service=WPS&version=1.0.0&request=DescribeProcess&identifier=spectral-index"
```
Point out: processes `ndvi` and `spectral-index`; inputs `bbox`, `index`, `date`, `radiometry`; outputs the GeoTIFF, the
preview PNG and the stats. A client learns all of this without documentation.

**Execute, synchronous and asynchronous:**
```
curl "$W?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326"
curl "$W?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326&storeExecuteResponse=true&status=true"
```
The first returns `ProcessSucceeded` with the stats inline and a link to the GeoTIFF. The second returns
`ProcessAccepted` with a `statusLocation`; open it to see `ProcessSucceeded` (that is how long jobs behave).

**Raw output and lineage:**
```
curl -o ndvi.tif "$W?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326&RawDataOutput=ndvi"
curl "$W?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326&lineage=true"
```

**Correct failures, not crashes:**
```
curl "$W?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=28.5,77.1,28.6,77.3,urn:ogc:def:crs:EPSG::4326"
curl "$W?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326&RawDataOutput=nope"
```
Both return a standard OGC `ExceptionReport` with a readable message.

**An independent client, and the official schemas:**
```
python scripts/owslib_check.py "$W"
python scripts/validate_schema.py "$W"
```
OWSLib discovers, runs and polls the process with no code written for our service. The second validates every reply
type (capabilities, describe, execute, errors, async status) against the official OGC XML schemas; say that PyWPS 4.8
produced schema-invalid XML in several places and we corrected them (README section 3).

## 4. How data gets in (2 minutes)
"The public address serves scenes prepared in advance; logging in to Bhoonidhi through a page is deliberately off on a
public tunnel. I fetched those scenes from Bhoonidhi with a script that logs in, searches its STAC catalogue, and
downloads the scene. The documented API download refused our token, so it uses the portal's own download route (which
emails a one-time code at login). It also checks the pixels: Bhoonidhi listed one scene as covering Hyderabad, but its
image is tilted and had no data there, and it was refused."

**Live fetch from the page (local instance only, the one with login switched on).** Start it beforehand in its own
terminal: `BROWSER_LOGIN=1 .venv/Scripts/python -m app.server` (PowerShell: `$env:BROWSER_LOGIN="1"; .\.venv\Scripts\python -m app.server`) and open
`http://localhost:5000/`. Draw a box outside the outlined scenes (for example over Vijayawada or Warangal, inside a
real Bhoonidhi scene), leave the date on "Newest scene on Bhoonidhi", press **Run**. The dialog asks for your Bhoonidhi
user id and password, then for the code Bhoonidhi emails you; type it in the page and the run continues by itself with a
progress bar (about 140 MB). Say: "Nothing was typed anywhere but the page and my mailbox." Do this **once**, rehearsed,
with a spare minute for the download; if the network is slow, show the same flow with the made-up archive instead:
`python scripts/dev_fake_bhoonidhi.py` (password `correct-horse`, code `123456`, box around 80.6 E 16.5 N), and say that
it is a stand-in. Logging in by choice (the account button) does not start anything.

Optional live proof, local terminal only, once or twice at most (login is limited to 20 per hour):
```
python scripts/fetch_scene.py --bbox 17.35,78.40,17.45,78.55 --dry-run
```
It logs in to Bhoonidhi's real API with your own account (read from the git-ignored `.env`; nothing secret is printed)
and lists the online LISS-III scenes covering the area. Do **not** run it without `--dry-run` in the demo: the real
download needs a code emailed to you.

## 5. Optional: open the result in QGIS (1 minute)
Layer > Add Raster Layer, pick the `ndvi.tif` from step 3 (or paste its URL). It sits over central Hyderabad in
EPSG:32643/32644; style it as singleband pseudocolor. Zoom to Hussain Sagar (17.424 N, 78.474 E).

## Honest limitations to state yourself
- Indices use at-sensor radiance, not reflectance: absolute values are biased and only contrast is meaningful.
  Reflectance (`radiometry=toa`) is built but disabled until a cited solar-irradiance table is available.
- The public instance serves four prepared scenes around Hyderabad (18 and 23 May, 2 and 9 Oct 2026); it has no Bhoonidhi
  credentials and never logs anyone in. Fetching needs a person with a Bhoonidhi account (the emailed code), through the
  page on a local instance or the script. The browser flow has been tested against a fake Bhoonidhi; say whether you have
  tried it with your real account before the demo.
- It is an unofficial service built on data from Bhoonidhi, not part of Bhoonidhi.
- Four indices (NDVI, NDWI, MNDWI, NDBI); no SAVI/EVI, no cloud masking, one scene per answer.

## If something goes wrong
| Symptom | Do this |
|---|---|
| The page shows a grey map with no streets | the browser cannot reach OpenStreetMap tiles (no internet); the WPS still works, use the terminal steps |
| The page says it cannot reach the service | the container is down: `docker start bwps` (or the `docker run` line in step 3 of the setup) |
| `curl` hangs or the browser shows an ngrok error page | ngrok is down or rate limited: wait a minute; restart ngrok; restart the container with the new address |
| Links in responses say `localhost` or an old address | container started without the current `PUBLIC_BASE_URL`; restart it with the ngrok address |
| Async stays on `ProcessAccepted` | `docker logs bwps`; restart the container |
| Everything fails | run the same steps against `http://localhost:5000` (page) and `http://localhost:5000/wps`; behaviour is identical |
