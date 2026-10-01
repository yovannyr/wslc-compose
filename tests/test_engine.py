import json
import subprocess

import pytest

from wslc_compose import engine
from wslc_compose.engine import WslcError


def _ok(args):
    return subprocess.CompletedProcess(args, 0, "", "")


def test_find_wslc_prefers_native_installation_over_shim(monkeypatch):
    engine.find_wslc.cache_clear()
    monkeypatch.delenv("WSLC_COMPOSE_BIN", raising=False)
    monkeypatch.setattr(engine.os.path, "isfile", lambda path:
                        path == "C:\\Program Files\\WSL\\wslc.exe")
    monkeypatch.setattr(engine.shutil, "which", lambda _: "venv/Scripts/wslc.exe")
    try:
        assert engine.find_wslc() == "C:\\Program Files\\WSL\\wslc.exe"
    finally:
        engine.find_wslc.cache_clear()


def test_find_wslc_preserves_explicit_override(monkeypatch):
    engine.find_wslc.cache_clear()
    monkeypatch.setenv("WSLC_COMPOSE_BIN", "custom/wslc.exe")
    try:
        assert engine.find_wslc() == "custom/wslc.exe"
    finally:
        engine.find_wslc.cache_clear()


@pytest.mark.parametrize("output, expected", [
    ('[{"Id": "a"}, {"Id": "b"}]', [{"Id": "a"}, {"Id": "b"}]),
    ('{"ID": "a"}\n{"ID": "b"}\n', [{"ID": "a"}, {"ID": "b"}]),
    ('{"ID": "a"}', {"ID": "a"}),
    ('[\n  {"Id": "a"}\n]', [{"Id": "a"}]),
    ('\n', []),
])
def test_capture_json_formats(monkeypatch, output, expected):
    monkeypatch.setattr(engine, "run", lambda args, **kw:
                        subprocess.CompletedProcess(args, 0, output, ""))
    assert engine.capture_json(["list", "--format", "json"]) == expected


@pytest.mark.parametrize("output", [
    '{"ID": "a"}\ninvalid', '{"ID": "a"}\n42', 'not json',
])
def test_capture_json_rejects_malformed_records(monkeypatch, output):
    monkeypatch.setattr(engine, "run", lambda args, **kw:
                        subprocess.CompletedProcess(args, 0, output, ""))
    with pytest.raises(WslcError, match="unexpected non-JSON"):
        engine.capture_json(["list"])


@pytest.mark.parametrize("count", [0, 1, 2])
def test_current_list_and_image_output(monkeypatch, count):
    def fake_run(args, **kwargs):
        record = {"ID": "abc", "Names": "demo-app-1"} if args[0] == "list" else {
            "Repository": "demo", "Tag": "1"
        }
        output = "\n".join(json.dumps(record) for _ in range(count))
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(engine, "run", fake_run)
    assert len(engine.list_project_containers("demo")) == count
    assert engine.image_exists("demo:1") == bool(count)
    assert not engine.image_exists("demo:2")


def test_run_retried_recovers_from_transient_error(monkeypatch):
    calls = []

    def fake_run(args, capture=False, check=True, dry_run=False):
        calls.append(list(args))
        if len(calls) == 1:
            raise WslcError(
                "wslc start foo failed (exit 1): Impossible de créer un fichier "
                "déjà existant. Code d'erreur : ERROR_ALREADY_EXISTS"
            )
        return _ok(args)

    monkeypatch.setattr(engine, "run", fake_run)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)

    proc = engine.run_retried(["start", "foo"])
    assert proc.returncode == 0
    assert len(calls) == 2


def test_run_retried_gives_up_after_max_attempts(monkeypatch):
    calls = []

    def fake_run(args, capture=False, check=True, dry_run=False):
        calls.append(list(args))
        raise WslcError("wslc start foo failed (exit 1): ERROR_SHARING_VIOLATION")

    monkeypatch.setattr(engine, "run", fake_run)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)

    with pytest.raises(WslcError):
        engine.run_retried(["start", "foo"], retries=3)
    assert len(calls) == 3


def test_run_retried_does_not_retry_other_errors(monkeypatch):
    calls = []

    def fake_run(args, capture=False, check=True, dry_run=False):
        calls.append(list(args))
        raise WslcError("wslc start foo failed (exit 1): no such container")

    monkeypatch.setattr(engine, "run", fake_run)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)

    with pytest.raises(WslcError):
        engine.run_retried(["start", "foo"])
    assert len(calls) == 1
