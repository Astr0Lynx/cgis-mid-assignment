import os
import time

from app.cleanup import purge_old

DAY = 24 * 3600


def age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


def make_job(directory, name, age_s):
    """A stored job: <name>.xml status document plus <name>/ndvi.tif result folder."""
    (directory / f"{name}.xml").write_text("<status/>")
    folder = directory / name
    folder.mkdir()
    (folder / "ndvi.tif").write_bytes(b"II*\x00")
    for p in (folder / "ndvi.tif", folder, directory / f"{name}.xml"):
        age(p, age_s)


def test_removes_old_jobs_keeps_recent_ones(tmp_path):
    make_job(tmp_path, "old", 2 * DAY)
    make_job(tmp_path, "fresh", 60)
    assert purge_old(tmp_path, DAY) == 2  # the old status file and the old folder
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fresh", "fresh.xml"]
    assert (tmp_path / "fresh" / "ndvi.tif").exists()


def test_never_removes_the_directory_itself_and_handles_empty_or_missing(tmp_path):
    assert purge_old(tmp_path, 0) == 0 and tmp_path.is_dir()
    assert purge_old(tmp_path / "does-not-exist", 0) == 0


def test_removes_everything_when_limit_is_zero_and_entries_are_old(tmp_path):
    make_job(tmp_path, "a", 10)
    assert purge_old(tmp_path, 0) == 2 and not list(tmp_path.iterdir())


def test_symlink_is_removed_as_a_link_not_followed(tmp_path):
    import pytest

    target = tmp_path / "keep"
    target.mkdir()
    (target / "precious.txt").write_text("x")
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    link = outputs / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted on this system")
    purge_old(outputs, 60, now=time.time() + 3600)  # an hour later, so the link counts as old
    assert not link.exists() and (target / "precious.txt").exists()
