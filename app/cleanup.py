"""Delete old result files so a long-running public instance does not fill its disk.

Every Execute with stored output leaves a status document (`<id>.xml`) and a folder (`<id>/ndvi.tif`) in outputs/.
Nothing else removes them. `purge_old` deletes entries older than a limit; `start_background_purge` runs it on a
timer. Age is the entry's last-modified time, so a job still being written is never old enough to be removed.
"""
import logging
import shutil
import threading
import time
from pathlib import Path

LOGGER = logging.getLogger("bhoonidhi.cleanup")


def purge_old(directory: Path, max_age_s: float, now: float | None = None) -> int:
    """Remove files and folders directly inside `directory` not modified for `max_age_s` seconds.

    Returns how many top-level entries were removed. Never touches `directory` itself, and a symlink is removed as a
    link, never followed. Problems with single entries are logged and skipped.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return 0
    now = time.time() if now is None else now
    removed = 0
    for entry in directory.iterdir():
        try:
            if now - entry.lstat().st_mtime <= max_age_s:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed += 1
        except FileNotFoundError:
            continue  # removed by someone else in the meantime
        except OSError as e:
            LOGGER.warning("could not remove %s: %s", entry, e)
    return removed


def start_background_purge(directory: Path, max_age_s: float, interval_s: float) -> threading.Thread:
    """Purge now and then every `interval_s` seconds, in a daemon thread (it dies with the server)."""

    def loop():
        while True:
            try:
                n = purge_old(directory, max_age_s)
                if n:
                    LOGGER.info("purged %d old result entries from %s", n, directory)
            except Exception:  # a cleanup bug must never take the service down
                LOGGER.exception("output purge failed")
            time.sleep(interval_s)

    thread = threading.Thread(target=loop, name="output-purge", daemon=True)
    thread.start()
    return thread
