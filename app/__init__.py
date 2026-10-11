"""Make rasterio/GDAL/PROJ find their OWN data, whatever else is installed on the machine.

A system-wide PostgreSQL/PostGIS install exports PROJ_LIB / GDAL_DATA pointing at its own, older PROJ database, which
breaks every CRS lookup ("proj.db contains DATABASE.LAYOUT.VERSION.MINOR = 2 whereas a number >= 6 is expected").
This runs at package import, before rasterio is first used:

  1. if rasterio ships its data (the pip wheel: rasterio/proj_data, rasterio/gdal_data), point the variables there;
  2. else look for the data of the Python distribution itself (conda: <prefix>/Library/share/proj, <prefix>/share/proj);
  3. else, if a variable points into a PostgreSQL/PostGIS install, drop it so the library falls back to its own default
     instead of reading another product's database.
"""
import os
import sys
from importlib.util import find_spec
from pathlib import Path

_FOREIGN_MARKERS = ("postgresql", "postgis")


def _first_dir(candidates):
    return next((c for c in candidates if c.is_dir()), None)


def _foreign(path: str | None) -> bool:
    return bool(path) and any(marker in path.lower() for marker in _FOREIGN_MARKERS)


def pin_proj_gdal_data(env, rasterio_root: Path | None, prefix: Path) -> dict:
    """Update `env` (a mapping such as os.environ) as described in the module docstring; returns what was decided."""
    roots = ([rasterio_root] if rasterio_root else [])
    proj = _first_dir([r / "proj_data" for r in roots] + [prefix / "Library" / "share" / "proj", prefix / "share" / "proj"])
    gdal = _first_dir([r / "gdal_data" for r in roots] + [prefix / "Library" / "share" / "gdal", prefix / "share" / "gdal"])
    decided = {}
    for var, found in (("PROJ_LIB", proj), ("PROJ_DATA", proj), ("GDAL_DATA", gdal)):
        if found:
            env[var] = str(found)
            decided[var] = str(found)
        elif _foreign(env.get(var)):
            decided[var] = None  # removed: it pointed into another product's installation
            env.pop(var, None)
    return decided


_spec = find_spec("rasterio")
_root = Path(next(iter(_spec.submodule_search_locations))) if _spec and _spec.submodule_search_locations else None
pin_proj_gdal_data(os.environ, _root, Path(sys.prefix))
