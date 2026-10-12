"""Run the real web app against a FAKE Bhoonidhi, to try the browser login + fetch workflow safely.

  python scripts/dev_fake_bhoonidhi.py [port]      then open http://localhost:5000/ (default port 5000)

No account, no email, no network and no download quota are involved:
  - log in with any user id and the password  correct-horse
  - the emailed one-time code is            123456
  - the fake archive holds three made-up scenes around Vijayawada (1 Oct and 15 Sep cover the area; 20 Sep is tilted away), far from the prepared Hyderabad scenes:
    draw a box around 80.6 E, 16.5 N (a few km wide) and press Run to see the whole flow.
Fetched scenes go to data/_fetched and expire like real ones. This is a development aid, never for a public instance.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["BROWSER_LOGIN"] = "1"

import app  # noqa: E402,F401  (pins PROJ/GDAL data first)
from app import webauth  # noqa: E402
from app.bhoonidhi_client import BhoonidhiClient  # noqa: E402
from app.server import flask_app  # noqa: E402
from tests.live_helpers import FakeBhoonidhi, FakeResponse, feature, footprint, make_zip  # noqa: E402

CENTER = (80.60, 16.50)  # Vijayawada
SCENE_ID = "R2301OCT2026999999999900060PSANSTUC00GTDF"      # 1 Oct, covers the area
OLDER_ID = "R2315SEP2026999999999900060PSANSTUC00GTDF"      # 15 Sep, covers the area
DENIED_ID = "R2325SEP2026999999999900060PSANSTUC00GTDF"     # 25 Sep: the portal answers "permission denied" for it
TILTED_ID = "R2320SEP2026999999999900060PSANSTUC00GTDF"     # 20 Sep: listed by the search, but its true image is elsewhere


class SlowFake(FakeBhoonidhi):
    """The fake, but its download arrives in small timed pieces so progress is visible."""

    def request(self, method, url, **kw):
        if "/bhoonidhi/data/" in url and DENIED_ID in url:  # what the real portal does for a refused download: 404 + its permission page
            return FakeResponse(404, "<!-- Icons --> <html> <body class=\"permission_denied\"> <div align=\"center\">",
                                headers={"Content-Type": "text/html;charset=UTF-8"})
        r = super().request(method, url, **kw)
        if "/bhoonidhi/data/" in url and r.status_code == 200:
            chunks = r._chunks

            def paced(chunk_size=1):
                for chunk in chunks:
                    time.sleep(0.4)
                    yield chunk

            r.iter_content = paced
        return r


def main(port: int) -> None:
    zips = Path(tempfile.mkdtemp(prefix="fake-bhoonidhi-"))
    api = SlowFake([feature(SCENE_ID, geometry=footprint(CENTER, 0.25)), feature(TILTED_ID, geometry=footprint(CENTER, 0.25)), feature(DENIED_ID, geometry=footprint(CENTER, 0.25)),
                    feature(OLDER_ID, geometry=footprint(CENTER, 0.25))],
                   archives={SCENE_ID: make_zip(zips, SCENE_ID, "01-OCT-2026", CENTER),
                             OLDER_ID: make_zip(zips, OLDER_ID, "15-SEP-2026", CENTER),
                             TILTED_ID: make_zip(zips, TILTED_ID, "20-SEP-2026", CENTER),
                             DENIED_ID: make_zip(zips, DENIED_ID, "25-SEP-2026", CENTER)})
    square = lambda lon, lat, h: {"UL": (lon - h, lat + h), "UR": (lon + h, lat + h), "LR": (lon + h, lat - h), "LL": (lon - h, lat - h)}
    api.filter_dates = True  # like the real search: only scenes inside the requested date range
    api.meta_corners = {SCENE_ID: square(*CENTER, 0.2), OLDER_ID: square(*CENTER, 0.2), TILTED_ID: square(82.5, 16.5, 0.1), DENIED_ID: square(*CENTER, 0.2)}
    api.portal_otp = True
    webauth.make_client = lambda user, password: BhoonidhiClient(
        user, password, session=api, sleep=lambda s: None, download_auth="portal")
    print(f"Fake Bhoonidhi ready. Password: correct-horse   Code: 123456   Try a box around {CENTER[0]} E, {CENTER[1]} N")
    flask_app.run(host="127.0.0.1", port=port, threaded=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 5000)
