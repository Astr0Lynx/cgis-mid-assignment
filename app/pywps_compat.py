"""Fixes for PyWPS 4.8 behaviours that break OGC conformance.

1. Bounding-box axis order (XML POST vs KVP).
2. Failed-execution template (see `_PatchedLoader`).

--- 1. Make XML-POST and KVP bounding boxes arrive in the same axis order.

PyWPS 4.8 passes KVP bbox values through untouched (OGC order: lat,lon for EPSG:4326),
but for XML POST it parses LowerCorner/UpperCorner through OWSLib, which swaps them to
x,y (lon,lat) for lat/lon CRSs. A client following the OGC convention would get a
different area depending on the request encoding. This wraps the XML parser so POST
also yields the raw corner order from the document, matching KVP.
"""
from collections import defaultdict

import re
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


# --- 2. Execute response template -------------------------------------------------------
# PyWPS 4.8's execute/main.xml (a) wraps the error of a failed process in <wps:ExceptionReport>,
# but the WPS 1.0.0 schema requires <ows:ExceptionReport>, and (b) HTML-escapes serviceInstance,
# which PyWPS already escaped, producing "&amp;amp;" so the advertised URL is wrong.
# Patch the template text as it is loaded, so this survives reinstalls and Docker rebuilds.
from jinja2 import PackageLoader  # noqa: E402

_response_basic = importlib.import_module("pywps.response.basic")


# (text in PyWPS's execute/main.xml, replacement). Each pair fixes one schema violation.
_EXECUTE_TEMPLATE_FIXES = [
    # failed process: the error must be an ows:ExceptionReport with a version attribute
    ("<wps:ExceptionReport>", '<ows:ExceptionReport version="1.0.0">'),
    ("</wps:ExceptionReport>", "</ows:ExceptionReport>"),
    (' locator="None"', ""),
    # only ProcessStarted/Paused carry percentCompleted in the 1.0.0 schema
    ('<wps:ProcessAccepted percentCompleted="{{ status.percent_done }}">', "<wps:ProcessAccepted>"),
    # do not escape serviceInstance a second time
    ("{{ service_instance }}", "{{ service_instance|safe }}"),
    # lineage echo: xs:boolean needs lowercase true/false
    ('asReference="{{ output.asreference }}"', """asReference="{{ 'true' if output.asreference else 'false' }}\""""),
    # lineage echo of a KVP bbox carries no crs/dimensions (printed as "None"); only EPSG:4326 2-D is supported
    ('<wps:BoundingBoxData crs="{{ input.crs }}" dimensions="{{ input.dimensions }}">',
     """<wps:BoundingBoxData crs="{{ input.crs or 'urn:ogc:def:crs:EPSG::4326' }}" dimensions="{{ input.dimensions or 2 }}">"""),
]


class _PatchedLoader(PackageLoader):
    def get_source(self, environment, template):
        source, filename, uptodate = super().get_source(environment, template)
        if template.endswith("1.0.0/capabilities/main.xml"):
            # PyWPS also advertises 2.0.0, but only its 1.0.0 documents are implemented: say so honestly
            source = re.sub(r"[ \t]*<ows:ServiceTypeVersion>2\.0\.0</ows:ServiceTypeVersion>\s*\n", "", source)
        if template.endswith("execute/main.xml"):
            for old, new in _EXECUTE_TEMPLATE_FIXES:
                source = source.replace(old, new)
        return source, filename, uptodate


_response_basic.PackageLoader = _PatchedLoader
