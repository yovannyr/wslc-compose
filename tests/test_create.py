import argparse
import subprocess

import pytest

from wslc_compose import cli
from wslc_compose.loader import ComposeError
from wslc_compose.model import Dependency, LifecycleHook, Project, Service, VolumeMount


def _namespace(**overrides):
    values = {
        "services": [],
        "profile": [],
        "scale": [],
        "wait_timeout": 60,
        "dry_run": False,
        "build": False,
        "no_build": False,
        "pull": None,
        "force_recreate": False,
        "no_recreate": False,
        "always_recreate_deps": False,
        "renew_anon_volumes": False,
        "no_start": False,
        "timeout": 10,
        "wait": False,
        "detach": True,
        "remove_orphans": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _entry(name, service, config_hash, running=True):
    return {
        "name": name,
        "service": service,
        "index": 1,
        "hash": config_hash,
        "running": running,
    }


def _runtime(monkeypatch, project, containers, calls):
    monkeypatch.setattr(cli, "_load", lambda _: project)
    monkeypatch.setattr(cli, "_project_containers", lambda _: list(containers))
    monkeypatch.setattr(cli.engine, "image_exists", lambda _: True)
    monkeypatch.setattr(cli.engine, "network_names", list)
    monkeypatch.setattr(cli.engine, "volume_names", list)
    monkeypatch.setattr(cli.engine, "ensure_volume", lambda *args, **kwargs: False)

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cli.engine, "run", fake_run)
    monkeypatch.setattr(cli.engine, "run_retried", fake_run)


def test_create_uses_wslc_create_and_does_not_run_post_start(monkeypatch):
    project = Project(name="demo", directory=".")
    project.services["app"] = Service(
        name="app",
        image="app:1",
        post_start=[LifecycleHook(command=["app", "migrate"])],
    )
    calls = []
    _runtime(monkeypatch, project, [], calls)

    assert cli.cmd_create(_namespace()) == 0

    assert any(args[0] == "create" for args in calls)
    assert not any(args[0] == "exec" for args in calls)


def test_no_recreate_keeps_stale_running_container(monkeypatch):
    project = Project(name="demo", directory=".")
    service = Service(name="app", image="app:1")
    project.services["app"] = service
    calls = []
    _runtime(
        monkeypatch,
        project,
        [_entry("demo-app-1", "app", "stale")],
        calls,
    )

    assert cli.cmd_up(_namespace(no_recreate=True)) == 0

    assert calls == []


def test_always_recreate_deps_recreates_only_selected_dependencies(monkeypatch):
    project = Project(name="demo", directory=".")
    db = Service(name="db", image="db:1")
    app = Service(
        name="app",
        image="app:1",
        depends_on=["db"],
        dependencies={"db": Dependency()},
    )
    project.services = {"db": db, "app": app}
    calls = []
    containers = [
        _entry("demo-db-1", "db", db.config_hash()),
        _entry("demo-app-1", "app", app.config_hash()),
    ]
    _runtime(monkeypatch, project, containers, calls)

    assert cli.cmd_up(
        _namespace(services=["app"], always_recreate_deps=True)
    ) == 0

    assert ["remove", "-f", "demo-db-1"] in calls
    assert ["remove", "-f", "demo-app-1"] not in calls


def test_renew_anonymous_volumes_recreates_volume(monkeypatch):
    project = Project(name="demo", directory=".")
    service = Service(
        name="app",
        image="app:1",
        volumes=[
            VolumeMount(
                type="volume",
                source="demo_app_anonymous_deadbeef",
                target="/data",
                anonymous=True,
            )
        ],
    )
    project.services["app"] = service
    calls = []
    ensured = []
    _runtime(
        monkeypatch,
        project,
        [_entry("demo-app-1", "app", service.config_hash())],
        calls,
    )
    monkeypatch.setattr(
        cli.engine,
        "ensure_volume",
        lambda name, **kwargs: ensured.append(name) or False,
    )

    assert cli.cmd_up(_namespace(renew_anon_volumes=True)) == 0

    volume = "demo_app_anonymous_deadbeef-1"
    assert ["volume", "remove", volume] in calls
    assert ensured.count(volume) == 2


def test_recreate_options_conflict(monkeypatch):
    project = Project(name="demo", directory=".")
    project.services["app"] = Service(name="app", image="app:1")
    monkeypatch.setattr(cli, "_load", lambda _: project)

    with pytest.raises(ComposeError, match="cannot be used together"):
        cli.cmd_up(_namespace(force_recreate=True, no_recreate=True))


def test_create_and_recreate_options_are_parsed():
    parser = cli.build_parser()
    assert parser.parse_args(["create", "app"]).func is cli.cmd_create
    ns = parser.parse_args(
        ["up", "--no-start", "--no-recreate", "--always-recreate-deps"]
    )
    assert ns.no_start and ns.no_recreate and ns.always_recreate_deps
