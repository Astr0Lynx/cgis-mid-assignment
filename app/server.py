"""Flask wrapper serving the PyWPS service at /wps and async outputs at /outputs."""
import io
import os
from pathlib import Path

import app  # noqa: F401  (pins PROJ/GDAL data first)
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory
from lxml import etree
from pywps import Service, configuration
from pywps.exceptions import InvalidParameterValue

import app.pywps_compat  # noqa: F401  (bbox axis order, see module docstring)
from app.cleanup import start_background_purge
from app.processes.ndvi import Ndvi, SpectralIndex

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:5000").rstrip("/")

OUT_DIR = ROOT / "outputs"
WORK_DIR = ROOT / "workdir"
OUT_DIR.mkdir(exist_ok=True)
WORK_DIR.mkdir(exist_ok=True)

# Results pile up in outputs/ (a status document and a folder per stored job); delete old ones. Set
# OUTPUT_TTL_HOURS=0 to keep everything.
OUTPUT_TTL_HOURS = float(os.environ.get("OUTPUT_TTL_HOURS", 24))
if OUTPUT_TTL_HOURS > 0:
    start_background_purge(OUT_DIR, OUTPUT_TTL_HOURS * 3600, interval_s=float(os.environ.get("OUTPUT_PURGE_MINUTES", 30)) * 60)

service = Service([Ndvi(), SpectralIndex()], cfgfiles=[str(ROOT / "pywps.cfg")])
configuration.CONFIG.set("server", "url", f"{BASE_URL}/wps")
configuration.CONFIG.set("server", "outputurl", f"{BASE_URL}/outputs/")
configuration.CONFIG.set("server", "outputpath", str(OUT_DIR))
configuration.CONFIG.set("server", "workdir", str(WORK_DIR))

# PyWPS tracks running/queued jobs in a database. Its default is in-memory, which is private to each
# process: async jobs run in forked children, so the parent never sees them finish, its "running"
# counter only grows, and once it reaches `parallelprocesses` every new async job is queued forever.
# A file database is shared between parent and children. Start from a clean one so a crash cannot
# leave jobs marked as running (this app runs a single gunicorn worker).
STATE_DIR = ROOT / "state"
STATE_DIR.mkdir(exist_ok=True)
JOBS_DB = STATE_DIR / "pywps-jobs.sqlite3"
JOBS_DB.unlink(missing_ok=True)
configuration.CONFIG.set("logging", "database", f"sqlite:///{JOBS_DB.as_posix()}")

flask_app = Flask(__name__)


def _requested_outputs():
    """Output identifiers an Execute request asks for (via RawDataOutput / ResponseDocument).

    Returns (process identifier, [(parameter name, output identifier), ...]); handles KVP and XML POST.
    PyWPS itself raises a KeyError (HTTP 500, HTML page) for an unknown one; we turn that into an
    OGC ExceptionReport before PyWPS sees the request.
    """
    process, wanted = None, []
    if request.method == "GET":
        if request.args.get("request", "").lower() != "execute":
            return None, []
        process = request.args.get("identifier")
        for param in ("RawDataOutput", "ResponseDocument"):
            for item in request.args.get(param, "").split(","):
                if item.strip():
                    wanted.append((param, item.split("@")[0].strip()))
    else:
        # PyWPS builds its own request from the raw WSGI environ, so reading the body here would leave it
        # empty for PyWPS; read it once and put an identical stream back.
        body = request.get_data()
        request.environ["wsgi.input"] = io.BytesIO(body)
        try:
            doc = etree.fromstring(body)
        except etree.XMLSyntaxError:
            return None, []  # let PyWPS report the malformed request itself
        if not doc.tag.endswith("}Execute"):
            return None, []
        ident = doc.xpath("*[local-name()='Identifier']")
        process = ident[0].text if ident else None
        for param in ("RawDataOutput", "ResponseDocument"):
            for node in doc.xpath(f".//*[local-name()='{param}']//*[local-name()='Identifier']"):
                wanted.append((param, (node.text or "").strip()))
    return process, wanted


@flask_app.route("/wps", methods=["GET", "POST"])
def wps():
    process, wanted = _requested_outputs()
    known = {o.identifier for p in service.processes.values() if p.identifier == process for o in p.outputs}
    for param, identifier in wanted:
        if known and identifier not in known:
            return InvalidParameterValue(
                f"Unknown output {identifier!r}; process {process!r} offers: {', '.join(sorted(known))}", param)
    return service


@flask_app.route("/outputs/<path:filename>")
def outputs(filename):
    # async status documents and result files; mimetype inferred from the extension
    return send_from_directory(OUT_DIR, filename)


STATIC_DIR = Path(__file__).resolve().parent / "static"


@flask_app.route("/")
def index():
    """The interactive map page: a client of the WPS at /wps (everything it does is an ordinary WPS request)."""
    return send_from_directory(STATIC_DIR, "index.html")


@flask_app.route("/api/info")
def api_info():
    """What the page needs to know about the service: the indices it offers and its limits."""
    from app.indices import INDICES
    from app.processes.ndvi import MAX_PIXELS

    return jsonify({
        "indices": [{"name": i.name, "title": i.title, "formula": i.formula, "meaning": i.meaning}
                    for i in INDICES.values()],
        "max_pixels": MAX_PIXELS, "resolution_m": 24, "radiometry": "radiance",
    })


@flask_app.route("/api/coverage")
def api_coverage():
    """Footprints of the scenes this instance has prepared (true image corners, so tilted edges are visible)."""
    from app.radiometry import parse_band_meta
    from app.scene import DATA_DIR

    features = []
    for meta_file in sorted(Path(DATA_DIR).glob("*/BAND_META.txt")):
        try:
            m = parse_band_meta(meta_file)
            corners = [(float(m[f"Image{c}Lon"]), float(m[f"Image{c}Lat"])) for c in ("UL", "UR", "LR", "LL")]
            when = datetime.strptime(m["DateOfPass"], "%d-%b-%Y").date().isoformat()
        except (KeyError, ValueError):
            continue  # a folder without usable metadata is simply not listed
        features.append({"type": "Feature",
                         "geometry": {"type": "Polygon", "coordinates": [[*corners, corners[0]]]},
                         "properties": {"id": m.get("OTSProductID", meta_file.parent.name), "date": when,
                                        "satellite": m.get("SatID"), "sensor": m.get("Sensor")}})
    features.sort(key=lambda f: f["properties"]["date"], reverse=True)
    return jsonify({"type": "FeatureCollection", "features": features})


if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=5000)
