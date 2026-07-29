import argparse
import subprocess

from wslc_compose import cli
from wslc_compose.flags import run_args
from wslc_compose.model import Dependency, PortMapping, Project, Service


def test_one_off_run_args_support_overrides():
    project = Project(name="demo", directory=".")
    service = Service(
        name="app",
        image="app:1",
        command=["default"],
        entrypoint=["/default"],
        ports=[PortMapping(target=80, published="8080")],
        tty=True,
    )

    args = run_args(
        project,
        service,
        detach=False,
        container_name="one-off",
        command_override=["echo", "hello"],
        entrypoint_override=["/bin/sh", "-c"],
        remove=True,
        include_ports=False,
    )

    assert args[:4] == ["run", "--rm", "--name", "one-off"]
    assert "8080:80" not in args
    assert args[args.index("--entrypoint") + 1] == "/bin/sh"
    image_index = args.index("app:1")
    assert args[image_index + 1 :] == ["-c", "echo", "hello"]


def test_run_command_creates_one_off_container(tmp_path, monkeypatch):
    compose = tmp_path / "compose.yaml"
    compose.write_text(
        """
name: demo
services:
  app:
    image: app:1
    environment:
      VALUE: original
    ports: ["8080:80"]
"""
    )
    calls = []
    monkeypatch.setattr(cli, "_ensure_service_resources", lambda *args: None)
    monkeypatch.setattr(cli, "_prepare_service_image", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli.engine, "to_host_path", lambda path: path)

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cli.engine, "run", fake_run)

    result = cli.main(
        [
            "-f",
            str(compose),
            "run",
            "--rm",
            "--name",
            "demo-task",
            "--service-ports",
            "-e",
            "VALUE=override",
            "--entrypoint",
            "/bin/sh -c",
            "app",
            "echo",
            "hello",
        ]
    )

    assert result == 0
    args = calls[0]
    assert args[:4] == ["run", "--rm", "--name", "demo-task"]
    assert "VALUE=override" in args
    assert "8080:80" in args
    assert args[args.index("--entrypoint") + 1] == "/bin/sh"
    image_index = args.index("app:1")
    assert args[image_index + 1 :] == ["-c", "echo", "hello"]


def test_run_starts_dependencies_unless_disabled(monkeypatch):
    project = Project(name="demo", directory=".")
    dependency = Service(name="db", image="db")
    service = Service(
        name="app",
        image="app",
        depends_on=["db"],
        dependencies={"db": Dependency()},
    )
    project.services = {"db": dependency, "app": service}
    up_calls = []
    monkeypatch.setattr(cli, "_load", lambda _: project)
    monkeypatch.setattr(cli, "cmd_up", lambda ns: up_calls.append(ns.services) or 0)
    monkeypatch.setattr(cli, "_ensure_service_resources", lambda *args: None)
    monkeypatch.setattr(cli, "_prepare_service_image", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli.engine,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
    )
    ns = argparse.Namespace(
        service="app", no_deps=False, build=False, no_build=False, pull=None,
        dry_run=False, env=[], command=["true"], entrypoint=None, name="task",
        detach=False, rm=True, service_ports=False, no_tty=True, wait_timeout=10,
    )

    assert cli.cmd_run(ns) == 0
    assert up_calls == [["db"]]
