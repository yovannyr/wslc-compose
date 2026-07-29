import subprocess

from wslc_compose import cli
from wslc_compose.model import Dependency, Project, Service


def _project():
    project = Project(name="demo", directory=".")
    worker = Service(name="worker", image="worker:1")
    test = Service(
        name="test",
        image="test:1",
        depends_on=["worker"],
        dependencies={"worker": Dependency()},
    )
    project.services = {"worker": worker, "test": test}
    return project


def _entry(service, running, exit_code=None):
    return {
        "name": f"demo-{service}-1",
        "service": service,
        "index": 1,
        "running": running,
        "exit_code": exit_code,
    }


def test_abort_on_failure_stops_remaining_services(monkeypatch):
    project = _project()
    snapshots = iter(
        [
            [_entry("worker", True), _entry("test", True)],
            [_entry("worker", True), _entry("test", False, 3)],
        ]
    )
    calls = []
    monkeypatch.setattr(cli, "_project_containers", lambda _: next(snapshots))
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        cli.engine,
        "run",
        lambda args, **kwargs: calls.append(args)
        or subprocess.CompletedProcess(args, 0, "", ""),
    )

    result = cli._monitor_job_exits(
        project,
        ["worker", "test"],
        abort_on_exit=False,
        abort_on_failure=True,
        exit_code_from=None,
        dry_run=False,
    )

    assert result == 3
    assert calls == [["stop", "-t", "10", "demo-worker-1"]]


def test_exit_code_from_returns_target_code_and_stops_others(monkeypatch):
    project = _project()
    calls = []
    monkeypatch.setattr(
        cli,
        "_project_containers",
        lambda _: [_entry("worker", True), _entry("test", False, 7)],
    )
    monkeypatch.setattr(
        cli.engine,
        "run",
        lambda args, **kwargs: calls.append(args)
        or subprocess.CompletedProcess(args, 0, "", ""),
    )

    result = cli._monitor_job_exits(
        project,
        ["worker", "test"],
        abort_on_exit=True,
        abort_on_failure=False,
        exit_code_from="test",
        dry_run=False,
    )

    assert result == 7
    assert calls == [["stop", "-t", "10", "demo-worker-1"]]


def test_monitor_returns_highest_code_after_all_services_exit(monkeypatch):
    project = _project()
    monkeypatch.setattr(
        cli,
        "_project_containers",
        lambda _: [_entry("worker", False, 2), _entry("test", False, 5)],
    )

    assert cli._monitor_job_exits(
        project,
        ["worker", "test"],
        abort_on_exit=False,
        abort_on_failure=False,
        exit_code_from=None,
        dry_run=False,
    ) == 5


def test_job_exit_options_are_parsed():
    ns = cli.build_parser().parse_args(
        ["up", "--abort-on-container-failure", "--exit-code-from", "test"]
    )
    assert ns.abort_on_container_failure
    assert ns.exit_code_from == "test"
