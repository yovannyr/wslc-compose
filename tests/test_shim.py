from wslc_compose import shim
from wslc_compose.shim import split_argv


def test_main_forwards_native_arguments_and_exit_code(monkeypatch):
    calls = []
    monkeypatch.setattr(shim.engine, "find_wslc", lambda: "native/wslc.exe")
    monkeypatch.setattr(shim.subprocess, "call", lambda args: calls.append(args) or 7)
    assert shim.main(["exec", "container", "echo", "hello world"]) == 7
    assert calls == [["native/wslc.exe", "exec", "container", "echo", "hello world"]]


def test_main_dispatches_compose_arguments_and_exit_code(monkeypatch):
    calls = []
    monkeypatch.setattr(shim.cli, "main", lambda args: calls.append(args) or 3)
    assert shim.main(["compose", "-f", "path with spaces.yaml", "ps"]) == 3
    assert calls == [["-f", "path with spaces.yaml", "ps"]]


def test_compose_dispatch():
    compose, passthrough = split_argv(["compose", "up", "-d"])
    assert compose == ["up", "-d"]
    assert passthrough == []


def test_bare_compose():
    compose, _passthrough = split_argv(["compose"])
    assert compose == []


def test_passthrough():
    compose, passthrough = split_argv(["list", "-a"])
    assert compose is None
    assert passthrough == ["list", "-a"]


def test_empty():
    compose, passthrough = split_argv([])
    assert compose is None
    assert passthrough == []
