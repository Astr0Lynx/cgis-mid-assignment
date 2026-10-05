"""Flask wrapper serving the PyWPS service at /wps and async outputs at /outputs."""
import os
from pathlib import Path

import app  # noqa: F401  (pins PROJ/GDAL data first)
from flask import Flask, send_from_directory
from pywps import Service, configuration

import app.pywps_compat  # noqa: F401  (bbox axis order, see module docstring)
from app.processes.ndvi import Ndvi

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:5000").rstrip("/")

OUT_DIR = ROOT / "outputs"
WORK_DIR = ROOT / "workdir"
OUT_DIR.mkdir(exist_ok=True)
WORK_DIR.mkdir(exist_ok=True)

service = Service([Ndvi()], cfgfiles=[str(ROOT / "pywps.cfg")])
configuration.CONFIG.set("server", "url", f"{BASE_URL}/wps")
configuration.CONFIG.set("server", "outputurl", f"{BASE_URL}/outputs/")
configuration.CONFIG.set("server", "outputpath", str(OUT_DIR))
configuration.CONFIG.set("server", "workdir", str(WORK_DIR))

flask_app = Flask(__name__)


@flask_app.route("/wps", methods=["GET", "POST"])
def wps():
    return service


@flask_app.route("/outputs/<path:filename>")
def outputs(filename):
    # async status documents and result files; mimetype inferred from the extension
    return send_from_directory(OUT_DIR, filename)


@flask_app.route("/")
def index():
    return (
        "<h1>Bhoonidhi WPS</h1>"
        f'<p><a href="{BASE_URL}/wps?service=WPS&request=GetCapabilities">GetCapabilities</a></p>'
        f'<p><a href="{BASE_URL}/wps?service=WPS&version=1.0.0&request=DescribeProcess&identifier=ndvi">DescribeProcess ndvi</a></p>'
    )


if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=5000)
