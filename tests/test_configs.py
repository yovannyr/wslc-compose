from pathlib import Path

import pytest

from wslc_compose.flags import run_args
from wslc_compose.loader import ComposeError, _materialize_config, load_project


def test_file_content_and_environment_configs_are_mounted(tmp_path, monkeypatch):
    (tmp_path / "app.conf").write_text("from-file")
    monkeypatch.setenv("INLINE_VALUE", "from-environment")
    monkeypatch.setenv("APP_MODE", "development")
    (tmp_path / "compose.yaml").write_text(
        """
name: demo
services:
  app:
    image: app
    configs:
      - file_config
      - source: inline_config
        target: /etc/app/inline.conf
        mode: 0400
      - environment_config
configs:
  file_config:
    file: ./app.conf
  inline_config:
    content: |
      mode=${APP_MODE}
  environment_config:
    environment: INLINE_VALUE
"""
    )

    project = load_project(str(tmp_path / "compose.yaml"), strict_unsupported=True)
    service = project.services["app"]

    assert service.configs[0].target == "/file_config"
    assert service.configs[0].file == str(tmp_path / "app.conf")
    assert service.configs[1].target == "/etc/app/inline.conf"
    assert Path(service.configs[1].file).read_text() == "mode=development\n"
    assert service.configs[2].target == "/environment_config"
    assert Path(service.configs[2].file).read_text() == "from-environment"
    assert "mode cannot be enforced" in "\n".join(project.warnings)
    assert "from-environment" not in repr(project)

    args = run_args(project, service, path_mapper=lambda path: f"WIN({path})")
    assert f"WIN({tmp_path / 'app.conf'}):/file_config:ro" in args
    assert f"WIN({service.configs[1].file}):/etc/app/inline.conf:ro" in args


def test_external_config_is_rejected(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: app
    configs: [shared]
configs:
  shared:
    external: true
"""
    )

    with pytest.raises(ComposeError, match="no config object store"):
        load_project(str(tmp_path / "compose.yaml"))


def test_environment_config_requires_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_CONFIG_VALUE", raising=False)
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: app
    configs: [settings]
configs:
  settings:
    environment: MISSING_CONFIG_VALUE
"""
    )

    with pytest.raises(ComposeError, match="environment variable.*is not set"):
        load_project(str(tmp_path / "compose.yaml"))


def test_config_target_must_not_conflict_with_volume(tmp_path):
    (tmp_path / "settings").write_text("value")
    (tmp_path / "mounted").mkdir()
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: app
    volumes:
      - ./mounted:/settings
    configs: [settings]
configs:
  settings:
    file: ./settings
"""
    )

    with pytest.raises(ComposeError, match="config target '/settings' conflicts"):
        load_project(str(tmp_path / "compose.yaml"))


def test_materialized_config_path_is_stable():
    # Stability is important because the path contributes to the service config hash.
    assert _materialize_config("settings", "same") == _materialize_config("settings", "same")
