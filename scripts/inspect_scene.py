"""Step 0: inspect a downloaded Bhoonidhi scene (extracted folder or .zip).

Usage: python scripts/inspect_scene.py path/to/scene_folder_or.zip

Prints the file listing and rasterio header metadata for each *.tif (size, dtype,
nodata, CRS, tiling, compression). Reads headers only, never pixel data, and
contains no credentials.
"""
import sys
import zipfile
from pathlib import Path

import rasterio


def describe(uri: str, label: str) -> None:
    with rasterio.open(uri) as ds:
        print(f"--- {label} ---")
        print(f"size={ds.width}x{ds.height} bands={ds.count} dtype={ds.dtypes[0]} "
              f"nodata={ds.nodata} crs={ds.crs}")
        print(f"res={ds.res} bounds={tuple(round(b, 2) for b in ds.bounds)}")
        print(f"block_shapes={ds.block_shapes} compression={ds.compression} "
              f"interleave={ds.interleaving} overviews={ds.overviews(1)}")
        print(f"tags={ds.tags()} band1_tags={ds.tags(1)}")


def main(path: str) -> None:
    p = Path(path)
    if p.is_dir():
        print(f"folder: {p.name}")
        for f in sorted(p.iterdir()):
            print(f"{f.stat().st_size / 1e6:9.2f} MB  {f.name}")
        print()
        for f in sorted(p.glob("*.tif")):
            describe(f.resolve().as_posix(), f.name)
    else:
        with zipfile.ZipFile(p) as zf:
            tifs = [n for n in zf.namelist() if n.lower().endswith((".tif", ".tiff"))]
            for info in zf.infolist():
                print(f"{info.file_size / 1e6:9.2f} MB  {info.filename}")
        print()
        for name in tifs:
            describe(f"/vsizip/{p.resolve().as_posix()}/{name}", name)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
