"""scripts/fetch_scene.py is the user's way to check their own Bhoonidhi access: it must not crash or leak."""
import importlib.util
import sys
from pathlib import Path

import pytest

from app.bhoonidhi_client import BhoonidhiClient
from tests.live_helpers import FakeBhoonidhi, feature, make_zip

SCENE = "R2323MAY2026078317010000060PSANSTUC00GTDF"


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    path = Path(__file__).resolve().parent.parent / "scripts" / "fetch_scene.py"
    spec = importlib.util.spec_from_file_location("fetch_scene", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    api = FakeBhoonidhi([feature(SCENE)], archives={SCENE: make_zip(tmp_path, SCENE)})
    monkeypatch.setattr(mod, "BhoonidhiClient",
                        lambda u, p: BhoonidhiClient(u, p, session=api, sleep=lambda s: None))
    monkeypatch.chdir(tmp_path)  # no stray .env is picked up
    monkeypatch.setenv("BHOONIDHI_USER", "student")
    monkeypatch.setenv("BHOONIDHI_PASSWORD", "correct-horse")

    def go(*argv):
        monkeypatch.setattr(sys, "argv", ["fetch_scene.py", *argv])
        code = mod.main()
        return code, capsys.readouterr().out, api
    return go


def test_dry_run_logs_in_and_searches_but_downloads_nothing(run, tmp_path):
    code, out, api = run("--bbox", "17.49,77.49,17.51,77.51", "--dry-run")
    assert code == 0 and "Login and search worked. 1 online scene(s)" in out and SCENE in out
    assert "covers the bbox" in out and "Dry run: nothing downloaded" in out
    assert api.count("/download") == 0 and not (tmp_path / "data").exists()
    assert "correct-horse" not in out


def test_full_run_downloads_into_the_data_dir(run, tmp_path):
    code, out, api = run("--bbox", "17.49,77.49,17.51,77.51", "--date", "2026-05-23")
    assert code == 0 and "Done:" in out and "100% of the bbox has data" in out
    assert (tmp_path / "data" / SCENE / "BAND_META.txt").exists()
    assert "correct-horse" not in out


def test_bad_arguments_and_missing_credentials_are_explained(run, monkeypatch):
    assert run("--bbox", "not,numbers", "--dry-run")[0] == 2
    assert run("--bbox", "1,2,3,4", "--date", "yesterday")[0] == 2
    monkeypatch.delenv("BHOONIDHI_PASSWORD")
    code, out, _ = run("--bbox", "17.49,77.49,17.51,77.51")
    assert code == 2 and "BHOONIDHI_USER and BHOONIDHI_PASSWORD" in out


def test_api_failures_exit_nonzero_with_a_message(run, monkeypatch):
    monkeypatch.setenv("BHOONIDHI_PASSWORD", "wrong-password")
    code, out, _ = run("--bbox", "17.49,77.49,17.51,77.51", "--dry-run")
    assert code == 1 and "authentication failed" in out and "wrong-password" not in out


def test_diagnose_download_prints_how_the_server_answers_and_downloads_nothing(run, tmp_path):
    code, out, api = run("--bbox", "17.49,77.49,17.51,77.51", "--date", "2026-05-23", "--diagnose-download")
    assert code == 0 and f"Asking /download about {SCENE}" in out
    assert "status" in out and "200" in out and "PK (a zip archive)" in out
    assert not (tmp_path / "data").exists()  # nothing was saved
    assert "correct-horse" not in out and "token-" not in out and "Bearer" not in out
