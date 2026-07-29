import argparse
import subprocess

from wslc_compose import cli
from wslc_compose.loader import load_project
from wslc_compose.model import Dependency, Project, Service


def _project():
    project = Project(name="demo", directory=".")
    db = Service(name="db", image="db", stop_grace_period=5.0)
    api = Service(
        name="api",
        image="api",
        depends_on=["db"],
        dependencies={"db": Dependency()},
        stop_grace_period=2.2,
    )
    project.services = {"db": db, "api": api}
    return project


def _containers():
    return [
        {
            "name": "demo-db-1",
            "service": "db",
            "index": 1,
            "running": True,
        },
        {
            "name": "demo-api-1",
            "service": "api",
            "index": 1,
            "running": True,
        },
    ]


def test_stop_grace_period_is_loaded(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: app
    stop_grace_period: 1.5s
"""
    )

    project = load_project(str(tmp_path / "compose.yaml"))

    assert project.services["app"].stop_grace_period == 1.5


def test_down_stops_in_reverse_dependency_order(monkeypatch):
    project = _project()
    calls = []
    monkeypatch.setattr(cli, "_load", lambda _: project)
    monkeypatch.setattr(cli, "_project_containers", lambda _: _containers())
    monkeypatch.setattr(cli.engine, "network_names", list)

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cli.engine, "run", fake_run)
    ns = argparse.Namespace(timeout=10, dry_run=False, volumes=False)

    assert cli.cmd_down(ns) == 0
    assert calls == [
        ["stop", "-t", "3", "demo-api-1"],
        ["remove", "-f", "demo-api-1"],
        ["stop", "-t", "5", "demo-db-1"],
        ["remove", "-f", "demo-db-1"],
    ]


def test_restart_stops_reverse_and_starts_forward(monkeypatch):
    project = _project()
    calls = []
    monkeypatch.setattr(cli, "_load", lambda _: project)
    monkeypatch.setattr(cli, "_project_containers", lambda _: _containers())

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cli.engine, "run", fake_run)
    monkeypatch.setattr(cli.engine, "run_retried", fake_run)
    ns = argparse.Namespace(services=[], timeout=10, dry_run=False)

    assert cli._lifecycle(ns, "restart") == 0
    assert calls == [
        ["stop", "-t", "3", "demo-api-1"],
        ["stop", "-t", "5", "demo-db-1"],
        ["start", "demo-db-1"],
        ["start", "demo-api-1"],
    ]


def test_container_order_preserves_replica_direction():
    project = _project()
    containers = _containers() + [
        {
            "name": "demo-api-2",
            "service": "api",
            "index": 2,
            "running": True,
        }
    ]

    forward = cli._ordered_containers(project, containers)
    reverse = cli._ordered_containers(project, containers, reverse=True)

    assert [entry["name"] for entry in forward] == [
        "demo-db-1", "demo-api-1", "demo-api-2"
    ]
    assert [entry["name"] for entry in reverse] == [
        "demo-api-2", "demo-api-1", "demo-db-1"
    ]
