"""Thin subprocess layer over the wslc CLI."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from functools import lru_cache
from typing import Dict, List, Optional

from wslc_compose import LABEL_PROJECT

_WIN_PATH_RE = re.compile(r"^([A-Za-z]:[\\/]|\\\\)")

WSLC_FALLBACKS = (
    "/mnt/c/Program Files/WSL/wslc.exe",
    "C:\\Program Files\\WSL\\wslc.exe",
)


class WslcError(RuntimeError):
    pass


def _is_self(path: str) -> bool:
    """True when `path` is our own `wslc` wrapper script, not the real CLI."""
    try:
        return os.path.realpath(path) == os.path.realpath(sys.argv[0])
    except OSError:
        return False


@lru_cache(maxsize=1)
def find_wslc() -> str:
    override = os.environ.get("WSLC_COMPOSE_BIN")
    if override:
        return override
    # Prefer Microsoft's known installation over PATH: uv/pip put our own
    # wslc shim first there. Comparing argv[0] alone misses Windows launchers
    # (which execute Python with a different argv[0]) and causes recursion.
    for path in WSLC_FALLBACKS:
        if os.path.isfile(path) and not _is_self(path):
            return path
    for name in ("wslc.exe", "wslc"):
        path = shutil.which(name)
        if path and not _is_self(path):
            return path
    raise WslcError(
        "wslc CLI not found. Install the WSL container preview "
        "(https://learn.microsoft.com/windows/wsl/wsl-container) or set WSLC_COMPOSE_BIN."
    )


def _running_in_wsl() -> bool:
    return sys.platform.startswith("linux") and (
        "WSL_DISTRO_NAME" in os.environ or "microsoft" in os.uname().release.lower()
    )


@lru_cache(maxsize=1)
def needs_path_translation() -> bool:
    """True when we call the Windows wslc.exe from inside a WSL distro."""
    return _running_in_wsl() and find_wslc().lower().endswith(".exe")


@lru_cache(maxsize=256)
def to_host_path(path: str) -> str:
    """Translate a Linux path to the Windows path wslc.exe expects."""
    if not needs_path_translation() or _WIN_PATH_RE.match(path):
        return path
    try:
        out = subprocess.run(
            ["wslpath", "-w", path], capture_output=True, text=True, check=True
        ).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.CalledProcessError):
        pass
    # manual fallback for /mnt/<drive>/... paths
    m = re.match(r"^/mnt/([a-zA-Z])(/.*)?$", path)
    if m:
        rest = (m.group(2) or "/").replace("/", "\\")
        return f"{m.group(1).upper()}:{rest}"
    print(
        f"wslc-compose: warning: cannot translate path {path!r} for wslc.exe; passing as-is",
        file=sys.stderr,
    )
    return path


def run(
    args: List[str],
    capture: bool = False,
    check: bool = True,
    dry_run: bool = False,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess:
    argv = [find_wslc()] + args
    if dry_run:
        print("+ " + " ".join(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")
    try:
        proc = subprocess.run(
            argv,
            capture_output=capture,
            text=capture,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        if check:
            raise WslcError(f"wslc {' '.join(args[:2])} timed out after {timeout:g}s") from exc
        return subprocess.CompletedProcess(argv, 124, exc.stdout or "", exc.stderr or "")
    if check and proc.returncode != 0:
        detail = (proc.stderr or "").strip() if capture else ""
        raise WslcError(
            f"wslc {' '.join(args[:2])} failed (exit {proc.returncode})"
            + (f": {detail}" if detail else "")
        )
    return proc


# Windows-side errors wslc can emit transiently: right after a stop the
# container's kernel object is not always released yet (ERROR_ALREADY_EXISTS
# on the next start), and the preview's session store rejects overlapping
# invocations (ERROR_SHARING_VIOLATION). A bounded retry rides out the race;
# a persistent machine-wide lock (docs/MIGRATION.md) still fails after the
# last attempt.
TRANSIENT_ERROR_MARKERS = ("ERROR_ALREADY_EXISTS", "ERROR_SHARING_VIOLATION")
TRANSIENT_RETRIES = 5
TRANSIENT_DELAY = 2.0


def run_retried(
    args: List[str],
    dry_run: bool = False,
    retries: int = TRANSIENT_RETRIES,
    delay: float = TRANSIENT_DELAY,
) -> subprocess.CompletedProcess:
    """`run(capture=True)` that retries failures matching TRANSIENT_ERROR_MARKERS."""
    for attempt in range(1, retries + 1):
        try:
            return run(args, capture=True, dry_run=dry_run)
        except WslcError as exc:
            transient = any(marker in str(exc) for marker in TRANSIENT_ERROR_MARKERS)
            if not transient or attempt == retries:
                raise
            print(
                f"wslc-compose: wslc {' '.join(args[:2])} hit a transient error, "
                f"retrying in {delay:g}s ({attempt}/{retries - 1})...",
                file=sys.stderr,
            )
            time.sleep(delay)
    raise AssertionError("unreachable")


def popen(args: List[str], **kwargs) -> subprocess.Popen:
    return subprocess.Popen([find_wslc()] + args, **kwargs)


def capture_json(args: List[str]):
    proc = run(args, capture=True)
    text = proc.stdout.strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # WSL 3.x list commands emit one JSON object per line, while older
        # previews and inspect emit a JSON array. Never accept a partial list.
        try:
            records = [json.loads(line) for line in text.splitlines() if line.strip()]
            if records and all(isinstance(record, dict) for record in records):
                return records
        except json.JSONDecodeError:
            pass
        raise WslcError(
            f"unexpected non-JSON output from wslc {' '.join(args)}: {exc}"
        ) from exc


def _json_records(result) -> List[dict]:
    """Normalize arrays and single-record JSON list output."""
    if isinstance(result, dict):
        return [result]
    return result if isinstance(result, list) else []


# --- queries ---------------------------------------------------------------


def list_project_containers(project: str, all_states: bool = True) -> List[dict]:
    args = ["list", "--format", "json", "-f", f"label={LABEL_PROJECT}={project}"]
    if all_states:
        args.insert(1, "-a")
    result = capture_json(args)
    return _json_records(result)


def inspect(object_id: str) -> Optional[dict]:
    try:
        result = capture_json(["inspect", object_id])
    except WslcError:
        return None
    if isinstance(result, list):
        return result[0] if result else None
    return result


def inspect_many(ids: List[str]) -> Dict[str, dict]:
    return {i: data for i in ids if (data := inspect(i)) is not None}


def _names_from_table(args: List[str], column: str = "NAME") -> List[str]:
    """Parse names out of a wslc table output (network/volume list)."""
    proc = run(args, capture=True)
    lines = proc.stdout.splitlines()
    if not lines:
        return []
    header = lines[0]
    idx = header.find(column)
    if idx < 0:
        return []
    names = []
    for line in lines[1:]:
        if len(line) > idx:
            names.append(line[idx:].split()[0])
    return names


def image_exists(name: str) -> bool:
    repo, _, tag = name.partition(":")
    try:
        images = capture_json(["images", "--format", "json"])
    except WslcError:
        return False
    for img in _json_records(images):
        if img.get("Repository") == repo and (not tag or img.get("Tag") == tag):
            return True
    return False


def network_names() -> List[str]:
    return _names_from_table(["network", "list"])


def volume_names() -> List[str]:
    return _names_from_table(["volume", "list"], column="VOLUME NAME")


def _resource_create_args(
    kind: str,
    name: str,
    driver: Optional[str],
    driver_opts: Optional[Dict[str, str]],
    labels: Optional[Dict[str, str]],
) -> List[str]:
    args = [kind, "create"]
    if driver:
        args += ["--driver", driver]
    for key, value in sorted((driver_opts or {}).items()):
        args += ["--opt", f"{key}={value}"]
    for key, value in sorted((labels or {}).items()):
        args += ["--label", f"{key}={value}"]
    args.append(name)
    return args


def ensure_network(
    name: str,
    dry_run: bool = False,
    driver: Optional[str] = None,
    driver_opts: Optional[Dict[str, str]] = None,
    labels: Optional[Dict[str, str]] = None,
) -> bool:
    if name in network_names():
        return False
    run(_resource_create_args("network", name, driver, driver_opts, labels), dry_run=dry_run)
    return True


def ensure_volume(
    name: str,
    dry_run: bool = False,
    driver: Optional[str] = None,
    driver_opts: Optional[Dict[str, str]] = None,
    labels: Optional[Dict[str, str]] = None,
) -> bool:
    if name in volume_names():
        return False
    run(_resource_create_args("volume", name, driver, driver_opts, labels), dry_run=dry_run)
    return True
