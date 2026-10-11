"""The Bhoonidhi EULA asks for the caption "ISRO-IRS" on the data and what is derived from it: outputs carry it."""
import json
from datetime import datetime

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from app.scene import ATTRIBUTION, Clip, write_cog
from tests.live_helpers import FakeBhoonidhi, feature, make_zip
from tests.test_live_mode import EXEC, SCENE, server, turn_on  # noqa: F401  (server is a fixture)


def test_the_output_geotiff_carries_the_attribution_and_its_source(tmp_path):
    clip = Clip(np.linspace(-0.5, 0.5, 64, dtype="float32").reshape(8, 8), from_origin(500000, 1900000, 24, 24),
                CRS.from_epsg(32644), "R2323MAY2026078317010000060PSANSTUC00GTDF", datetime(2026, 5, 23))
    out = write_cog(clip, tmp_path / "x.tif")
    with rasterio.open(out) as ds:
        tags = ds.tags()
    assert ATTRIBUTION == "ISRO-IRS"
    assert tags["ATTRIBUTION"] == "ISRO-IRS" and "Bhoonidhi" in tags["SOURCE"] and tags["SCENE_ID"] == clip.scene_id


def test_the_statistics_document_names_the_attribution(server):
    client, data, tmp, mp = server
    api = FakeBhoonidhi([feature(SCENE)], archives={SCENE: make_zip(tmp, SCENE)})
    turn_on(mp, api)
    r = client.get(EXEC + "&RawDataOutput=stats")
    assert r.status_code == 200 and json.loads(r.data)["attribution"] == "ISRO-IRS"
