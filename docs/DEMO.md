# Demo script

About 10 minutes. Everything is a real request to the public service; nothing is mocked. The map page comes first
(it is what people remember); the terminal steps afterwards prove it is a standards-compliant OGC WPS underneath.

## Before you start (10 minutes earlier)
The service runs on an Azure VM with HTTPS: **https://bhoonidhi-wps-guntesh.malaysiawest.cloudapp.azure.com/** . Nothing needs starting on your laptop.
1. **Network.** The website loads over HTTPS from the IIIT-H campus network (checked 2026-10-12) and from a phone hotspot.
   Only SSH to the VM (administration, not the demo) was unreliable from campus. Check the address loads from the network
   you will present on; if it does not, use a hotspot or the ngrok fallback below.
2. In the Azure portal check the VM `cgis-mid` shows **Running** (it keeps running and billing until stopped; start it in the
   portal if it was stopped, and give it two minutes). Open the address above in the browser: the map page should load with a
   padlock and a "Bhoonidhi account: not logged in" line.
3. In a terminal set the address once so the commands below can be pasted: bash `W="https://bhoonidhi-wps-guntesh.malaysiawest.cloudapp.azure.com/wps"`,
   PowerShell `$W = "https://bhoonidhi-wps-guntesh.malaysiawest.cloudapp.azure.com/wps"`.
4. For the live-fetch part (section 4) have your Bhoonidhi password ready and your mailbox open for the emailed code. Do the
   fetch **once** beforehand: the fetched scene is shared and expires after 6 hours, so repeating it right before the demo
   would hide the fetch (use an area you have not fetched yet).
5. Do the whole script **once** beforehand. QGIS open with an OpenStreetMap basemap on Hyderabad is optional (step 5).

**Fallback (only if the VM is unreachable):** the laptop copy. Start Docker Desktop, check `docker ps` shows `bwps` Up
(else `docker start bwps`), run `ngrok http 5000`, and if its address differs from the one the container was started with:
`docker rm -f bwps` then `docker run -d --name bwps --restart unless-stopped -p 5000:5000 -e PUBLIC_BASE_URL=https://YOUR-NGROK-ADDRESS bhoonidhi-wps`.
That copy serves only the prepared scenes (login is off on a tunnel) and the free ngrok plan limits connections.

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
5. Switch the index to **NDWI** and run: now the lake is the strongest feature. The NDVI result is still there as a
   **saved layer**: untick or reorder layers, change a layer's opacity, click a layer to see its own statistics and downloads.
6. In **OGC operations** press **GetCapabilities** (what the service offers), then **DescribeProcess** (pick `spectral-index`: its inputs and outputs), then **Execute** (the request behind the result). Each shows the exact request and the reply: "This is the exact OGC request the page sent. Any WPS client can send it." "Copy as curl" gives the same call for a terminal.
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

**Live fetch from the page (on the Azure instance, where login is switched on).** Open `https://bhoonidhi-wps-guntesh.malaysiawest.cloudapp.azure.com/`.
(A local copy also works: `BROWSER_LOGIN=1 PORT=5001 .venv/Scripts/python -m app.server`, then `http://127.0.0.1:5001/`.)
Draw a box outside the outlined scenes (for example over Vijayawada or Warangal, inside a
real Bhoonidhi scene), press **Find Bhoonidhi scenes** (or leave the date on "Newest scene on Bhoonidhi"), pick a date from the list (**choose a 2026 date**: Bhoonidhi refuses some 2021-2023 downloads, see README section 5), press **Run**. The dialog asks for your Bhoonidhi
user id and password, then for the code Bhoonidhi emails you; type it in the page and the run continues by itself with a
progress bar (about 140 MB). Say: "I only typed into this page and my mailbox; the server uses the password once to log in to Bhoonidhi and does not store it." Do this **once**, rehearsed,
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
- The instance serves four prepared scenes around Hyderabad (18 and 23 May, 2 and 9 Oct 2026) and has no Bhoonidhi
  credentials of its own; anyone can log in with their own account on the page to fetch more (the server sees that password
  while logging in, which is why the page says to use it only on an instance you trust). Fetching needs a person with a
  Bhoonidhi account (the emailed code). The page flow, the scene list by date and the downloads were verified with a real
  account on 2026-10-11/12.
- Bhoonidhi refuses some older (2021-2023) scene downloads with a "permission denied" page, for this account, from any address;
  the page says so and marks the scene. 2020 and 2026 scenes download.
- It is an unofficial service built on data from Bhoonidhi, not part of Bhoonidhi.
- Four indices (NDVI, NDWI, MNDWI, NDBI); no SAVI/EVI; no cloud masking, only a rough "cloud or haze may be present" warning
  on a layer; one scene per answer.

## If something goes wrong
| Symptom | Do this |
|---|---|
| The address times out or the page never loads | try another network such as a phone hotspot; check in the portal that the VM is Running |
| The page shows a grey map with no streets | the browser cannot reach OpenStreetMap tiles (no internet); the WPS still works, use the terminal steps |
| The page says it cannot reach the service | on the VM: `cd cgis-mid-assignment && sudo docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d` (SSH in with the key; see docs/AZURE.md) |
| Browser certificate warning | the VM's address changed or Caddy could not renew; check `sudo docker compose -f deploy/docker-compose.yml logs caddy` |
| "Permission denied" / "REFUSED this download" when fetching a scene | Bhoonidhi refuses that scene for the account: pick another date, preferably a 2026 one |
| A message about too many attempts or requests | wait: the service allows 10 login/code attempts a minute, Bhoonidhi 20 logins an hour per server address |
| Login says it is disabled | you are on the ngrok or laptop copy, where login is off by design |
| Async stays on `ProcessAccepted` | `sudo docker compose -f deploy/docker-compose.yml logs app` on the VM, then restart with `restart app` |
| Everything fails | use the ngrok fallback above, or a local copy: `http://localhost:5000` (identical behaviour) |
