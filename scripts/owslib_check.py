"""Independent client check: drive the WPS with OWSLib (GetCapabilities, DescribeProcess,
async Execute, status polling). Usage: python scripts/owslib_check.py [wps_url]

Polling is bounded (TIMEOUT_S, one poll per POLL_S). OWSLib's own monitorExecution() loops forever and,
if a job's status document disappears, hammers the server with no delay; never use it unbounded."""
import sys
import time

from owslib.wps import BoundingBoxDataInput, WebProcessingService

TIMEOUT_S = 90
POLL_S = 2

url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5000/wps"
w = WebProcessingService(url, version="1.0.0")
print("service:", w.identification.title, w.identification.version)
print("processes:", [p.identifier for p in w.processes])

p = w.describeprocess("ndvi")
print("store/status supported:", p.storeSupported, p.statusSupported)
for i in p.dataInputs:
    print("  input :", i.identifier, i.dataType, "min", i.minOccurs)
for o in p.processOutputs:
    print("  output:", o.identifier, [f.mimeType for f in o.supportedValues])

# EPSG:4326 axis order per OGC: lat,lon -> miny,minx,maxy,maxx
bbox = BoundingBoxDataInput([17.35, 78.40, 17.45, 78.55], crs="urn:ogc:def:crs:EPSG::4326")
ex = w.execute("ndvi", [("bbox", bbox)], output=[("ndvi", True), ("stats", False)], mode="async")

deadline = time.monotonic() + TIMEOUT_S
while not ex.isComplete() and time.monotonic() < deadline:
    time.sleep(POLL_S)
    try:
        ex.checkStatus(sleepSecs=0)
    except Exception as e:  # a vanished/unparseable status document: report and stop, do not spin
        print("status poll failed:", type(e).__name__, str(e)[:120])
        break

print("status:", ex.status, "| succeeded:", ex.isSucceded())
if not ex.isComplete():
    print(f"gave up after {TIMEOUT_S}s")
for o in ex.processOutputs:
    print("  ->", o.identifier, o.mimeType, o.reference or (o.data[0][:150] if o.data else None))
sys.exit(0 if ex.isSucceded() else 1)
