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
    assert inputs == ["bbox", "date", "radiometry"]
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


# --- response options: RawDataOutput, ResponseDocument, lineage ----------------------------------

EXEC = "/wps?service=WPS&version=1.0.0&request=Execute&identifier=ndvi&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326"
RAW_TEMPLATE = POST_TEMPLATE.replace(
    "</wps:DataInputs>",
    "</wps:DataInputs><wps:ResponseForm><wps:RawDataOutput><ows:Identifier>{out}</ows:Identifier></wps:RawDataOutput></wps:ResponseForm>",
)


@needs_data
def test_raw_stats_returns_json_not_xml(client):
    r = client.get(EXEC + "&RawDataOutput=stats")
    assert r.status_code == 200 and r.headers["Content-Type"].startswith("application/json")
    assert json.loads(r.data)["date"] == "2026-05-23"


@needs_data
def test_raw_ndvi_returns_the_geotiff_itself(client):
    import numpy as np
    import rasterio

    r = client.get(EXEC + "&RawDataOutput=ndvi")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "image/tiff; subtype=geotiff"
    with rasterio.MemoryFile(r.data) as mf, mf.open() as ds:
        assert ds.dtypes[0] == "float32" and ds.crs.to_epsg() == 32644
        assert np.isfinite(ds.read(1)).mean() > 0.95


@needs_data
def test_raw_over_post(client):
    body = RAW_TEMPLATE.format(crs="urn:ogc:def:crs:EPSG::4326", lc="17.35 78.40", uc="17.45 78.55", out="stats")
    r = client.post("/wps", data=body, content_type="text/xml")
    assert r.status_code == 200
    assert json.loads(r.data)["date"] == "2026-05-23"


@needs_data
def test_reference_url_is_named_after_the_output(client):
    root = ET.fromstring(client.get(EXEC).data)
    ref = root.find(".//wps:Output[ows:Identifier='ndvi']//wps:Reference", NS)
    assert ref.get("href").endswith("/ndvi.tif")  # not PyWPS's default "input.tif"


@pytest.mark.parametrize("param", ["RawDataOutput", "ResponseDocument"])
def test_unknown_output_is_an_ogc_exception_not_a_500(client, param):
    r = client.get(EXEC + f"&{param}=nope")
    assert r.status_code == 400
    assert b"ExceptionReport" in r.data and b"InvalidParameterValue" in r.data
    assert b"ndvi, stats" in r.data  # tells the client what is available


def test_unknown_output_over_post(client):
    body = RAW_TEMPLATE.format(crs="urn:ogc:def:crs:EPSG::4326", lc="17.35 78.40", uc="17.45 78.55", out="nope")
    r = client.post("/wps", data=body, content_type="text/xml")
    assert r.status_code == 400 and b"InvalidParameterValue" in r.data


@needs_data
def test_radiometry_default_and_explicit_radiance(client):
    for suffix in ("", ";radiometry=radiance"):
        r = client.get(EXEC + suffix)
        assert b"ProcessSucceeded" in r.data
    stats = json.loads(client.get(EXEC + ";radiometry=radiance&RawDataOutput=stats").data)
    assert stats["radiometry"] == "radiance"


@needs_data
def test_toa_refused_with_clear_exception_until_esun_configured(client):
    r = client.get(EXEC + ";radiometry=toa")
    assert b"ProcessFailed" in r.data or b"ExceptionReport" in r.data
    assert b"ESUN" in r.data and b"radiometry=radiance" in r.data


def test_invalid_radiometry_value_rejected(client):
    r = client.get(EXEC + ";radiometry=foo")
    assert r.status_code == 400 and b"InvalidParameterValue" in r.data


# --- spectral-index process ------------------------------------------------------------------------
SI = "/wps?service=WPS&version=1.0.0&request=Execute&identifier=spectral-index&DataInputs=bbox=17.35,78.40,17.45,78.55,urn:ogc:def:crs:EPSG::4326"


def test_both_processes_advertised(client):
    root = ET.fromstring(client.get("/wps?service=WPS&request=GetCapabilities").data)
    ids = [e.text for e in root.findall(".//wps:Process/ows:Identifier", NS)]
    assert ids == ["ndvi", "spectral-index"]


def test_spectral_index_describes_allowed_indices(client):
    r = client.get("/wps?service=WPS&version=1.0.0&request=DescribeProcess&identifier=spectral-index")
    root = ET.fromstring(r.data)
    inp = {e.find("ows:Identifier", NS).text: e for e in root.findall(".//Input")}
    assert list(inp) == ["bbox", "index", "date", "radiometry"]
    assert inp["index"].get("minOccurs") == "1"
    values = [v.text for v in inp["index"].findall(".//ows:Value", NS)]
    assert values == ["ndvi", "ndwi", "mndwi", "ndbi"]


@needs_data
@pytest.mark.parametrize("name", ["ndvi", "ndwi", "mndwi", "ndbi"])
def test_spectral_index_executes_each_index(client, name):
    stats = json.loads(client.get(SI + f";index={name}&RawDataOutput=stats").data)
    assert stats["index"] == name and stats["date"] == "2026-05-23"
    assert -1 <= stats["min"] <= stats["max"] <= 1 and stats["valid_fraction"] > 0.95


@needs_data
def test_spectral_index_matches_the_ndvi_process(client):
    a = client.get(SI + ";index=ndvi&RawDataOutput=index").data
    b = client.get(EXEC + "&RawDataOutput=ndvi").data
    assert a == b  # same code path, byte-identical raster


@needs_data
def test_spectral_index_output_is_named_index(client):
    root = ET.fromstring(client.get(SI + ";index=ndwi").data)
    ref = root.find(".//wps:Output[ows:Identifier='index']//wps:Reference", NS)
    assert ref.get("href").endswith("/index.tif")


def test_spectral_index_rejects_unknown_and_missing_index(client):
    assert client.get(SI + ";index=savi").status_code == 400  # not offered (needs reflectance)
    assert client.get(SI).status_code == 400  # index is required


def test_service_identifies_itself_not_as_pywps_defaults(client):
    text = client.get("/wps?service=WPS&request=GetCapabilities").data.decode()
    assert "Bhoonidhi Spectral Index WPS" in text
    for placeholder in ("PyWPS Processing Service", "Lastname, Firstname", "Mailing Address", "+xx-xxx-xxx-xxxx"):
        assert placeholder not in text
