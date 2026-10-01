"""Exercise the installer in real PowerShell with disposable profile files."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from wslc_compose import __version__

POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(os.name != "nt" or not POWERSHELL,
                              reason="Windows PowerShell integration")
INSTALLER = Path(__file__).resolve().parents[1] / "install.ps1"


@pytest.fixture(autouse=True, params=list(dict.fromkeys(
    path for path in (shutil.which("pwsh"), shutil.which("powershell")) if path
)) or [None])
def powershell_edition(request, monkeypatch):
    monkeypatch.setitem(globals(), "POWERSHELL", request.param)


def run_ps(*args):
    return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", *args],
                          capture_output=True, text=True, timeout=30, check=False)


@pytest.fixture
def installed_wrapper(tmp_path):
    # Use the actual wrapper installed in the development environment.
    wrapper = Path(__file__).resolve().parents[1] / ".venv/Scripts/wslc.exe"
    if not wrapper.exists():
        pytest.skip("Install the project into .venv for Windows integration tests")
    destination = tmp_path / "wrapper's folder"
    destination.mkdir()
    shutil.copy2(wrapper, destination / "wslc.exe")
    shutil.copy2(wrapper.with_name("wslc-compose.exe"), destination / "wslc-compose.exe")
    return destination / "wslc.exe"


def install(profile, wrapper, *extra):
    return run_ps("-File", str(INSTALLER), "-SkipPackageInstall",
                  "-WrapperPath", str(wrapper), "-ProfilePath", str(profile), *extra)


def test_profile_install_repeat_dispatch_and_remove(tmp_path, installed_wrapper):
    profile = tmp_path / "profile's folder" / "profile.ps1"
    profile.parent.mkdir()
    original = "# Keep user settings\n$global:UserSetting = 'preserved'"
    profile.write_text(original, encoding="utf-8-sig")
    result = install(profile, installed_wrapper)
    assert result.returncode == 0, result.stderr
    first = profile.read_bytes()
    assert install(profile, installed_wrapper).returncode == 0
    assert profile.read_bytes() == first
    assert len(list(profile.parent.glob("*.wslc-compose-backup-*"))) == 1
    quoted = str(profile).replace("'", "''")
    result = run_ps("-Command", f". '{quoted}'; wslc compose version; "
                    "wslc-compose version; Write-Output $global:UserSetting")
    assert result.returncode == 0, result.stderr
    assert result.stdout.count(__version__) == 2
    assert "preserved" in result.stdout
    result = run_ps("-File", str(INSTALLER), "-Uninstall", "-ProfilePath", str(profile))
    assert result.returncode == 0, result.stderr
    assert profile.read_text(encoding="utf-8-sig") == original
    assert run_ps("-File", str(INSTALLER), "-Uninstall",
                  "-ProfilePath", str(profile)).returncode == 0


@pytest.mark.parametrize("original", [
    "function wslc { 'user function' }\n",
    "# >>> wslc-compose >>>\n",
    "function broken {\n",
])
def test_conflicting_or_broken_profile_is_untouched(tmp_path, installed_wrapper, original):
    profile = tmp_path / "profile.ps1"
    profile.write_text(original, encoding="utf-8")
    before = profile.read_bytes()
    assert install(profile, installed_wrapper).returncode != 0
    assert profile.read_bytes() == before


def test_failed_package_install_does_not_modify_profile(tmp_path):
    profile = tmp_path / "profile.ps1"
    profile.write_text("# User profile\n", encoding="utf-8")
    before = profile.read_bytes()
    result = run_ps("-File", str(INSTALLER), "-Python", "missing-python-for-test",
                    "-InstallDirectory", str(tmp_path / "venv"),
                    "-ProfilePath", str(profile))
    assert result.returncode != 0
    assert profile.read_bytes() == before
