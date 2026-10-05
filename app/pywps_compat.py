"""Make XML-POST and KVP bounding boxes arrive in the same axis order.

PyWPS 4.8 passes KVP bbox values through untouched (OGC order: lat,lon for EPSG:4326),
but for XML POST it parses LowerCorner/UpperCorner through OWSLib, which swaps them to
x,y (lon,lat) for lat/lon CRSs. A client following the OGC convention would get a
different area depending on the request encoding. This wraps the XML parser so POST
also yields the raw corner order from the document, matching KVP.
"""
from collections import defaultdict

import importlib

# pywps.app re-exports the WPSRequest *class* under the module's name, so import the module explicitly
_wpsrequest = importlib.import_module("pywps.app.WPSRequest")

_original = _wpsrequest.get_inputs_from_xml


def _local(name):
    return f"*[local-name()='{name}']"


def get_inputs_from_xml(doc):
    inputs = _original(doc)

    raw = defaultdict(list)
    for bbox in doc.xpath(f"//{_local('Input')}/{_local('Data')}/{_local('BoundingBoxData')}"):
        identifier = bbox.xpath(f"../../{_local('Identifier')}")[0].text
        lower = bbox.xpath(f"{_local('LowerCorner')}")[0].text.split()
        upper = bbox.xpath(f"{_local('UpperCorner')}")[0].text.split()
        raw[identifier].append([float(v) for v in lower + upper])

    for identifier, corners in raw.items():
        bbox_inputs = [i for i in inputs.get(identifier, []) if "dimensions" in i]
        for inpt, data in zip(bbox_inputs, corners):
            inpt["data"] = data
    return inputs


_wpsrequest.get_inputs_from_xml = get_inputs_from_xml
