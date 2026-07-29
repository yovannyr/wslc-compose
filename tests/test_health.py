import subprocess
import time

import pytest

from wslc_compose import cli, engine
from wslc_compose.loader import ComposeError, load_project, parse_duration
from wslc_compose.model import Healthcheck, Project, Service


def test_healthcheck_and_dependency_conditions_are_loaded(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  db:
    image: db
    healthcheck:
      test: [CMD, pg_isready]
      interval: 1m30s
      timeout: 2s
      retries: 5
      start_period: 10s
  migrate:
    image: migrate
    depends_on:
      db:
        condition: service_healthy
  app:
    image: app
    depends_on:
      migrate:
        condition: service_completed_successfully
        required: false
"""
    )

    project = load_project(str(tmp_path / "compose.yaml"), strict_unsupported=True)

    healthcheck = project.services["db"].healthcheck
    assert healthcheck is not None
    assert healthcheck.test == ["CMD", "pg_isready"]
    assert healthcheck.interval == 90.0
    assert healthcheck.timeout == 2.0
    assert healthcheck.retries == 5
    assert healthcheck.start_period == 10.0
    assert project.services["migrate"].dependencies["db"].condition == "service_healthy"
    dependency = project.services["app"].dependencies["migrate"]
    assert dependency.condition == "service_completed_successfully"
    assert not dependency.required


def test_service_healthy_requires_a_healthcheck(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  db:
    image: db
  app:
    image: app
    depends_on:
      db:
        condition: service_healthy
"""
    )

    with pytest.raises(ComposeError, match="has no healthcheck"):
        load_project(str(tmp_path / "compose.yaml"))


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("500ms", 0.5), ("1m30s", 90.0), ("2h5m", 7500.0)],
)
def test_parse_duration(value, seconds):
    assert parse_duration(value, "duration") == seconds


def test_health_wait_retries_until_success(monkeypatch):
    service = Service(
        name="db",
        image="db",
        healthcheck=Healthcheck(
            test=["CMD-SHELL", "pg_isready"], interval=0, timeout=1, retries=3
        ),
    )
    results = iter([1, 0])
    calls = []

    def fake_run(args, capture, check, timeout):
        calls.append(args)
        return subprocess.CompletedProcess(args, next(results), "", "")

    monkeypatch.setattr(cli.engine, "run", fake_run)
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)

    cli._wait_for_container_health("demo-db-1", service, time.monotonic() + 5)

    assert calls == [
        ["exec", "demo-db-1", "/bin/sh", "-c", "pg_isready"],
        ["exec", "demo-db-1", "/bin/sh", "-c", "pg_isready"],
    ]


def test_health_wait_fails_after_configured_retries(monkeypatch):
    service = Service(
        name="db",
        image="db",
        healthcheck=Healthcheck(test=["CMD", "false"], interval=0, timeout=1, retries=2),
    )

    monkeypatch.setattr(
        cli.engine,
        "run",
        lambda args, capture, check, timeout: subprocess.CompletedProcess(args, 1, "", ""),
    )
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)

    with pytest.raises(ComposeError, match="unhealthy after 2 attempts"):
        cli._wait_for_container_health("demo-db-1", service, time.monotonic() + 5)


def test_completed_dependency_checks_exit_code(monkeypatch):
    project = Project(name="demo", directory=".")
    service = Service(name="migrate", image="migrate")
    project.services[service.name] = service
    states = iter(
        [
            {"State": {"Running": True}},
            {"State": {"Running": False, "ExitCode": 0}},
        ]
    )
    monkeypatch.setattr(engine, "inspect", lambda _: next(states))
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)

    cli._wait_for_service_completion(project, service, time.monotonic() + 5)


def test_completed_dependency_rejects_nonzero_exit(monkeypatch):
    project = Project(name="demo", directory=".")
    service = Service(name="migrate", image="migrate")
    project.services[service.name] = service
    monkeypatch.setattr(
        engine, "inspect", lambda _: {"State": {"Running": False, "ExitCode": 7}}
    )

    with pytest.raises(ComposeError, match="exited with code 7"):
        cli._wait_for_service_completion(project, service, time.monotonic() + 5)
