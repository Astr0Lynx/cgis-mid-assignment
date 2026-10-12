"""A rough 'cloud or haze may be present' hint from green-band brightness. It must say 'may', and must never break a result."""
import json

import numpy as np
import pytest

from app import clouds
from app.scene import Scene
from tests.live_helpers import FakeBhoonidhi, feature, make_zip
from tests.test_live_mode import EXEC, SCENE, server, turn_on  # noqa: F401  (server is a fixture)


def clip(bright_share, n=1000):
    data = np.full(n, 5.0, dtype="float32")                 # clear land: a few mW/cm2/sr/um
    data[: int(n * bright_share)] = 25.0                    # cloud-like
    return data.reshape(10, -1)


@pytest.mark.parametrize("share, level", [(0.0, "none"), (0.05, "none"), (0.10, "some"), (0.30, "some"), (0.50, "much"), (0.94, "much")])
def test_levels_follow_the_share_of_very_bright_pixels(share, level):
    got = clouds.assess(clip(share))
    assert got["level"] == level and got["bright_share"] == pytest.approx(share, abs=0.005)


def test_the_hint_describes_itself_as_a_hint_not_a_mask():
    note = clouds.assess(clip(0.5))["note"]
    assert "may" in note and "not a cloud mask" in note


def test_background_pixels_are_ignored_and_an_empty_clip_is_none_level():
    data = clip(0.0)
    data[:3, :] = np.nan                                     # background is not "bright"
    assert clouds.assess(data)["level"] == "none"
    assert clouds.assess(np.full((4, 4), np.nan, dtype="float32")) == {**clouds.assess(np.full((4, 4), np.nan)), "level": "none", "bright_share": 0.0}


def test_a_failure_while_computing_the_hint_never_breaks_a_result():
    class Broken:
        def read_clip(self, *a, **k):
            raise RuntimeError("boom")

    assert clouds.for_scene(Broken(), (0, 0, 1, 1)) is None


def stats(client):
    return json.loads(client.get(EXEC + "&RawDataOutput=stats").data)


def test_every_result_carries_the_hint(server):
    client, data, tmp, mp = server
    turn_on(mp, FakeBhoonidhi([feature(SCENE)], archives={SCENE: make_zip(tmp, SCENE)}))
    hint = stats(client)["cloud_hint"]
    assert hint["level"] in ("none", "some", "much") and 0.0 <= hint["bright_share"] <= 1.0


def test_a_bright_scene_is_flagged_through_the_wps(server, monkeypatch):
    client, data, tmp, mp = server
    turn_on(mp, FakeBhoonidhi([feature(SCENE)], archives={SCENE: make_zip(tmp, SCENE)}))
    real = Scene.read_clip

    def bright_green(self, band, bbox, mode="radiance"):
        values, transform = real(self, band, bbox, mode)
        return (np.full_like(values, 25.0) if band == "green" else values), transform

    monkeypatch.setattr(Scene, "read_clip", bright_green)
    hint = stats(client)["cloud_hint"]
    assert hint["level"] == "much" and hint["bright_share"] == 1.0
