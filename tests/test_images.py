import subprocess

import pytest

from wslc_compose import cli
from wslc_compose.loader import ComposeError, load_project
from wslc_compose.model import BuildConfig, Project, Service


def test_pull_policy_is_loaded_and_alias_is_normalized(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: app
    pull_policy: if_not_present
"""
    )

    project = load_project(str(tmp_path / "compose.yaml"))

    assert project.services["app"].pull_policy == "missing"


def test_invalid_pull_policy_is_rejected(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: app
    pull_policy: sometimes
"""
    )

    with pytest.raises(ComposeError, match="invalid pull_policy"):
        load_project(str(tmp_path / "compose.yaml"))


def _project_with(service):
    project = Project(name="demo", directory=".")
    project.services[service.name] = service
    return project


def _capture_engine(monkeypatch, image_exists):
    calls = []
    monkeypatch.setattr(cli.engine, "image_exists", lambda _: image_exists)

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cli.engine, "run", fake_run)
    monkeypatch.setattr(cli.engine, "to_host_path", lambda path: path)
    return calls


def test_missing_build_image_is_built(monkeypatch):
    service = Service(
        name="app",
        build=BuildConfig(context="/src"),
    )
    calls = _capture_engine(monkeypatch, image_exists=False)

    cli._prepare_service_image(_project_with(service), service)

    assert calls == [["build", "-t", "demo-app", "/src"]]


def test_always_policy_pulls_even_existing_image(monkeypatch):
    service = Service(name="app", image="registry/app:1", pull_policy="always")
    calls = _capture_engine(monkeypatch, image_exists=True)

    cli._prepare_service_image(_project_with(service), service)

    assert calls == [["pull", "registry/app:1"]]


def test_latest_is_refreshed_with_missing_policy(monkeypatch):
    service = Service(name="app", image="registry/app:latest", pull_policy="missing")
    calls = _capture_engine(monkeypatch, image_exists=True)

    cli._prepare_service_image(_project_with(service), service)

    assert calls == [["pull", "registry/app:latest"]]


def test_never_policy_rejects_missing_image(monkeypatch):
    service = Service(name="app", image="registry/app:1", pull_policy="never")
    calls = _capture_engine(monkeypatch, image_exists=False)

    with pytest.raises(ComposeError, match="pull_policy is never"):
        cli._prepare_service_image(_project_with(service), service)

    assert calls == []


def test_build_policy_forces_rebuild(monkeypatch):
    service = Service(
        name="app",
        image="registry/app:1",
        build=BuildConfig(context="/src"),
        pull_policy="build",
    )
    calls = _capture_engine(monkeypatch, image_exists=True)

    cli._prepare_service_image(_project_with(service), service)

    assert calls == [["build", "-t", "registry/app:1", "/src"]]


def test_no_build_rejects_explicit_build(monkeypatch):
    service = Service(name="app", build=BuildConfig(context="/src"))
    _capture_engine(monkeypatch, image_exists=False)

    with pytest.raises(ComposeError, match="--build and --no-build"):
        cli._prepare_service_image(
            _project_with(service), service, build=True, no_build=True
        )


def test_no_build_rejects_missing_build_only_image(monkeypatch):
    service = Service(name="app", build=BuildConfig(context="/src"))
    _capture_engine(monkeypatch, image_exists=False)

    with pytest.raises(ComposeError, match="image build is disabled"):
        cli._prepare_service_image(
            _project_with(service), service, no_build=True
        )
