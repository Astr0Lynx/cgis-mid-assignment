"""Spectral index formulas. NDVI only for now; more are planned in later.md."""
import numpy as np


def ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    """(NIR - Red) / (NIR + Red); NaN where either input is NaN or the sum is 0."""
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (nir - red) / (nir + red)
    out[~np.isfinite(out)] = np.nan
    return out.astype(np.float32)
