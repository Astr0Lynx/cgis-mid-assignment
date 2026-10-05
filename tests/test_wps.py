import json
import xml.etree.ElementTree as ET

import pytest

from app.scene import DATA_DIR

NS = {"wps": "http://www.opengis.net/wps/1.0.0", "ows": "http://www.opengis.net/ows/1.1"}
needs_data = pytest.mark.skipif(not list(DATA_DIR.glob("*/BAND_META.txt")), reason="no scenes in data/")


@pytest.fixture(scope="module")
def client():
    from app.server import flask_app
    return flask_app.test_client()


def test_get_capabilities(client):
    r = client.get("/wps?service=WPS&request=GetCapabilities")
    assert r.status_code == 200
    root = ET.fromstring(r.data)
    assert root.tag.endswith("Capabilities")
    ids = [e.text for e in root.findall(".//ows:Identifier", NS)]
    assert "ndvi" in ids


def test_describe_process(client):
    r = client.get("/wps?service=WPS&version=1.0.0&request=DescribeProcess&identifier=ndvi")
    assert r.status_code == 200
    root = ET.fromstring(r.data)
    inputs = [e.find("ows:Identifier", NS).text for e in root.findall(".//Input")]
    assert inputs == ["bbox", "date"]
    assert b"image/tiff; subtype=geotiff" in r.data


def test_unknown_process_is_exception(client):
    r = client.get("/wps?service=WPS&version=1.0.0&request=DescribeProcess&identifier=nope")
    assert r.status_code == 400
    assert b"ExceptionReport" in r.data


def execute(client, bbox, date=None):
    di =f"bbox={bbox}" + (f";date={date}" if date else "")
    return client.get(f"/wps?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs={di}")


@needs_data
def test_execute_hyderabad(client):
    # EPSG:4326 axis order: miny,minx,maxy,maxx
    r = execute(client, "17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326")
    assert r.status_code == 200, r.data[:500]
    root = ET.fromstring(r.data)
    assert root.find(".//wps:ProcessSucceeded", NS) is not None
    stats = json.loads(root.find(".//wps:Output[ows:Identifier='stats']//wps:ComplexData", NS).text)
    assert stats["date"] == "2026-05-23"
    assert -1 <= stats["min"] <= stats["max"] <= 1
    assert stats["valid_fraction"] > 0.95


@needs_data
def test_execute_no_scene_is_exception(client):
    r = execute(client, "0,0,1,1,urn:ogc:def:crs:EPSG::4326")
    assert b"ExceptionReport" in r.data or b"ProcessFailed" in r.data


POST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<wps:Execute service="WPS" version="1.0.0" xmlns:wps="http://www.opengis.net/wps/1.0.0" xmlns:ows="http://www.opengis.net/ows/1.1">
<ows:Identifier>ndvi</ows:Identifier><wps:DataInputs><wps:Input><ows:Identifier>bbox</ows:Identifier>
<wps:Data><wps:BoundingBoxData crs="{crs}" dimensions="2"><ows:LowerCorner>{lc}</ows:LowerCorner><ows:UpperCorner>{uc}</ows:UpperCorner></wps:BoundingBoxData></wps:Data>
</wps:Input></wps:DataInputs></wps:Execute>"""


@needs_data
@pytest.mark.parametrize("crs", ["urn:ogc:def:crs:EPSG::4326", "EPSG:4326"])
def test_post_bbox_axis_order_matches_kvp(client, crs):
    """Regression: PyWPS swaps POST corners to lon,lat; app.pywps_compat must undo that."""
    body = POST_TEMPLATE.format(crs=crs, lc="17.35 78.40", uc="17.45 78.55")
    r = client.post("/wps", data=body, content_type="text/xml")
    assert b"ProcessSucceeded" in r.data, r.data[:400]


@needs_data
def test_date_selects_scene_and_rejects_bad_values(client):
    bbox = "17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326"
    ok = execute(client, bbox, date="2026-05-23")
    assert b"ProcessSucceeded" in ok.data
    # the 18 May scene does not fully cover this window, so asking for it must fail cleanly
    assert b"ExceptionReport" in execute(client, bbox, date="2026-05-18").data or \
        b"ProcessFailed" in execute(client, bbox, date="2026-05-18").data
    assert b"YYYY-MM-DD" in execute(client, bbox, date="18/05/2026").data


def test_oversized_bbox_rejected(client):
    r = execute(client, "10,70,30,90,urn:ogc:def:crs:EPSG::4326")
    assert b"bbox too large" in r.data


def test_inverted_bbox_rejected(client):
    r = execute(client, "17.45,78.55,17.35,78.40,urn:ogc:def:crs:EPSG::4326")
    assert b"Invalid bbox" in r.data
