"""Spectral indices for Resourcesat-2 LISS-III (B2 green, B3 red, B4 NIR, B5 SWIR).

Every index offered here is a normalised difference (a - b) / (a + b) of two bands, so the registry is just a table
of band pairs. A new normalised-difference index is one line in INDICES.

Not offered, deliberately:
  EVI   needs a Blue band; LISS-III has none.
  SAVI  its soil constant (0.5) is defined for reflectance. It is meaningless on radiance (mW/cm2/sr/um), so it
        waits for TOA reflectance (see README section 5).
"""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Index:
    name: str
    title: str
    a: str  # first band (numerator sign +)
    b: str  # second band (numerator sign -)
    meaning: str

    @property
    def bands(self) -> tuple[str, str]:
        return (self.a, self.b)

    @property
    def formula(self) -> str:
        return f"({self.a}-{self.b})/({self.a}+{self.b})"


INDICES = {i.name: i for i in (
    Index("ndvi", "Normalised Difference Vegetation Index", "nir", "red",
          "vegetation vigour: high over healthy vegetation, near 0 over bare ground, negative over water"),
    Index("ndwi", "Normalised Difference Water Index (McFeeters)", "green", "nir",
          "open water: positive over water, negative over vegetation and soil"),
    Index("mndwi", "Modified Normalised Difference Water Index (Xu)", "green", "swir",
          "water, better separated from built-up land than NDWI"),
    Index("ndbi", "Normalised Difference Built-up Index", "swir", "nir",
          "built-up land: higher over built-up and bare surfaces than over vegetation"),
)}


def normalized_difference(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """(a - b) / (a + b); NaN where either input is NaN or the sum is 0."""
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (a - b) / (a + b)
    out[~np.isfinite(out)] = np.nan
    return out.astype(np.float32)


def compute(name: str, bands: dict[str, np.ndarray]) -> np.ndarray:
    index = INDICES[name]
    return normalized_difference(bands[index.a], bands[index.b])


def ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    """(NIR - Red) / (NIR + Red)."""
    return normalized_difference(nir, red)
