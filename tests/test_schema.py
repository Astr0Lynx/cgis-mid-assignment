"""Validate service responses against the official OGC WPS 1.0.0 / OWS 1.1 XSDs (tests/schemas)."""
from pathlib import Path

import pytest
from lxml import etree

from app.scene import DATA_DIR

SCHEMA = Path(__file__).parent / "schemas" / "wps" / "1.0.0" / "wpsAll.xsd"
KVP = "/wps?service=WPS&version=1.0.0&request="
BBOX = "bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326"
needs_data = pytest.mark.skipif(not list(DATA_DIR.glob("*/BAND_META.txt")), reason="no scenes in data/")


@pytest.fixture(scope="module")
def schema():
    return etree.XMLSchema(etree.parse(str(SCHEMA)))


@pytest.fixture(scope="module")
def client():
    from app.server import flask_app
    return flask_app.test_client()


def assert_valid(schema, data):
    doc = etree.fromstring(data)
    assert schema.validate(doc), "\n".join(f"line {e.line}: {e.message}" for e in list(schema.error_log)[:5])


def test_capabilities_valid(schema, client):
    assert_valid(schema, client.get("/wps?service=WPS&request=GetCapabilities").data)


def test_describe_process_valid(schema, client):
    assert_valid(schema, client.get(KVP + "DescribeProcess&identifier=ndvi").data)


def test_exception_report_valid(schema, client):
    r = client.get(KVP + "DescribeProcess&identifier=nope")
    assert r.status_code == 400
    assert_valid(schema, r.data)


@needs_data
def test_execute_response_valid(schema, client):
    assert_valid(schema, client.get(KVP + f"Execute&identifier=ndvi&DataInputs={BBOX}").data)


@needs_data
def test_execute_failure_response_valid(schema, client):
    assert_valid(schema, client.get(KVP + "Execute&identifier=ndvi&DataInputs=bbox=0,0,1,1,urn:ogc:def:crs:EPSG::4326").data)


@needs_data
def test_service_instance_url_not_double_escaped(client):
    doc = etree.fromstring(client.get(KVP + f"Execute&identifier=ndvi&DataInputs={BBOX}").data)
    url = doc.get("serviceInstance")
    assert "&amp;" not in url and "&service=WPS" in url


@needs_data
def test_lineage_response_valid(schema, client):
    r = client.get(KVP + f"Execute&identifier=ndvi&DataInputs={BBOX}&lineage=true")
    assert_valid(schema, r.data)
    root = etree.fromstring(r.data)
    ns = {"wps": "http://www.opengis.net/wps/1.0.0", "ows": "http://www.opengis.net/ows/1.1"}
    # lineage = the response echoes the inputs it was given and the outputs it was asked for
    assert root.find(".//wps:DataInputs/wps:Input[ows:Identifier='bbox']", ns) is not None
    assert root.find(".//wps:OutputDefinitions", ns) is not None


@needs_data
def test_unknown_output_exception_valid(schema, client):
    r = client.get(KVP + f"Execute&identifier=ndvi&DataInputs={BBOX}&RawDataOutput=nope")
    assert_valid(schema, r.data)


def test_spectral_index_describe_process_valid(schema, client):
    assert_valid(schema, client.get(KVP + "DescribeProcess&identifier=spectral-index").data)


@needs_data
def test_spectral_index_execute_valid(schema, client):
    assert_valid(schema, client.get(KVP + f"Execute&identifier=spectral-index&DataInputs={BBOX};index=ndwi").data)
