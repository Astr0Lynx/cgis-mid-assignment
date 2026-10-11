"""Run the real web app against a FAKE Bhoonidhi, to try the browser login + fetch workflow safely.

  python scripts/dev_fake_bhoonidhi.py [port]      then open http://localhost:5000/ (default port 5000)

No account, no email, no network and no download quota are involved:
  - log in with any user id and the password  correct-horse
  - the emailed one-time code is            123456
  - the fake archive holds one made-up scene (1 Oct 2026) around Vijayawada, far from the prepared Hyderabad scenes:
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
SCENE_ID = "R2301OCT2026999999999900060PSANSTUC00GTDF"


class SlowFake(FakeBhoonidhi):
    """The fake, but its download arrives in small timed pieces so progress is visible."""

    def request(self, method, url, **kw):
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
    api = SlowFake([feature(SCENE_ID, geometry=footprint(CENTER, 0.25))],
                   archives={SCENE_ID: make_zip(zips, SCENE_ID, "01-OCT-2026", CENTER)})
    api.portal_otp = True
    webauth.make_client = lambda user, password: BhoonidhiClient(
        user, password, session=api, sleep=lambda s: None, download_auth="portal")
    print(f"Fake Bhoonidhi ready. Password: correct-horse   Code: 123456   Try a box around {CENTER[0]} E, {CENTER[1]} N")
    flask_app.run(host="127.0.0.1", port=port, threaded=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 5000)
