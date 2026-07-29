import argparse
import subprocess

from wslc_compose import cli
from wslc_compose.model import Dependency, Project, Service


def _project():
    project = Project(name="demo", directory=".")
    db = Service(name="db", image="db:1")
    app = Service(
        name="app",
        image="app:1",
        depends_on=["db"],
        dependencies={"db": Dependency()},
    )
    project.services = {"db": db, "app": app}
    return project


def _entry(name, service, running=True, exit_code=None, ports=None):
    return {
        "id": name,
        "name": name,
        "service": service,
        "index": 1,
        "running": running,
        "exit_code": exit_code,
        "ports": ports or [],
    }


def test_kill_uses_reverse_dependency_order(monkeypatch):
    project = _project()
    calls = []
    monkeypatch.setattr(cli, "_load", lambda _: project)
    monkeypatch.setattr(
        cli,
        "_project_containers",
        lambda _: [_entry("demo-db-1", "db"), _entry("demo-app-1", "app")],
    )
    monkeypatch.setattr(
        cli.engine,
        "run",
        lambda args, **kwargs: calls.append(args)
        or subprocess.CompletedProcess(args, 0, "", ""),
    )

    ns = argparse.Namespace(services=[], signal="TERM", dry_run=False)
    assert cli.cmd_kill(ns) == 0
    assert calls == [
        ["kill", "-s", "TERM", "demo-app-1"],
        ["kill", "-s", "TERM", "demo-db-1"],
    ]


def test_wait_returns_highest_exit_code(monkeypatch):
    project = _project()
    snapshots = iter(
        [
            [_entry("demo-db-1", "db"), _entry("demo-app-1", "app")],
            [
                _entry("demo-db-1", "db", False, 0),
                _entry("demo-app-1", "app", False, 4),
            ],
        ]
    )
    monkeypatch.setattr(cli, "_load", lambda _: project)
    monkeypatch.setattr(cli, "_project_containers", lambda _: next(snapshots))
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)

    ns = argparse.Namespace(services=[], timeout=5)
    assert cli.cmd_wait(ns) == 4


def test_port_prints_requested_binding(monkeypatch, capsys):
    project = _project()
    ports = [
        {
            "Protocol": 6,
            "BindingAddress": "127.0.0.1",
            "HostPort": 8080,
            "ContainerPort": 80,
        }
    ]
    monkeypatch.setattr(cli, "_load", lambda _: project)
    monkeypatch.setattr(
        cli,
        "_project_containers",
        lambda _: [_entry("demo-app-1", "app", ports=ports)],
    )

    ns = argparse.Namespace(service="app", private_port="80/tcp", index=1)
    assert cli.cmd_port(ns) == 0
    assert capsys.readouterr().out.strip() == "127.0.0.1:8080"


def test_push_skips_build_only_services(monkeypatch):
    project = _project()
    project.services["local"] = Service(name="local", build=object())
    calls = []
    monkeypatch.setattr(cli, "_load", lambda _: project)
    monkeypatch.setattr(
        cli.engine,
        "run",
        lambda args, **kwargs: calls.append(args)
        or subprocess.CompletedProcess(args, 0, "", ""),
    )

    ns = argparse.Namespace(services=[], profile=[], dry_run=False)
    assert cli.cmd_push(ns) == 0
    assert calls == [["push", "db:1"], ["push", "app:1"]]


class _LogProcess:
    def __init__(self):
        self.stdout = []

    def send_signal(self, signal):
        pass


def test_logs_forward_since_and_until_to_wslc(monkeypatch):
    project = _project()
    calls = []
    monkeypatch.setattr(
        cli,
        "_project_containers",
        lambda _: [_entry("demo-app-1", "app")],
    )
    monkeypatch.setattr(
        cli.engine,
        "popen",
        lambda args, **kwargs: calls.append(args) or _LogProcess(),
    )

    assert cli._follow_logs(
        project,
        ["app"],
        follow=True,
        tail=20,
        timestamps=True,
        since="10m",
        until="1m",
    ) == 0
    assert calls == [
        [
            "logs", "-f", "-n", "20", "-t", "--since", "10m",
            "--until", "1m", "demo-app-1"
        ]
    ]


def test_new_command_parsers_are_available():
    parser = cli.build_parser()
    assert parser.parse_args(["kill", "-s", "TERM", "app"]).signal == "TERM"
    assert parser.parse_args(["wait", "--timeout", "5", "app"]).timeout == 5
    assert parser.parse_args(["port", "app", "80/tcp"]).private_port == "80/tcp"
