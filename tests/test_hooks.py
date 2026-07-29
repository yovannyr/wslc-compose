import argparse
import subprocess

import pytest

from wslc_compose import cli
from wslc_compose.loader import ComposeError, load_project
from wslc_compose.model import LifecycleHook, Project, Service


def test_lifecycle_hooks_are_loaded(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: app
    post_start:
      - command: [app, migrate]
        user: worker
        working_dir: /srv
        environment:
          MODE: ready
    pre_stop:
      - command: /bin/sh -c "app drain"
"""
    )

    service = load_project(str(tmp_path / "compose.yaml")).services["app"]

    assert service.post_start == [
        LifecycleHook(
            command=["app", "migrate"],
            user="worker",
            working_dir="/srv",
            environment={"MODE": "ready"},
        )
    ]
    assert service.pre_stop[0].command == ["/bin/sh", "-c", "app drain"]


def test_privileged_lifecycle_hook_is_rejected(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: app
    post_start:
      - command: whoami
        privileged: true
"""
    )

    with pytest.raises(ComposeError, match="privileged is not supported"):
        load_project(str(tmp_path / "compose.yaml"))


def test_lifecycle_hook_builds_exec_arguments(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cli.engine, "run", fake_run)
    hook = LifecycleHook(
        command=["app", "migrate"],
        user="worker",
        working_dir="/srv",
        environment={"MODE": "ready", "INHERIT": None},
    )

    cli._run_lifecycle_hooks("demo-app-1", [hook], "post_start", False)

    assert calls == [[
        "exec", "-u", "worker", "-w", "/srv",
        "-e", "MODE=ready", "-e", "INHERIT",
        "demo-app-1", "app", "migrate",
    ]]


def test_stop_runs_pre_stop_before_container_stop(monkeypatch):
    project = Project(name="demo", directory=".")
    project.services["app"] = Service(
        name="app",
        image="app",
        pre_stop=[LifecycleHook(command=["app", "drain"])],
    )
    container = {
        "name": "demo-app-1",
        "service": "app",
        "index": 1,
        "running": True,
    }
    calls = []
    monkeypatch.setattr(cli, "_load", lambda _: project)
    monkeypatch.setattr(cli, "_project_containers", lambda _: [container])

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cli.engine, "run", fake_run)
    ns = argparse.Namespace(services=[], timeout=10, dry_run=False)

    assert cli._lifecycle(ns, "stop") == 0
    assert calls == [
        ["exec", "demo-app-1", "app", "drain"],
        ["stop", "-t", "10", "demo-app-1"],
    ]


def test_failed_hook_aborts_lifecycle(monkeypatch):
    monkeypatch.setattr(
        cli.engine,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 7, "", ""),
    )

    with pytest.raises(ComposeError, match="exited with code 7"):
        cli._run_lifecycle_hooks(
            "demo-app-1",
            [LifecycleHook(command=["false"])],
            "post_start",
            False,
        )
