import argparse
import subprocess

import pytest

from wslc_compose import cli
from wslc_compose.engine import WslcError
from wslc_compose.model import Project, Service


def _namespace(remove_orphans=False):
    return argparse.Namespace(
        services=[],
        profile=[],
        scale=[],
        wait_timeout=60,
        dry_run=False,
        build=False,
        no_build=False,
        pull=None,
        force_recreate=False,
        timeout=10,
        wait=False,
        detach=True,
        remove_orphans=remove_orphans,
    )


def _entry(name, service, running=False):
    return {
        "name": name,
        "service": service,
        "index": 1,
        "hash": "",
        "running": running,
    }


def _project(*names):
    project = Project(name="demo", directory=".")
    project.services = {
        name: Service(name=name, image=f"{name}:1") for name in names
    }
    return project


def _mock_runtime(monkeypatch, project, containers, calls, fail_service=None):
    monkeypatch.setattr(cli, "_load", lambda _: project)
    monkeypatch.setattr(cli, "_project_containers", lambda _: list(containers))
    monkeypatch.setattr(cli.engine, "image_exists", lambda _: True)

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[0] == "run" and fail_service and fail_service in args:
            raise WslcError("create failed")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cli.engine, "run", fake_run)


def test_up_warns_about_orphans_without_removing_them(monkeypatch, capsys):
    project = _project("app")
    calls = []
    _mock_runtime(
        monkeypatch, project, [_entry("demo-old-1", "old")], calls
    )

    assert cli.cmd_up(_namespace()) == 0

    assert ["remove", "-f", "demo-old-1"] not in calls
    assert "found orphan containers: demo-old-1" in capsys.readouterr().err


def test_up_remove_orphans_deletes_stale_project_containers(monkeypatch):
    project = _project("app")
    calls = []
    _mock_runtime(
        monkeypatch, project, [_entry("demo-old-1", "old")], calls
    )

    assert cli.cmd_up(_namespace(remove_orphans=True)) == 0

    assert ["remove", "-f", "demo-old-1"] in calls


def test_down_preserves_orphans_by_default(monkeypatch, capsys):
    project = _project("app")
    calls = []
    monkeypatch.setattr(cli, "_load", lambda _: project)
    monkeypatch.setattr(
        cli,
        "_project_containers",
        lambda _: [_entry("demo-old-1", "old", running=True)],
    )
    monkeypatch.setattr(cli.engine, "network_names", list)
    monkeypatch.setattr(
        cli.engine,
        "run",
        lambda args, **kwargs: calls.append(args)
        or subprocess.CompletedProcess(args, 0, "", ""),
    )

    ns = argparse.Namespace(
        timeout=10, dry_run=False, volumes=False, remove_orphans=False
    )
    assert cli.cmd_down(ns) == 0

    assert calls == []
    assert "use --remove-orphans" in capsys.readouterr().err


def test_failed_up_rolls_back_only_newly_created_containers(monkeypatch):
    project = _project("first", "second")
    calls = []
    _mock_runtime(monkeypatch, project, [], calls, fail_service="second:1")

    with pytest.raises(WslcError, match="create failed"):
        cli.cmd_up(_namespace())

    assert ["remove", "-f", "demo-first-1"] in calls
    assert ["remove", "-f", "demo-second-1"] not in calls


def test_remove_orphans_options_are_parsed():
    parser = cli.build_parser()
    assert parser.parse_args(["up", "--remove-orphans"]).remove_orphans
    assert parser.parse_args(["down", "--remove-orphans"]).remove_orphans
