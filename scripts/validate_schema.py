"""Validate a running WPS against the official WPS 1.0.0 / OWS 1.1 XSDs, including the async lifecycle
(ProcessAccepted -> ProcessSucceeded, and an async job that fails -> ProcessFailed).
Usage: python scripts/validate_schema.py [wps_url]"""
import sys
import time
from pathlib import Path

import requests
from lxml import etree

url = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5000/wps").rstrip("/")
schema = etree.XMLSchema(etree.parse(str(Path(__file__).resolve().parent.parent / "tests/schemas/wps/1.0.0/wpsAll.xsd")))
KVP = f"{url}?service=WPS&version=1.0.0&request="
OK_BBOX = "bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326"
BIG_BBOX = "bbox=10,70,30,90,urn:ogc:def:crs:EPSG::4326"
fails = 0


def check(name, text):
    global fails
    valid = schema.validate(etree.fromstring(text.encode() if isinstance(text, str) else text))
    print(f"{'VALID  ' if valid else 'INVALID'} {name}")
    if not valid:
        fails += 1
        for e in list(schema.error_log)[:3]:
            print(f"        line {e.line}: {e.message[:200]}")


def status_of(doc):
    root = etree.fromstring(doc.encode())
    ns = {"wps": "http://www.opengis.net/wps/1.0.0"}
    return root.get("statusLocation"), [e.tag.split("}")[1] for e in root.find("wps:Status", ns)]


def run_async(label, bbox):
    r = requests.get(KVP + f"Execute&identifier=ndvi&DataInputs={bbox}&storeExecuteResponse=true&status=true", timeout=60)
    check(f"{label}: async Execute response", r.text)
    loc, state = status_of(r.text)
    print(f"        accepted -> {state}")
    for _ in range(30):
        time.sleep(2)
        s = requests.get(loc, timeout=30).text
        state = status_of(s)[1]
        if state[0] in ("ProcessSucceeded", "ProcessFailed"):
            break
    check(f"{label}: final status document ({state[0]})", s)


check("GetCapabilities", requests.get(f"{url}?service=WPS&request=GetCapabilities", timeout=30).text)
check("DescribeProcess", requests.get(KVP + "DescribeProcess&identifier=ndvi", timeout=30).text)
check("Execute (sync)", requests.get(KVP + f"Execute&identifier=ndvi&DataInputs={OK_BBOX}", timeout=60).text)
check("DescribeProcess (spectral-index)", requests.get(KVP + "DescribeProcess&identifier=spectral-index", timeout=30).text)
check("Execute (spectral-index, ndwi)", requests.get(KVP + f"Execute&identifier=spectral-index&DataInputs={OK_BBOX};index=ndwi", timeout=60).text)
check("ExceptionReport (unknown process)", requests.get(KVP + "DescribeProcess&identifier=nope", timeout=30).text)
run_async("success", OK_BBOX)
run_async("failure", BIG_BBOX)
sys.exit(1 if fails else 0)
