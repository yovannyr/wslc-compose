"""Exercise the installer in real PowerShell with disposable profile files."""

import os
import shutil
import subprocess
import uuid
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
    old_block = (
        "\r\n# >>> wslc-compose >>>\r\n"
        "function global:wslc { & 'C:\\old-appdata\\wslc.exe' @args }\r\n"
        "# <<< wslc-compose <<<\r\n"
    )
    profile.write_bytes((original + old_block).encode("utf-8-sig"))
    result = install(profile, installed_wrapper)
    assert result.returncode == 0, result.stderr
    first = profile.read_bytes()
    assert b"old-appdata" not in first
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


def test_default_environment_is_outside_virtualized_appdata():
    # Read the parameter AST without installing into the actual user directory.
    quoted = str(INSTALLER).replace("'", "''")
    result = run_ps("-Command", "$tokens = $null; $errors = $null; "
                    "[System.Management.Automation.Language.Parser]::ParseFile("
                    f"'{quoted}', [ref]$tokens, [ref]$errors).ParamBlock.Parameters | "
                    "Where-Object { $_.Name.VariablePath.UserPath -eq 'InstallDirectory' } | "
                    "ForEach-Object { $_.DefaultValue.Extent.Text }")
    assert result.returncode == 0, result.stderr
    assert "$env:USERPROFILE" in result.stdout
    assert "$env:LOCALAPPDATA" not in result.stdout


@pytest.fixture
def cmd_registry():
    import winreg

    subkey = rf"Software\WslcComposeTests\{uuid.uuid4().hex}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey):
        pass
    yield subkey, winreg
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)


def query_registry(subkey, registry, name):
    with registry.OpenKey(registry.HKEY_CURRENT_USER, subkey) as key:
        return registry.QueryValueEx(key, name)


@pytest.mark.parametrize("value_kind", ["absent", "empty", "string", "expand"])
def test_cmd_preserves_autorun_repeat_update_and_remove(
    tmp_path, installed_wrapper, cmd_registry, value_kind
):
    subkey, registry = cmd_registry
    kind = registry.REG_EXPAND_SZ if value_kind == "expand" else registry.REG_SZ
    original = '' if value_kind == "empty" else 'echo prior-autorun'
    if value_kind != "absent":
        with registry.OpenKey(registry.HKEY_CURRENT_USER, subkey, 0,
                              registry.KEY_SET_VALUE) as key:
            registry.SetValueEx(key, "AutoRun", 0, kind, original)
    profile = tmp_path / "profile.ps1"
    extra = ("-EnableCmd", "-CmdRegistryPath", "HKCU:\\" + subkey)
    result = install(profile, installed_wrapper, *extra)
    assert result.returncode == 0, result.stderr
    first = query_registry(subkey, registry, "AutoRun")
    assert first[1] == kind
    assert install(profile, installed_wrapper, *extra).returncode == 0
    assert query_registry(subkey, registry, "AutoRun") == first
    result = subprocess.run(
        'cmd.exe /d /c ' + first[0] + " & wslc compose version",
        capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
    assert __version__ in result.stdout
    if original and value_kind != "absent":
        assert "prior-autorun" in result.stdout
    new_dir = tmp_path / "updated wrapper"
    new_dir.mkdir()
    shutil.copy2(installed_wrapper, new_dir / "wslc.exe")
    shutil.copy2(installed_wrapper.with_name("wslc-compose.exe"), new_dir / "wslc-compose.exe")
    assert install(profile, new_dir / "wslc.exe", *extra).returncode == 0
    updated = query_registry(subkey, registry, "AutoRun")[0]
    assert updated.count('set "PATH=') == 1
    assert str(new_dir) in updated
    result = run_ps("-File", str(INSTALLER), "-Uninstall", "-ProfilePath", str(profile), *extra)
    assert result.returncode == 0, result.stderr
    if value_kind == "absent":
        with pytest.raises(FileNotFoundError):
            query_registry(subkey, registry, "AutoRun")
    else:
        assert query_registry(subkey, registry, "AutoRun") == (original, kind)
    with pytest.raises(FileNotFoundError):
        query_registry(subkey, registry, "WslcComposeIntegration")


def test_cmd_refuses_to_overwrite_external_changes(tmp_path, installed_wrapper, cmd_registry):
    subkey, registry = cmd_registry
    profile = tmp_path / "profile.ps1"
    extra = ("-EnableCmd", "-CmdRegistryPath", "HKCU:\\" + subkey)
    assert install(profile, installed_wrapper, *extra).returncode == 0
    changed = query_registry(subkey, registry, "AutoRun")[0] + " & echo external"
    with registry.OpenKey(registry.HKEY_CURRENT_USER, subkey, 0,
                          registry.KEY_SET_VALUE) as key:
        registry.SetValueEx(key, "AutoRun", 0, registry.REG_SZ, changed)
    assert install(profile, installed_wrapper, *extra).returncode != 0
    assert query_registry(subkey, registry, "AutoRun")[0] == changed
    result = run_ps("-File", str(INSTALLER), "-Uninstall", "-ProfilePath", str(profile), *extra)
    assert result.returncode != 0
    assert query_registry(subkey, registry, "AutoRun")[0] == changed


def test_cmd_rejects_expanding_paths_without_changing_registry(
    tmp_path, installed_wrapper, cmd_registry
):
    subkey, registry = cmd_registry
    bad_dir = tmp_path / "bad%path"
    bad_dir.mkdir()
    shutil.copy2(installed_wrapper, bad_dir / "wslc.exe")
    shutil.copy2(installed_wrapper.with_name("wslc-compose.exe"), bad_dir / "wslc-compose.exe")
    profile = tmp_path / "profile.ps1"
    result = install(profile, bad_dir / "wslc.exe", "-EnableCmd",
                     "-CmdRegistryPath", "HKCU:\\" + subkey)
    assert result.returncode != 0
    with pytest.raises(FileNotFoundError):
        query_registry(subkey, registry, "AutoRun")
    assert not profile.exists()
