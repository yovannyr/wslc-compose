import argparse
import subprocess

from wslc_compose import cli
from wslc_compose.flags import run_args
from wslc_compose.loader import load_project


def _load_anonymous_project(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
name: demo
services:
  app:
    image: app
    volumes:
      - /data
      - type: volume
        target: /cache
"""
    )
    return load_project(str(tmp_path / "compose.yaml"))


def test_anonymous_volumes_get_stable_project_scoped_names(tmp_path):
    first = _load_anonymous_project(tmp_path)
    second = load_project(str(tmp_path / "compose.yaml"))

    first_mounts = first.services["app"].volumes
    second_mounts = second.services["app"].volumes
    assert all(mount.anonymous for mount in first_mounts)
    assert [mount.source for mount in first_mounts] == [
        mount.source for mount in second_mounts
    ]
    assert first_mounts[0].source.startswith("demo_app_anonymous_")
    assert first_mounts[0].source != first_mounts[1].source


def test_anonymous_volume_name_is_unique_per_replica(tmp_path):
    project = _load_anonymous_project(tmp_path)
    service = project.services["app"]

    first_args = run_args(project, service, index=1)
    second_args = run_args(project, service, index=2)

    first_source = service.volumes[0].source
    assert f"{first_source}-1:/data" in first_args
    assert f"{first_source}-2:/data" in second_args


def test_one_off_resource_setup_uses_unique_suffix(tmp_path, monkeypatch):
    project = _load_anonymous_project(tmp_path)
    service = project.services["app"]
    created = []
    monkeypatch.setattr(cli.engine, "ensure_network", lambda name, dry_run=False: False)
    monkeypatch.setattr(
        cli.engine,
        "ensure_volume",
        lambda name, dry_run=False: created.append(name) or True,
    )

    cli._ensure_service_resources(project, service, False, "task1234")

    assert created == [
        f"{service.volumes[0].source}-task1234",
        f"{service.volumes[1].source}-task1234",
    ]


def test_down_volumes_removes_anonymous_volume_instances(tmp_path, monkeypatch):
    project = _load_anonymous_project(tmp_path)
    prefix = project.services["app"].volumes[0].source
    calls = []
    monkeypatch.setattr(cli, "_load", lambda _: project)
    monkeypatch.setattr(cli, "_project_containers", lambda _: [])
    monkeypatch.setattr(cli.engine, "network_names", list)
    monkeypatch.setattr(
        cli.engine,
        "volume_names",
        lambda: [f"{prefix}-1", f"{prefix}-2", "unrelated"],
    )

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cli.engine, "run", fake_run)

    assert cli.cmd_down(
        argparse.Namespace(volumes=True, timeout=10, dry_run=False)
    ) == 0
    assert calls == [
        ["volume", "remove", f"{prefix}-1"],
        ["volume", "remove", f"{prefix}-2"],
    ]
