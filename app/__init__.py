"""Pin PROJ/GDAL data to the copies bundled with rasterio.

A system-wide PostgreSQL/PostGIS install can export PROJ_LIB/GDAL_DATA pointing at
an incompatible PROJ database, which breaks every CRS lookup. This must run before
rasterio is first used, so it lives at package import.
"""
import os
from importlib.util import find_spec
from pathlib import Path

_spec = find_spec("rasterio")
if _spec and _spec.submodule_search_locations:
    _root = Path(next(iter(_spec.submodule_search_locations)))
    for _var, _dir in (("PROJ_LIB", "proj_data"), ("PROJ_DATA", "proj_data"), ("GDAL_DATA", "gdal_data")):
        if (_root / _dir).is_dir():
            os.environ[_var] = str(_root / _dir)
