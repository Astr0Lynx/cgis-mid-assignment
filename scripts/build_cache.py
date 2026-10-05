"""Build the public-instance cache: clip each scene in data/ to an area of interest.

Output has the same folder layout as a Bhoonidhi scene (BAND2..5.tif + BAND_META.txt) but
the bands are small compressed COGs holding the original raw DN values, so the service
runs unchanged against it (BHOONIDHI_DATA_DIR=cache). No Bhoonidhi credentials are
involved: this only reads scenes you already downloaded.

Usage: python scripts/build_cache.py [--aoi minlon minlat maxlon maxlat] [--src data] [--dst cache]
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402,F401  (pins PROJ/GDAL data before rasterio is used)
import rasterio  # noqa: E402
from rasterio.shutil import copy as rio_copy  # noqa: E402

from app.scene import Scene  # noqa: E402

HYDERABAD_AOI = (78.20, 17.15, 78.80, 17.65)  # lon/lat, ~65 x 55 km around Hyderabad


def clip_band(scene: Scene, band: str, aoi, dst: Path) -> None:
    from rasterio.windows import Window, from_bounds

    with rasterio.open(scene.band_path(band)) as src:
        win = from_bounds(*scene.bounds_in(aoi), transform=src.transform)
        win = win.round_offsets().round_lengths().intersection(Window(0, 0, src.width, src.height))
        dn = src.read(1, window=win)
        profile = dict(driver="GTiff", height=dn.shape[0], width=dn.shape[1], count=1, dtype="uint16",
                       crs=scene.crs, transform=src.window_transform(win))
    tmp = dst.with_suffix(".tmp.tif")
    with rasterio.open(tmp, "w", **profile) as out:
        out.write(dn, 1)
    rio_copy(tmp, dst, driver="COG", compress="DEFLATE", predictor=2, blocksize=512)
    tmp.unlink()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", type=float, nargs=4, default=HYDERABAD_AOI, metavar=("MINLON", "MINLAT", "MAXLON", "MAXLAT"))
    ap.add_argument("--src", default="data")
    ap.add_argument("--dst", default="cache")
    args = ap.parse_args()

    for meta in sorted(Path(args.src).glob("*/BAND_META.txt")):
        scene = Scene(meta.parent)
        out = Path(args.dst) / scene.scene_id
        out.mkdir(parents=True, exist_ok=True)
        for band in ("green", "red", "nir", "swir"):
            clip_band(scene, band, args.aoi, out / scene.band_path(band).name)
        shutil.copy(meta, out / "BAND_META.txt")
        size = sum(f.stat().st_size for f in out.iterdir()) / 1e6
        print(f"{scene.scene_id} ({scene.date.date()}): {size:.1f} MB")


if __name__ == "__main__":
    main()
