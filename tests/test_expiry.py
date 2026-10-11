"""Expiry of scenes fetched from Bhoonidhi: by age, then by total size; the permanent cache is never touched."""
import os
import time

from app.cleanup import purge_fetched

DAY = 24 * 3600


def age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


def make_scene_dir(parent, name, size, age_s):
    d = parent / name
    d.mkdir(parents=True)
    (d / "BAND4.tif").write_bytes(b"x" * size)
    age(d / "BAND4.tif", age_s)
    age(d, age_s)
    return d


def test_fetched_scenes_expire_after_the_time_limit_and_nothing_else_is_touched(tmp_path):
    permanent = make_scene_dir(tmp_path, "permanent", 100, 30 * DAY)  # ancient, but not in the fetch folder
    fetched = tmp_path / "_fetched"
    make_scene_dir(fetched, "old", 100, 7 * 3600)
    make_scene_dir(fetched, "fresh", 100, 60)
    assert purge_fetched(fetched, ttl_s=6 * 3600, max_bytes=10 ** 9) == 1
    assert sorted(p.name for p in fetched.iterdir()) == ["fresh"] and permanent.exists()


def test_fetched_scenes_are_evicted_oldest_first_when_over_the_size_cap(tmp_path):
    fetched = tmp_path / "_fetched"
    for name, ago in (("a_oldest", 3000), ("b_middle", 2000), ("c_newest", 1000)):
        make_scene_dir(fetched, name, 1000, ago)
    assert purge_fetched(fetched, ttl_s=10 ** 6, max_bytes=2500) == 1  # 3000 bytes > 2500: drop the oldest only
    assert sorted(p.name for p in fetched.iterdir()) == ["b_middle", "c_newest"]


def test_nothing_is_removed_when_everything_is_young_and_small(tmp_path):
    fetched = tmp_path / "_fetched"
    make_scene_dir(fetched, "a", 10, 5)
    assert purge_fetched(fetched, ttl_s=3600, max_bytes=10 ** 6) == 0 and (fetched / "a").exists()


def test_purge_fetched_is_safe_when_the_folder_is_missing(tmp_path):
    assert purge_fetched(tmp_path / "nope", 1, 1) == 0
