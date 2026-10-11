"""Check your Bhoonidhi access and fetch a scene into data/ (run this locally, with YOUR credentials).

  python scripts/fetch_scene.py --bbox 17.35,78.40,17.45,78.55 --dry-run     # log in + search only, no download
  python scripts/fetch_scene.py --bbox 17.35,78.40,17.45,78.55 --date 2026-05-23

--bbox is miny,minx,maxy,maxx (latitude first, like the WPS). Credentials come from the environment or a .env file:
    BHOONIDHI_USER=...
    BHOONIDHI_PASSWORD=...
They are never printed. Start with --dry-run: it uses one login and one search and downloads nothing. Authentication is
limited to 20 calls per hour per IP address, so do not run it in a loop.
"""
import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402,F401  (pins PROJ/GDAL data before rasterio is used)
from app import live_fetch  # noqa: E402
from app.bhoonidhi_client import (DOWNLOAD_AUTH_STYLES, PORTAL_RECORD_FIELDS, BhoonidhiClient, BhoonidhiError,  # noqa: E402
                                 footprint_contains_bbox, load_env_file)


def ask_otp() -> str:
    try:
        return input("Bhoonidhi emailed you a 6-digit one-time code. Enter it here: ")
    except EOFError:
        raise BhoonidhiError("a one-time code is needed but there is no terminal to type it in")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bbox", required=True, help="miny,minx,maxy,maxx in degrees (latitude first)")
    ap.add_argument("--date", help="acquisition date YYYY-MM-DD (default: the last %d days)" % live_fetch.LOOKBACK_DAYS)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--dry-run", action="store_true", help="log in and search only; download nothing")
    ap.add_argument("--probe-download-auth", action="store_true",
                    help="try each way of presenting the token to /download (Bearer header, ?token=, token header) "
                         "and report which one the server accepts; downloads nothing")
    ap.add_argument("--probe-portal-download", action="store_true",
                    help="log in to the portal itself, then ask its own data path (what the unofficial client uses) for the scene; "
                         "reads only the first bytes, downloads nothing")
    ap.add_argument("--download-auth", choices=DOWNLOAD_AUTH_STYLES, default=None,
                    help="how to download: 'portal' (works; asks for an emailed one-time code), or an official-API form. "
                         "Default: $BHOONIDHI_DOWNLOAD_AUTH, else bearer")
    ap.add_argument("--settle", type=float, default=0.0, metavar="SECONDS",
                    help="with --probe-download-auth: wait this long after login and search before asking /download")
    ap.add_argument("--diagnose-download", action="store_true",
                    help="send ONE request to /download and print how the server answers (status, headers, "
                         "redirect target, first bytes); downloads nothing and prints no secrets")
    args = ap.parse_args()

    load_env_file(Path(".env"))
    user, password = os.environ.get("BHOONIDHI_USER"), os.environ.get("BHOONIDHI_PASSWORD")
    if not user or not password:
        print("Set BHOONIDHI_USER and BHOONIDHI_PASSWORD (environment or .env file).")
        return 2
    try:
        miny, minx, maxy, maxx = (float(v) for v in args.bbox.split(","))
        wanted = date.fromisoformat(args.date) if args.date else None
    except ValueError:
        print("Bad --bbox (expected four numbers) or --date (expected YYYY-MM-DD).")
        return 2
    bbox = (minx, miny, maxx, maxy)

    client = BhoonidhiClient(user, password, download_auth=args.download_auth or os.environ.get("BHOONIDHI_DOWNLOAD_AUTH", "bearer"),
                              otp_prompt=ask_otp)
    try:
        start, end = (wanted, wanted) if wanted else (None, None)
        if not wanted:
            from datetime import timedelta
            end = date.today()
            start = end - timedelta(days=live_fetch.LOOKBACK_DAYS)
        print(f"Searching Bhoonidhi for LISS-III scenes intersecting the bbox, {start} to {end} ...")
        items = client.search(bbox, start, end)
        print(f"Login and search worked. {len(items)} online scene(s) intersect the bbox:")
        for i in sorted(items, key=lambda i: i.date or date.min, reverse=True):
            covers = "covers the bbox" if footprint_contains_bbox(i.geometry, bbox) else "does NOT fully cover the bbox"
            print(f"  {i.date}  {i.collection:26s} {i.id}  ({covers})")
        if args.dry_run:
            print("Dry run: nothing downloaded.")
            return 0
        if args.probe_portal_download:
            options = live_fetch.candidates(bbox, wanted, client)
            if not options:
                print("No scene to probe: none covers the bbox.")
                return 1
            print("Logging in to the portal itself (a separate login from the API's) ...")
            client.portal_login()
            print("Portal login worked (the session token is not shown).")
            print("Asking the portal's own search where this scene lives ...")
            record = None
            try:
                record = client.find_portal_record(options[0], bbox)
            except BhoonidhiError as e:
                print(f"  portal search failed: {e}")
            if record:
                print("  portal record (non-secret fields): " + ", ".join(
                    f"{k}={record.get(k)}" for k in PORTAL_RECORD_FIELDS if k in record))
            else:
                print("  the portal search did not return this scene; falling back to the inferred layout")
            print(f"Asking the portal's data path for {options[0].id} (nothing is downloaded):")
            results = client.probe_portal_download(options[0], record)
            for r in results:
                print(f"  {r['path']}\n      status={r['status']}  {r['content_type']}  length={r['content_length']}"
                      f"  location={r['location']}\n      {r['starts_with']}")
            print("RESULT: set BHOONIDHI_DOWNLOAD_AUTH=portal to use it." if any(r["status"] == 200 for r in results)
                  else "RESULT: not accepted; paste this output.")
            return 0
        if args.probe_download_auth:
            options = live_fetch.candidates(bbox, wanted, client)
            if not options:
                print("No scene to probe: none covers the bbox.")
                return 1
            if args.settle:
                print(f"Waiting {args.settle:g} s after login before asking /download (does a new session need time?) ...")
                time.sleep(args.settle)
            print(f"Probing how /download wants the token, using {options[0].id} (nothing is downloaded):")
            results = client.probe_download_auth(options[0])
            for r in results:
                print(f"  {r['style']:13s} status={r['status']}  {r['content_type']}  {r['starts_with']}")
            worked = [r["style"] for r in results if r["status"] == 200]
            print(f"RESULT: set BHOONIDHI_DOWNLOAD_AUTH={worked[0]} to use it." if worked
                  else "RESULT: no style was accepted; paste this output so the client can be adjusted.")
            return 0
        if args.diagnose_download:
            options = live_fetch.candidates(bbox, wanted, client)
            if not options:
                print("No scene to diagnose: none covers the bbox.")
                return 1
            print(f"Asking /download about {options[0].id} (redirects not followed; the file is not downloaded):")
            for key, value in client.diagnose_download(options[0]).items():
                print(f"  {key:20s} {value}")
            return 0
        scene = live_fetch.ensure_scene(bbox, wanted, Path(args.data_dir), client=client,
                                        progress=lambda message, percent: print(f"  [{percent:3d}%] {message}"))
        print(f"Done: {scene.folder}  ({scene.date.date()}, {scene.valid_fraction(bbox):.0%} of the bbox has data)")
        print("Run scripts/build_cache.py if you want a small cache of it for the public image.")
        return 0
    except BhoonidhiError as e:
        print(f"Failed: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
