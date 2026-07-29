import argparse

import pytest

from wslc_compose import cli
from wslc_compose.loader import ComposeError, load_project
from wslc_compose.model import Project, Service, WatchRule


def test_rebuild_and_restart_watch_rules_are_loaded(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: app
    develop:
      watch:
        - action: rebuild
          path: ./src
          ignore: [bin/**, "*.tmp"]
        - action: restart
          path: ./config.json
"""
    )

    rules = load_project(str(tmp_path / "compose.yaml")).services["app"].watch

    assert rules == [
        WatchRule(action="rebuild", path=str(source), ignore=["bin/**", "*.tmp"]),
        WatchRule(action="restart", path=str(tmp_path / "config.json")),
    ]


def test_sync_watch_is_rejected_with_runtime_reason(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: app
    develop:
      watch:
        - action: sync
          path: ./src
          target: /app
"""
    )

    with pytest.raises(ComposeError, match="no container file-copy operation"):
        load_project(str(tmp_path / "compose.yaml"))


def test_watch_snapshot_detects_changes_and_honors_ignore(tmp_path):
    (tmp_path / "keep.txt").write_text("one")
    (tmp_path / "skip.tmp").write_text("ignored")

    first = cli._watch_snapshot(str(tmp_path), ["*.tmp"])
    (tmp_path / "keep.txt").write_text("two-two")
    second = cli._watch_snapshot(str(tmp_path), ["*.tmp"])

    assert len(first) == 1
    assert first != second
    assert all(not path.endswith("skip.tmp") for path in second)


def test_watch_rebuilds_service_after_change(monkeypatch, tmp_path):
    project = Project(name="demo", directory=str(tmp_path))
    project.services["app"] = Service(
        name="app",
        image="app",
        watch=[WatchRule(action="rebuild", path=str(tmp_path))],
    )
    snapshots = iter([{"file": (1, 1)}, {"file": (2, 2)}])
    up_calls = []
    sleeps = 0
    monkeypatch.setattr(cli, "_load", lambda _: project)
    monkeypatch.setattr(cli, "_watch_snapshot", lambda *args: next(snapshots))
    monkeypatch.setattr(cli, "cmd_up", lambda ns: up_calls.append(ns) or 0)

    def fake_sleep(_):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", fake_sleep)
    ns = argparse.Namespace(
        services=[],
        profile=[],
        no_up=True,
        interval=0.01,
        dry_run=False,
    )

    with pytest.raises(KeyboardInterrupt):
        cli.cmd_watch(ns)

    assert len(up_calls) == 1
    assert up_calls[0].services == ["app"]
    assert up_calls[0].build
    assert up_calls[0].force_recreate


def test_watch_parser_options():
    ns = cli.build_parser().parse_args(
        ["watch", "--no-up", "--interval", "1.5", "app"]
    )
    assert ns.func is cli.cmd_watch
    assert ns.no_up
    assert ns.interval == 1.5
    assert ns.services == ["app"]
