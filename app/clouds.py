"""A rough hint that cloud or haze MAY be present, from how bright the green band is.

Bhoonidhi gives no cloud cover for these scenes (the field is empty) and the service does no cloud masking, so this is only a
warning. Clouds are far brighter in the visible bands than land. Measured in at-sensor radiance (calibrated, so scenes can be
compared) on the scenes available while building this:

    clear-sky scenes (May, Oct 2026 and Dec 2020, Hyderabad area and Medak-Bidar):  mean green radiance 3.9 to 7.8
        and at most 5% of pixels above 15 mW/cm2/sr/um
    12 Aug 2020 (monsoon, visibly overcast):                                        mean green radiance 23.4, 94% above 15

so the share of pixels above 15 separates them, but that is six scenes. Bright bare ground, sand, roofs or water glint can also
exceed it, and thin cloud or haze can stay below it: the hint says "may", never "is".
"""
import numpy as np

GREEN_RADIANCE_LIMIT = 15.0  # mW/cm2/sr/um: far above clear land in the scenes measured
SOME_SHARE = 0.10            # at least this share of bright pixels: "may be present"
MUCH_SHARE = 0.50            # at least this share: "may cover much of the area"


def assess(green_radiance: np.ndarray) -> dict:
    """The hint for one clip of green-band radiance (NaN = background)."""
    valid = np.isfinite(green_radiance)
    share = float((green_radiance[valid] > GREEN_RADIANCE_LIMIT).mean()) if valid.any() else 0.0
    level = "much" if share >= MUCH_SHARE else "some" if share >= SOME_SHARE else "none"
    return {"level": level, "bright_share": round(share, 3), "green_radiance_limit": GREEN_RADIANCE_LIMIT,
            "note": "heuristic from green-band brightness; cloud, haze or bright ground may be present: not a cloud mask"}


def for_scene(scene, bbox) -> dict | None:
    """The hint for the bbox of a scene, or None if it cannot be computed (it must never break a result)."""
    try:
        green, _ = scene.read_clip("green", bbox, "radiance")
        return assess(green)
    except Exception:
        return None
