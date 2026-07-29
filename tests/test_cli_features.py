import argparse
import subprocess

from wslc_compose import cli
from wslc_compose.engine import WslcError
from wslc_compose.model import BuildConfig, Dependency, Project, Service


def _entry(service, running=True):
    return {
        "name": f"demo-{service}-1",
        "service": service,
        "index": 1,
        "running": running,
    }


def test_stats_forwards_format_for_running_selected_containers(monkeypatch):
    project = Project(name="demo", directory=".")
    project.services = {
        "api": Service(name="api", image="api"),
        "job": Service(name="job", image="job"),
    }
    calls = []
    monkeypatch.setattr(cli, "_load", lambda _: project)
    monkeypatch.setattr(
        cli,
        "_project_containers",
        lambda _: [_entry("api"), _entry("job", running=False)],
    )
    monkeypatch.setattr(
        cli.engine,
        "run",
        lambda args, **kwargs: calls.append(args)
        or subprocess.CompletedProcess(args, 0, "", ""),
    )

    ns = argparse.Namespace(
        services=[], all=False, format="json", no_trunc=True, dry_run=False
    )
    assert cli.cmd_stats(ns) == 0
    assert calls == [["stats", "--format", "json", "--no-trunc", "demo-api-1"]]


def test_config_query_modes(monkeypatch, capsys):
    project = Project(name="demo", directory=".")
    project.services = {
        "api": Service(name="api", image="registry/api:1", profiles=["web"]),
        "job": Service(name="job", build=BuildConfig(context="."), profiles=["ci"]),
    }
    monkeypatch.setattr(cli, "_load", lambda _: project)

    base = {"capabilities": False, "services": False, "images": False, "profiles": False, "quiet": False}
    for option, expected in (
        ("services", "api\njob"),
        ("images", "demo-job\nregistry/api:1"),
        ("profiles", "ci\nweb"),
    ):
        values = dict(base)
        values[option] = True
        assert cli.cmd_config(argparse.Namespace(**values)) == 0
        assert capsys.readouterr().out.strip() == expected

    values = dict(base)
    values["quiet"] = True
    assert cli.cmd_config(argparse.Namespace(**values)) == 0
    assert capsys.readouterr().out == ""


def test_pull_can_ignore_failures_and_continue(monkeypatch):
    project = Project(name="demo", directory=".")
    project.services = {
        "bad": Service(name="bad", image="bad:1"),
        "good": Service(name="good", image="good:1"),
    }
    calls = []
    monkeypatch.setattr(cli, "_load", lambda _: project)

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[-1] == "bad:1":
            raise WslcError("not found")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cli.engine, "run", fake_run)
    ns = argparse.Namespace(
        services=[], profile=[], dry_run=False, ignore_pull_failures=True
    )

    assert cli.cmd_pull(ns) == 0
    assert calls == [["pull", "bad:1"], ["pull", "good:1"]]


def test_build_with_dependencies_controls_selection(monkeypatch):
    project = Project(name="demo", directory=".")
    base = Service(name="base", build=BuildConfig(context="base"))
    app = Service(
        name="app",
        build=BuildConfig(context="app"),
        depends_on=["base"],
        dependencies={"base": Dependency()},
    )
    project.services = {"base": base, "app": app}
    calls = []
    monkeypatch.setattr(cli, "_load", lambda _: project)
    monkeypatch.setattr(
        cli.engine,
        "run",
        lambda args, **kwargs: calls.append(args)
        or subprocess.CompletedProcess(args, 0, "", ""),
    )

    ns = argparse.Namespace(
        services=["app"], profile=[], no_cache=False, dry_run=False,
        with_dependencies=False,
    )
    assert cli.cmd_build(ns) == 0
    assert [args[2] for args in calls] == ["demo-app"]

    calls.clear()
    ns.with_dependencies = True
    assert cli.cmd_build(ns) == 0
    assert [args[2] for args in calls] == ["demo-base", "demo-app"]


def test_down_rmi_local_removes_only_built_images(monkeypatch):
    project = Project(name="demo", directory=".")
    project.services = {
        "local": Service(name="local", build=BuildConfig(context=".")),
        "remote": Service(name="remote", image="remote:1"),
    }
    calls = []
    monkeypatch.setattr(cli, "_load", lambda _: project)
    monkeypatch.setattr(cli, "_project_containers", lambda _: [])
    monkeypatch.setattr(cli.engine, "network_names", list)
    monkeypatch.setattr(
        cli.engine,
        "run",
        lambda args, **kwargs: calls.append(args)
        or subprocess.CompletedProcess(args, 0, "", ""),
    )

    ns = argparse.Namespace(
        timeout=10, dry_run=False, volumes=False, remove_orphans=False, rmi="local"
    )
    assert cli.cmd_down(ns) == 0
    assert calls == [["rmi", "demo-local"]]


def test_new_cli_feature_options_are_parsed():
    parser = cli.build_parser()
    assert parser.parse_args(["stats", "--format", "json"]).func is cli.cmd_stats
    assert parser.parse_args(["down", "--rmi", "all"]).rmi == "all"
    assert parser.parse_args(["pull", "--ignore-pull-failures"]).ignore_pull_failures
    assert parser.parse_args(["build", "--with-dependencies"]).with_dependencies
    assert parser.parse_args(["config", "--services"]).services
