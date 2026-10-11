"""Only WPS 1.0.0 is implemented, so that is all the service advertises and accepts; other versions get an OGC exception."""
import pytest
from lxml import etree

from app.server import flask_app

BBOX = "bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326"
OWS = "{http://www.opengis.net/ows/1.1}"


@pytest.fixture
def client():
    return flask_app.test_client()


def codes(response):
    doc = etree.fromstring(response.data)
    assert doc.tag.endswith("ExceptionReport")
    return [e.get("exceptionCode") for e in doc.iter(OWS + "Exception")]


def test_capabilities_advertise_only_version_1_0_0(client):
    doc = etree.fromstring(client.get("/wps?service=WPS&request=GetCapabilities").data)
    assert [v.text for v in doc.iter(OWS + "ServiceTypeVersion")] == ["1.0.0"]


def test_accepting_1_0_0_among_several_versions_still_works(client):
    r = client.get("/wps?service=WPS&request=GetCapabilities&AcceptVersions=2.0.0,1.0.0")
    assert r.status_code == 200 and b'version="1.0.0"' in r.data


def test_get_capabilities_asking_only_for_2_0_0_fails_negotiation(client):
    r = client.get("/wps?service=WPS&request=GetCapabilities&acceptversions=2.0.0")
    assert r.status_code == 400 and codes(r) == ["VersionNegotiationFailed"]


@pytest.mark.parametrize("operation", ["DescribeProcess&identifier=ndvi", f"Execute&identifier=ndvi&DataInputs={BBOX}"])
def test_other_operations_with_version_2_0_0_are_refused(client, operation):
    r = client.get(f"/wps?service=WPS&version=2.0.0&request={operation}")
    assert r.status_code == 400 and codes(r) == ["InvalidParameterValue"] and b'locator="version"' in r.data


def test_xml_post_requests_follow_the_same_rules(client):
    ns = 'xmlns:wps="http://www.opengis.net/wps/1.0.0" xmlns:ows="http://www.opengis.net/ows/1.1"'
    describe = f'<wps:DescribeProcess {ns} service="WPS" version="2.0.0"><ows:Identifier>ndvi</ows:Identifier></wps:DescribeProcess>'
    assert codes(client.post("/wps", data=describe, content_type="text/xml")) == ["InvalidParameterValue"]
    caps = (f'<wps:GetCapabilities {ns} service="WPS"><wps:AcceptVersions><ows:Version>2.0.0</ows:Version>'
            "</wps:AcceptVersions></wps:GetCapabilities>")
    assert codes(client.post("/wps", data=caps, content_type="text/xml")) == ["VersionNegotiationFailed"]
    ok = f'<wps:DescribeProcess {ns} service="WPS" version="1.0.0"><ows:Identifier>ndvi</ows:Identifier></wps:DescribeProcess>'
    assert client.post("/wps", data=ok, content_type="text/xml").status_code == 200


def test_requests_without_a_version_still_work(client):
    assert client.get("/wps?service=WPS&request=DescribeProcess&identifier=ndvi").status_code == 200
