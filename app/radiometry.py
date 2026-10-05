"""DN -> physical-quantity conversion for Resourcesat LISS-III.

v1 uses at-sensor radiance. TOA reflectance is deferred (see later.md); the
`mode` argument is the single switch point so callers do not change.
"""
from pathlib import Path

import numpy as np

# LISS-III is 10-bit; BAND_META.txt Lmin/Lmax span DN 0..1023.
MAX_DN = 1023.0
BAND_NUMBERS = {"green": 2, "red": 3, "nir": 4, "swir": 5}


def parse_band_meta(path: Path) -> dict[str, str]:
    """Parse the `Key= value` lines of BAND_META.txt into a dict."""
    meta = {}
    for line in Path(path).read_text(errors="replace").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            meta[key.strip()] = value.strip()
    return meta


def dn_to_radiance(dn: np.ndarray, lmin: float, lmax: float) -> np.ndarray:
    """Linear DN -> radiance (mW/cm2/sr/um). DN 0 (background) becomes NaN."""
    radiance = lmin + dn.astype(np.float32) * (lmax - lmin) / MAX_DN
    radiance[dn == 0] = np.nan
    return radiance


def to_physical(dn: np.ndarray, meta: dict[str, str], band: int, mode: str = "radiance") -> np.ndarray:
    if mode != "radiance":
        raise NotImplementedError(f"radiometry mode {mode!r} not implemented yet (see later.md)")
    return dn_to_radiance(dn, float(meta[f"B{band}_Lmin"]), float(meta[f"B{band}_Lmax"]))
