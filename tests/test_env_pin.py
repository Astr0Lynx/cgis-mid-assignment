"""app.pin_proj_gdal_data: rasterio must find its own PROJ/GDAL data, not another product's (PostgreSQL's)."""
from pathlib import Path

from app import pin_proj_gdal_data

POSTGRES_PROJ = r"C:\Program Files\PostgreSQL\18\share\contrib\postgis-3.6\proj"
POSTGRES_GDAL = r"C:\Program Files\PostgreSQL\18\gdal-data"


def make(tmp_path, *dirs):
    for d in dirs:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_bundled_rasterio_data_wins_over_whatever_the_system_exports(tmp_path):
    root = make(tmp_path / "rasterio", "proj_data", "gdal_data")
    env = {"PROJ_LIB": POSTGRES_PROJ, "GDAL_DATA": POSTGRES_GDAL}
    pin_proj_gdal_data(env, root, tmp_path / "prefix")
    assert env["PROJ_LIB"] == env["PROJ_DATA"] == str(root / "proj_data") and env["GDAL_DATA"] == str(root / "gdal_data")


def test_conda_style_data_is_used_when_rasterio_ships_none(tmp_path):
    prefix = make(tmp_path / "conda", "Library/share/proj", "Library/share/gdal")
    env = {"PROJ_LIB": POSTGRES_PROJ}
    pin_proj_gdal_data(env, tmp_path / "rasterio-without-data", prefix)
    assert env["PROJ_LIB"] == str(prefix / "Library" / "share" / "proj")
    assert env["GDAL_DATA"] == str(prefix / "Library" / "share" / "gdal")


def test_a_postgres_path_is_dropped_when_no_better_data_exists(tmp_path):
    """The case that crashed a real download: nothing of our own to point at, but PostgreSQL's data is exported."""
    env = {"PROJ_LIB": POSTGRES_PROJ, "PROJ_DATA": POSTGRES_PROJ, "GDAL_DATA": POSTGRES_GDAL, "PATH": "x"}
    decided = pin_proj_gdal_data(env, None, tmp_path / "empty-prefix")
    assert env == {"PATH": "x"} and decided == {"PROJ_LIB": None, "PROJ_DATA": None, "GDAL_DATA": None}


def test_a_sensible_user_setting_is_left_alone_when_nothing_better_exists(tmp_path):
    env = {"PROJ_LIB": "/opt/proj/share", "GDAL_DATA": "/opt/gdal"}
    assert pin_proj_gdal_data(env, None, tmp_path / "empty") == {} and env["PROJ_LIB"] == "/opt/proj/share"


def test_it_works_on_the_real_environment_of_this_machine():
    """After `import app`, a CRS can be built (this failed on a machine with PostgreSQL's PROJ exported)."""
    import rasterio

    assert rasterio.crs.CRS.from_epsg(32644).to_epsg() == 32644
