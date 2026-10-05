"""Independent client check: drive the WPS with OWSLib (GetCapabilities, DescribeProcess,
async Execute, status polling). Usage: python scripts/owslib_check.py [wps_url]"""
import sys

from owslib.wps import BoundingBoxDataInput, WebProcessingService, monitorExecution

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
monitorExecution(ex, sleepSecs=2)
print("status:", ex.status, "| succeeded:", ex.isSucceded())
for o in ex.processOutputs:
    print("  ->", o.identifier, o.mimeType, o.reference or (o.data[0][:150] if o.data else None))
sys.exit(0 if ex.isSucceded() else 1)
