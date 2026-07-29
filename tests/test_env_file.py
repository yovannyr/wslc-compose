import pytest

from wslc_compose.loader import ComposeError, load_project


def test_env_file_long_syntax_supports_required_and_raw(tmp_path):
    raw = tmp_path / "raw.env"
    raw.write_text("TOKEN=$not-interpolated\n")
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: app
    env_file:
      - path: ./missing.env
        required: false
      - path: ./raw.env
        format: raw
"""
    )

    service = load_project(str(tmp_path / "compose.yaml")).services["app"]

    assert service.env_files == [str(raw)]
    assert raw.read_text() == "TOKEN=$not-interpolated\n"


def test_missing_required_env_file_is_rejected(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: app
    env_file:
      path: ./missing.env
      required: true
"""
    )

    with pytest.raises(ComposeError, match="required env_file not found"):
        load_project(str(tmp_path / "compose.yaml"))


def test_invalid_env_file_format_is_rejected(tmp_path):
    env_file = tmp_path / "app.env"
    env_file.write_text("MODE=test\n")
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: app
    env_file:
      path: ./app.env
      format: json
"""
    )

    with pytest.raises(ComposeError, match="format must be compose or raw"):
        load_project(str(tmp_path / "compose.yaml"))


def test_env_file_long_path_is_absolutized_for_include(tmp_path):
    module = tmp_path / "module"
    module.mkdir()
    (module / "app.env").write_text("MODE=module\n")
    (module / "compose.yaml").write_text(
        """
services:
  app:
    image: app
    env_file:
      path: ./app.env
      format: raw
"""
    )
    (tmp_path / "compose.yaml").write_text("include: ./module/compose.yaml\nservices: {}\n")

    service = load_project(str(tmp_path / "compose.yaml")).services["app"]

    assert service.env_files == [str(module / "app.env")]
