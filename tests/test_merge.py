from wslc_compose.cli import build_parser
from wslc_compose.loader import load_project, merge_compose_models


def test_cli_accepts_repeated_compose_files():
    ns = build_parser().parse_args(
        ["-f", "compose.yaml", "-f", "compose.dev.yaml", "config"]
    )

    assert ns.file == ["compose.yaml", "compose.dev.yaml"]


def test_multiple_files_merge_service_model(tmp_path):
    base = tmp_path / "compose.yaml"
    override = tmp_path / "compose.dev.yaml"
    base.write_text(
        """
name: demo
services:
  app:
    image: base:latest
    command: [base]
    environment:
      FIRST: base
      SHARED: base
    ports:
      - "8080:80"
    volumes:
      - data:/data
volumes:
  data:
"""
    )
    override.write_text(
        """
services:
  app:
    image: override:latest
    command: [override]
    environment:
      SHARED: override
      SECOND: override
    ports:
      - "8080:80"
      - "8081:81"
    volumes:
      - other:/data
volumes:
  other:
"""
    )

    project = load_project([str(base), str(override)])
    service = project.services["app"]

    assert service.image == "override:latest"
    assert service.command == ["override"]
    assert service.environment == {
        "FIRST": "base",
        "SHARED": "override",
        "SECOND": "override",
    }
    assert [port.to_flag() for port in service.ports] == ["8080:80", "8081:81"]
    assert [(mount.source, mount.target) for mount in service.volumes] == [
        ("demo_other", "/data")
    ]


def test_override_paths_are_relative_to_first_compose_file(tmp_path):
    override_dir = tmp_path / "overrides"
    override_dir.mkdir()
    (tmp_path / "assets").mkdir()
    base = tmp_path / "compose.yaml"
    override = override_dir / "compose.dev.yaml"
    base.write_text(
        """
services:
  app:
    image: base
"""
    )
    override.write_text(
        """
services:
  app:
    volumes:
      - ./assets:/assets
"""
    )

    project = load_project([str(base), str(override)])

    assert project.services["app"].volumes[0].source == str(tmp_path / "assets")


def test_healthcheck_test_is_replaced_instead_of_appended():
    merged = merge_compose_models(
        {"services": {"app": {"healthcheck": {"test": ["CMD", "base"]}}}},
        {"services": {"app": {"healthcheck": {"test": ["CMD", "override"]}}}},
    )

    assert merged["services"]["app"]["healthcheck"]["test"] == ["CMD", "override"]


def test_reset_tag_clears_inherited_sequences_and_mappings(tmp_path):
    base = tmp_path / "compose.yaml"
    override = tmp_path / "compose.override.yaml"
    base.write_text(
        """
services:
  app:
    image: app
    environment: {KEEP: no}
    ports: ["8080:80"]
"""
    )
    override.write_text(
        """
services:
  app:
    environment: !reset null
    ports: !reset []
"""
    )

    service = load_project([str(base), str(override)]).services["app"]

    assert service.environment == {}
    assert service.ports == []


def test_override_tag_bypasses_unique_resource_merge(tmp_path):
    base = tmp_path / "compose.yaml"
    override = tmp_path / "compose.override.yaml"
    base.write_text(
        """
services:
  app:
    image: app
    ports: ["8080:80", "8081:81"]
"""
    )
    override.write_text(
        """
services:
  app:
    ports: !override ["9090:90"]
"""
    )

    service = load_project([str(base), str(override)]).services["app"]

    assert [port.to_flag() for port in service.ports] == ["9090:90"]


def test_tagged_values_are_interpolated(tmp_path, monkeypatch):
    base = tmp_path / "compose.yaml"
    override = tmp_path / "compose.override.yaml"
    base.write_text(
        """
services:
  app:
    image: app
    command: [base]
"""
    )
    override.write_text(
        """
services:
  app:
    command: !override [echo, "${MESSAGE}"]
"""
    )
    monkeypatch.setenv("MESSAGE", "ready")

    service = load_project([str(base), str(override)]).services["app"]

    assert service.command == ["echo", "ready"]
