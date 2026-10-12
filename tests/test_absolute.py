"""Absolute colour scale and the reflectance option: both exist end to end, without inventing any solar-irradiance numbers."""
import json

import numpy as np
import pytest
from rasterio.transform import from_origin

from app import radiometry
from app.preview import render_preview
from tests.live_helpers import FakeBhoonidhi, feature, make_zip
from tests.test_live_mode import EXEC, SCENE, server, turn_on  # noqa: F401  (server is a fixture)

TRANSFORM = from_origin(500000, 1900000, 24, 24)


def preview(data, scale):
    from rasterio.crs import CRS

    return render_preview(data, TRANSFORM, CRS.from_epsg(32644), "ndvi", scale)


def test_stretch_follows_the_data_and_absolute_is_fixed():
    low = np.linspace(0.10, 0.20, 400, dtype="float32").reshape(20, 20)
    high = np.linspace(0.60, 0.70, 400, dtype="float32").reshape(20, 20)
    s_low, s_high = preview(low, "stretch"), preview(high, "stretch")
    assert s_low.vmin != s_high.vmin                              # relative: each result fills the ramp
    a_low, a_high = preview(low, "absolute"), preview(high, "absolute")
    assert (a_low.vmin, a_low.vmax) == (a_high.vmin, a_high.vmax) == (-1.0, 1.0)
    assert a_low.png != a_high.png                                 # different values, different colours


def test_absolute_colours_do_not_change_the_raster_values_or_default_behaviour(server):
    client, data, tmp, mp = server
    turn_on(mp, FakeBhoonidhi([feature(SCENE)], archives={SCENE: make_zip(tmp, SCENE)}))
    default = json.loads(client.get(EXEC + "&RawDataOutput=stats").data)
    absolute = json.loads(client.get(EXEC + ";colour_scale=absolute&RawDataOutput=stats").data)
    assert default["preview"]["scale"] == "stretch" and absolute["preview"]["scale"] == "absolute"
    assert (absolute["preview"]["vmin"], absolute["preview"]["vmax"]) == (-1.0, 1.0)
    assert (absolute["min"], absolute["mean"], absolute["max"]) == (default["min"], default["mean"], default["max"])


def test_unknown_colour_scale_is_rejected(server):
    client, *_ = server
    assert b"ExceptionReport" in client.get(EXEC + ";colour_scale=rainbow").data


# ---- reflectance: usable the moment a cited table exists ---------------------------------------------------------------------
def write_table(path, source="TEST-ONLY synthetic table, not a real calibration"):
    path.write_text(json.dumps({"source": source, "units": "mW/cm2/um", "bands": {"2": 1.0, "3": 1.0, "4": 1.0, "5": 1.0}}))
    return path


def test_toa_is_unavailable_without_a_cited_table_and_works_with_one(server, tmp_path_factory):
    client, data, tmp, mp = server
    turn_on(mp, FakeBhoonidhi([feature(SCENE)], archives={SCENE: make_zip(tmp, SCENE)}))
    assert radiometry.toa_available() is False and client.get("/api/info").get_json()["toa_available"] is False
    assert b"ESUN" in client.get(EXEC + ";radiometry=toa").data            # refused, with the explanation

    table = write_table(tmp_path_factory.mktemp("esun") / "esun.json")
    mp.setattr(radiometry, "ESUN_FILE", table)
    assert radiometry.toa_available() is True and client.get("/api/info").get_json()["toa_available"] is True
    stats = json.loads(client.get(EXEC + ";radiometry=toa&RawDataOutput=stats").data)
    assert stats["radiometry"] == "toa" and -1.0 <= stats["min"] <= stats["max"] <= 1.0


def test_a_table_without_a_source_is_still_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(radiometry, "ESUN_FILE", write_table(tmp_path / "e.json", source=None))
    assert radiometry.toa_available() is False
