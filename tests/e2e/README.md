# Browser test of the login + fetch workflow

Drives the real web page in Chrome against the real app running with a **fake Bhoonidhi** (no account, no network).
Not part of CI (it needs Chrome and Node); the Python tests cover the same endpoints without a browser.

```bash
python scripts/dev_fake_bhoonidhi.py 5056      # terminal 1: the app + a fake Bhoonidhi (password correct-horse, code 123456)
cd tests/e2e && npm install && node login_flow.js   # terminal 2   (BASE=http://localhost:5056, CHROME=path to chrome.exe)
```
Checks: an uncovered box offers a fetch; Run opens the login dialog; the emailed-code step (wrong code rejected, right code
accepted); the pending Run resumes with a progress bar and shows the result; the password is gone from the page; logout; a
second visitor reuses the fetched scene without logging in; logging in by choice starts nothing; cancelling fetches nothing.

## Choosing a Bhoonidhi scene
`BASE=http://localhost:5057 node choose_scene.js` against `python scripts/dev_fake_bhoonidhi.py 5057` (fake archive with three scenes,
one of them tilted away). Delete `data/_fetched` between runs: a scene fetched by an earlier run counts as prepared.
Checks: the search button appears with an area; searching asks for the login; the scenes are listed newest first with a coverage
mark; the tilted one cannot be chosen; Run fetches exactly the chosen date and saves it as a layer.

## Saved layers
`node layers.js` (against any running instance with the prepared scenes, `BASE=http://127.0.0.1:5000`; run it from
`tests/e2e`) checks the saved-layers list: each run is kept, selecting a layer shows its own details and download links,
visibility, per-layer opacity, reordering, renaming, duplicate requests, "Clear" keeping the layers, map-click selection,
removing one and removing all.
