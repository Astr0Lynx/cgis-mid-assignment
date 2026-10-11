"""Read a Bhoonidhi LISS-III scene folder and clip it to a lat/lon bbox."""
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio
from rasterio.errors import WindowError
from rasterio.shutil import copy as rio_copy
from rasterio.transform import Affine
from rasterio.warp import transform, transform_bounds
from rasterio.windows import Window, from_bounds

from app import indices
from app.geometry import clip_convex
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
        try:
            self.crs = rasterio.crs.CRS.from_epsg(32600 + int(float(self.meta["ZoneNo"])))
        except rasterio.errors.CRSError as e:
            raise RuntimeError(
                "Could not build the scene's coordinate system, which is almost always a PROJ data problem "
                f"(python={sys.executable}; rasterio={rasterio.__file__}; PROJ_LIB={os.environ.get('PROJ_LIB')}; "
                f"PROJ_DATA={os.environ.get('PROJ_DATA')}). Original error: {str(e)[:200]}") from e
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


def available_area(folder: Path) -> list[tuple[float, float]]:
    """Polygon (lon, lat) of where this scene folder really has data.

    The scene's true image footprint (the four corners in BAND_META.txt: the image is tilted, so it is not a
    rectangle) intersected with the extent of the rasters on disk. For a full scene the two nearly coincide; for the
    pre-clipped cache the raster is much smaller, and only the clip can actually be served. Empty if they do not overlap.
    """
    scene = Scene(folder)
    m = scene.meta
    b = scene.bounds
    lons, lats = transform(scene.crs, "EPSG:4326", [b.left, b.right, b.right, b.left], [b.top, b.top, b.bottom, b.bottom])
    raster = list(zip(lons, lats))
    try:
        footprint = [(float(m[f"Image{c}Lon"]), float(m[f"Image{c}Lat"])) for c in ("UL", "UR", "LR", "LL")]
    except (KeyError, ValueError):
        return raster  # no corner metadata: the extent of the rasters is the best statement of where data exists
    return clip_convex(footprint, raster)


FETCHED_SUBDIR = "_fetched"  # scenes fetched from Bhoonidhi on request: they expire, the permanent cache never does


def fetch_dir(data_dir: Path) -> Path:
    return Path(data_dir) / FETCHED_SUBDIR


def scene_folders(data_dir: Path) -> list[Path]:
    """Every folder that holds a scene: the permanent ones (data_dir/<id>) and the fetched ones (data_dir/_fetched/<id>)."""
    root = Path(data_dir)
    return sorted(m.parent for pattern in ("*/BAND_META.txt", f"{FETCHED_SUBDIR}/*/BAND_META.txt") for m in root.glob(pattern))


def find_scenes(bbox, data_dir: Path = DATA_DIR, min_valid: float = 0.95) -> list[Scene]:
    """Scenes under data_dir whose image (not just bounding rectangle) covers the bbox.

    Scenes are tilted, so the bounding rectangle overstates coverage; `valid_fraction`
    checks real pixels. Newest first.
    """
    scenes = [Scene(folder) for folder in scene_folders(data_dir)]
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
