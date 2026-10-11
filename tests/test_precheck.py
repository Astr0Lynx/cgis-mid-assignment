"""Reading a scene's small public metadata file before downloading it, so a tilted image that misses the area is skipped."""
import pytest

from app import live_fetch
from app.bhoonidhi_client import BhoonidhiClient, image_corners_geometry
from tests.live_helpers import CENTER, FakeBhoonidhi, feature, make_zip

COVERING = "R2323MAY2026078317010000060PSANSTUC00GTDF"  # 23 May: its true image covers the area
MISSING = "R2309OCT2026080292009900060PSANSTUC00GTDF"   # 9 Oct: the search says "covers", the real image does not
BBOX = (77.49, 17.49, 77.51, 17.51)


def corners(lon, lat, half):
    return {"UL": (lon - half, lat + half), "UR": (lon + half, lat + half), "LR": (lon + half, lat - half), "LL": (lon - half, lat - half)}


def portal_client(api):
    return BhoonidhiClient("student", "correct-horse", session=api, sleep=lambda s: None, download_auth="portal")


@pytest.fixture
def api(tmp_path):
    a = FakeBhoonidhi([feature(MISSING), feature(COVERING)],  # both footprints "cover" as far as the search can tell
                      archives={COVERING: make_zip(tmp_path, COVERING), MISSING: make_zip(tmp_path, MISSING)})
    a.meta_corners = {COVERING: corners(*CENTER, 0.5), MISSING: corners(78.9, 17.5, 0.3)}  # the newer one is really elsewhere
    return a


def downloads(api):
    return [path for _, path, _ in api.calls if path.startswith("/bhoonidhi/data/")]


def test_a_scene_whose_true_image_misses_the_area_is_skipped_without_being_downloaded(api, tmp_path):
    notes = []
    scene = live_fetch.ensure_scene(BBOX, None, tmp_path / "data", client=portal_client(api),
                                    progress=lambda m, p: notes.append(m))
    assert scene.scene_id == COVERING
    assert len(downloads(api)) == 1 and COVERING in downloads(api)[0] and not any(MISSING in d for d in downloads(api))
    assert "Checking scene footprints" in notes


def test_when_the_metadata_is_unavailable_the_candidate_is_kept_and_the_pixels_decide(api, tmp_path):
    api.meta_corners = {}  # every metadata request is a 404
    scene = live_fetch.ensure_scene(BBOX, None, tmp_path / "data", client=portal_client(api))
    assert scene.scene_id == MISSING  # newest first; its (synthetic) pixels do cover the area here
    assert len(downloads(api)) == 1


def test_only_the_portal_route_does_the_check(api, tmp_path):
    client = BhoonidhiClient("student", "correct-horse", session=api, sleep=lambda s: None, download_auth="bearer")
    kept = live_fetch.precheck(client, live_fetch.candidates(BBOX, None, client), BBOX, lambda m, p: None)
    assert [i.id for i in kept] == [MISSING, COVERING]  # untouched
    assert api.portal_search_bodies == []  # and no portal request was made


def test_every_candidate_missing_means_a_clear_error_and_no_download(api, tmp_path):
    from app.bhoonidhi_client import BhoonidhiError

    api.meta_corners = {COVERING: corners(79.5, 17.5, 0.1), MISSING: corners(78.9, 17.5, 0.3)}
    with pytest.raises(BhoonidhiError, match="No online Resourcesat LISS-III scene"):
        live_fetch.ensure_scene(BBOX, None, tmp_path / "data", client=portal_client(api))
    assert downloads(api) == []


REAL = {"ID": "R2309OCT2026080292009900060PSANSTUC00GTDF", "FILENAME": "R2309OCT2026080292009900060PSANSTUC00GTDF",
        "DIRPATH": "/imgarchive/PRODUCTJPGS//RS2/LIS3/2026/OCT/09/", "PRICED": "OpenData_DirectDownload", "TABLETYPE": "PMETA"}


def test_meta_url_from_the_real_record_and_its_gating():
    c = portal_client(FakeBhoonidhi())
    assert c.portal_meta_url(REAL) == ("https://bhoonidhi.nrsc.gov.in/imgarchive/PRODUCTJPGS//RS2/LIS3/2026/OCT/09/"
                                       "R2309OCT2026080292009900060PSANSTUC00GTDF.meta")
    assert c.portal_meta_url({**REAL, "PRICED": "Priced_Archive"}) is None  # only open data has a public metadata link
    assert c.portal_meta_url({**REAL, "TABLETYPE": "SMETA"}) is None
    assert c.portal_meta_url({**REAL, "DIRPATH": ""}) is None


def test_meta_fields_are_parsed_and_turned_into_a_polygon(api):
    c = portal_client(api)
    record = c.find_portal_record(feature_item(COVERING), BBOX)
    meta = c.fetch_portal_meta(record)
    geom = image_corners_geometry(meta)
    assert geom["type"] == "Polygon" and len(geom["coordinates"][0]) == 5
    assert c.fetch_portal_meta({**record, "PRICED": "Priced"}) is None


def test_incomplete_corner_metadata_gives_no_polygon():
    assert image_corners_geometry({"ImageULLat": "1"}) is None and image_corners_geometry({}) is None
    assert image_corners_geometry({f"Image{c}{k}": "x" for c in ("UL", "UR", "LR", "LL") for k in ("Lat", "Lon")}) is None


def feature_item(scene_id):
    from app.bhoonidhi_client import Item, date_from_id

    return Item(scene_id, "ResourceSat-2_LISS3_L2", date_from_id(scene_id), None, True)
