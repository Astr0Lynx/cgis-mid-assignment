"""Read a Bhoonidhi LISS-III scene folder and clip it to a lat/lon bbox."""
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio
from rasterio.errors import WindowError
from rasterio.shutil import copy as rio_copy
from rasterio.transform import Affine
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds

from app import indices
from app.radiometry import BAND_NUMBERS, parse_band_meta, to_physical

DATA_DIR = Path(os.environ.get("BHOONIDHI_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


@dataclass
class Clip:
    data: np.ndarray
    transform: Affine
    crs: rasterio.crs.CRS
    scene_id: str
    date: datetime


class Scene:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.meta = parse_band_meta(self.folder / "BAND_META.txt")
        self.scene_id = self.meta["OTSProductID"]
        self.date = datetime.strptime(self.meta["DateOfPass"], "%d-%b-%Y")
        # The tifs' GeoTIFF keys carry a WGS84 ellipsoid off by ~1e-6 in flattening, which
        # matches no EPSG code; use the standard WGS84/UTM north code from ZoneNo instead.
        self.crs = rasterio.crs.CRS.from_epsg(32600 + int(float(self.meta["ZoneNo"])))
        with rasterio.open(self.band_path("red")) as ds:
            self.bounds = ds.bounds

    def band_path(self, band: str) -> Path:
        return self.folder / f"BAND{BAND_NUMBERS[band]}.tif"

    def bounds_in(self, bbox):
        """Transform a (minx, miny, maxx, maxy) lat/lon bbox into the scene CRS."""
        return transform_bounds("EPSG:4326", self.crs, *bbox)

    def covers(self, bbox) -> bool:
        l, b, r, t = self.bounds_in(bbox)
        return l >= self.bounds.left and r <= self.bounds.right and b >= self.bounds.bottom and t <= self.bounds.top

    def valid_fraction(self, bbox) -> float:
        """Fraction of the bbox with real data in both NIR and Red (DN 0 = background); 0 if it misses the scene."""
        try:
            nir, _ = self.read_clip("nir", bbox)
            red, _ = self.read_clip("red", bbox)
        except WindowError:  # the bbox does not overlap the image at all
            return 0.0
        return float(np.mean(np.isfinite(nir) & np.isfinite(red))) if nir.size else 0.0

    def read_clip(self, band: str, bbox, mode: str = "radiance"):
        """Read one band for the bbox, returned as physical values (NaN = background)."""
        with rasterio.open(self.band_path(band)) as ds:
            window = from_bounds(*self.bounds_in(bbox), transform=ds.transform)
            window = window.round_offsets().round_lengths().intersection(Window(0, 0, ds.width, ds.height))
            dn = ds.read(1, window=window)
            transform = ds.window_transform(window)
        return to_physical(dn, self.meta, BAND_NUMBERS[band], mode), transform

    def index(self, bbox, name: str, mode: str = "radiance") -> Clip:
        """Clip the bbox and compute the named spectral index (see app.indices.INDICES)."""
        bands, transform = {}, None
        for band in indices.INDICES[name].bands:
            bands[band], transform = self.read_clip(band, bbox, mode)
        return Clip(indices.compute(name, bands), transform, self.crs, self.scene_id, self.date)

    def ndvi(self, bbox, mode: str = "radiance") -> Clip:
        return self.index(bbox, "ndvi", mode)


def find_scenes(bbox, data_dir: Path = DATA_DIR, min_valid: float = 0.95) -> list[Scene]:
    """Scenes under data_dir whose image (not just bounding rectangle) covers the bbox.

    Scenes are tilted, so the bounding rectangle overstates coverage; `valid_fraction`
    checks real pixels. Newest first.
    """
    scenes = [Scene(p.parent) for p in Path(data_dir).glob("*/BAND_META.txt")]
    hits = [s for s in scenes if s.covers(bbox) and s.valid_fraction(bbox) >= min_valid]
    return sorted(hits, key=lambda s: s.date, reverse=True)


def write_cog(clip: Clip, path: Path) -> Path:
    """Write a float32 NaN-nodata COG (via a temporary plain GeoTIFF)."""
    path = Path(path)
    tmp = path.with_suffix(".tmp.tif")
    h, w = clip.data.shape
    profile = dict(driver="GTiff", height=h, width=w, count=1, dtype="float32",
                   crs=clip.crs, transform=clip.transform, nodata=np.nan)
    with rasterio.open(tmp, "w", **profile) as dst:
        dst.write(clip.data, 1)
    rio_copy(tmp, path, driver="COG", compress="DEFLATE")
    tmp.unlink()
    return path
