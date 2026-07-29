import pytest

from wslc_compose.loader import ComposeError, load_project


def test_include_loads_services_and_resolves_their_own_paths(tmp_path):
    module = tmp_path / "module"
    module.mkdir()
    (module / "data").mkdir()
    (tmp_path / "compose.yaml").write_text(
        """
name: demo
include:
  - ./module/compose.yaml
"""
    )
    (module / "compose.yaml").write_text(
        """
services:
  worker:
    image: worker
    volumes:
      - ./data:/data
"""
    )

    project = load_project(str(tmp_path / "compose.yaml"))

    assert project.services["worker"].volumes[0].source == str(module / "data")


def test_include_long_syntax_honors_project_directory_and_env_file(tmp_path):
    module = tmp_path / "module"
    runtime = tmp_path / "runtime"
    module.mkdir()
    runtime.mkdir()
    (runtime / "assets").mkdir()
    (runtime / "module.env").write_text("MODULE_IMAGE=worker:dev")
    (tmp_path / "compose.yaml").write_text(
        """
include:
  - path: ./module/compose.yaml
    project_directory: ./runtime
    env_file: ./module.env
"""
    )
    (module / "compose.yaml").write_text(
        """
services:
  worker:
    image: ${MODULE_IMAGE}
    volumes:
      - ./assets:/assets
"""
    )

    project = load_project(str(tmp_path / "compose.yaml"))

    assert project.services["worker"].image == "worker:dev"
    assert project.services["worker"].volumes[0].source == str(runtime / "assets")


def test_circular_include_is_rejected(tmp_path):
    (tmp_path / "compose.yaml").write_text("include: ./other.yaml\n")
    (tmp_path / "other.yaml").write_text("include: ./compose.yaml\n")

    with pytest.raises(ComposeError, match="circular include"):
        load_project(str(tmp_path / "compose.yaml"))


def test_extends_merges_environment_and_replaces_command(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  base:
    image: app
    command: [base]
    environment:
      BASE: yes
      SHARED: base
  app:
    extends: base
    command: [child]
    environment:
      SHARED: child
      CHILD: yes
"""
    )

    project = load_project(str(tmp_path / "compose.yaml"))
    service = project.services["app"]

    assert service.image == "app"
    assert service.command == ["child"]
    assert service.environment == {
        "BASE": "True",
        "SHARED": "child",
        "CHILD": "True",
    }


def test_extends_can_reference_an_external_compose_file(tmp_path):
    (tmp_path / "base.yaml").write_text(
        """
services:
  common:
    image: app:base
    environment:
      BASE: enabled
"""
    )
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    extends:
      file: ./base.yaml
      service: common
    environment:
      CHILD: enabled
"""
    )

    project = load_project(str(tmp_path / "compose.yaml"))

    assert project.services["app"].image == "app:base"
    assert project.services["app"].environment == {
        "BASE": "enabled",
        "CHILD": "enabled",
    }


def test_circular_extends_is_rejected(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  one:
    image: app
    extends: two
  two:
    image: app
    extends: one
"""
    )

    with pytest.raises(ComposeError, match="circular extends"):
        load_project(str(tmp_path / "compose.yaml"))
