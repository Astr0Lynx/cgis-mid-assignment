"""DN -> physical-quantity conversion for Resourcesat LISS-III.

Two modes, selected by `mode` (the only switch point; callers do not change):
  radiance  at-sensor radiance, mW/cm2/sr/um, from the per-band Lmin/Lmax in BAND_META.txt
  toa       top-of-atmosphere reflectance (unitless, ~0..1):
                rho = pi * L * d^2 / (ESUN * cos(theta_s))
            with L the radiance above, d the Earth-Sun distance in AU, theta_s the solar zenith angle
            (90 deg minus the scene's SunElevationAtCenter), and ESUN the band's mean solar exo-atmospheric
            irradiance. Formula: NRSC Resourcesat-2A surface-reflectance user guide (eq. 1 context) and
            arXiv:1809.10190 eq. 3. Neither gives numeric ESUN values; see `esun_liss3.json`.
            This is *not* atmospheric correction (no aerosol / water-vapour removal).
"""
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np

# LISS-III is 10-bit; BAND_META.txt Lmin/Lmax span DN 0..1023.
MAX_DN = 1023.0
BAND_NUMBERS = {"green": 2, "red": 3, "nir": 4, "swir": 5}
MODES = ("radiance", "toa")
ESUN_FILE = Path(__file__).with_name("esun_liss3.json")


class EsunNotConfigured(RuntimeError):
    """TOA reflectance requested but no cited ESUN table is available."""


def parse_band_meta(path: Path) -> dict[str, str]:
    """Parse the `Key= value` lines of BAND_META.txt into a dict."""
    meta = {}
    for line in Path(path).read_text(errors="replace").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            meta[key.strip()] = value.strip()
    return meta


def load_esun(path: Path = ESUN_FILE) -> dict[int, float]:
    """Per-band ESUN (mW/cm2/um), or raise EsunNotConfigured.

    The file must name its source and give a number for every band; this refuses to guess, because a
    wrong constant would silently produce plausible-looking but wrong reflectance.
    """
    data = json.loads(Path(path).read_text())
    bands = data.get("bands", {})
    if not data.get("source") or any(bands.get(str(b)) is None for b in BAND_NUMBERS.values()):
        raise EsunNotConfigured(
            "TOA reflectance needs the LISS-III solar exo-atmospheric irradiance, ESUN, per band, and none is "
            f"configured with a cited source. See {Path(path).name}. Use radiometry=radiance."
        )
    return {int(b): float(v) for b, v in bands.items()}


def earth_sun_distance_au(day_of_year: int) -> float:
    """Earth-Sun distance in AU (standard first-order approximation, error < 0.0002 AU)."""
    return 1.0 - 0.01672 * math.cos(math.radians(0.9856 * (day_of_year - 4)))


def dn_to_radiance(dn: np.ndarray, lmin: float, lmax: float) -> np.ndarray:
    """Linear DN -> radiance (mW/cm2/sr/um). DN 0 (background) becomes NaN."""
    radiance = lmin + dn.astype(np.float32) * (lmax - lmin) / MAX_DN
    radiance[dn == 0] = np.nan
    return radiance


def radiance_to_toa(radiance: np.ndarray, esun: float, d_au: float, sun_elevation_deg: float) -> np.ndarray:
    """TOA reflectance from radiance; cos(zenith) = sin(elevation)."""
    return (math.pi * radiance * d_au ** 2 / (esun * math.sin(math.radians(sun_elevation_deg)))).astype(np.float32)


def to_physical(dn: np.ndarray, meta: dict[str, str], band: int, mode: str = "radiance") -> np.ndarray:
    if mode not in MODES:
        raise ValueError(f"unknown radiometry mode {mode!r}; expected one of {MODES}")
    radiance = dn_to_radiance(dn, float(meta[f"B{band}_Lmin"]), float(meta[f"B{band}_Lmax"]))
    if mode == "radiance":
        return radiance
    esun = load_esun()[band]
    doy = datetime.strptime(meta["DateOfPass"], "%d-%b-%Y").timetuple().tm_yday
    # one sun angle per scene (at its centre): fine over a city-sized clip, approximate over a full scene
    return radiance_to_toa(radiance, esun, earth_sun_distance_au(doy), float(meta["SunElevationAtCenter"]))
