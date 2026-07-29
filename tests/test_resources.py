import subprocess

from wslc_compose import engine
from wslc_compose.loader import load_project


def test_network_and_volume_metadata_are_loaded(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: app
    networks: [front]
    volumes: [data:/data]
networks:
  front:
    driver: bridge
    driver_opts:
      com.example.mtu: "1400"
    labels:
      tier: frontend
volumes:
  data:
    driver: vhd
    driver_opts:
      size: 10GB
    labels:
      backup: daily
"""
    )

    project = load_project(str(tmp_path / "compose.yaml"))

    network = project.networks["front"]
    assert network.driver == "bridge"
    assert network.driver_opts == {"com.example.mtu": "1400"}
    assert network.labels == {"tier": "frontend"}
    volume = project.volumes["data"]
    assert volume.driver == "vhd"
    assert volume.driver_opts == {"size": "10GB"}
    assert volume.labels == {"backup": "daily"}


def test_ensure_network_forwards_driver_options_and_labels(monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "network_names", list)
    monkeypatch.setattr(
        engine,
        "run",
        lambda args, **kwargs: calls.append(args)
        or subprocess.CompletedProcess(args, 0, "", ""),
    )

    assert engine.ensure_network(
        "demo_front",
        driver="bridge",
        driver_opts={"z": "last", "a": "first"},
        labels={"tier": "frontend"},
    )

    assert calls == [[
        "network", "create", "--driver", "bridge",
        "--opt", "a=first", "--opt", "z=last",
        "--label", "tier=frontend", "demo_front",
    ]]


def test_ensure_volume_forwards_driver_options_and_labels(monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "volume_names", list)
    monkeypatch.setattr(
        engine,
        "run",
        lambda args, **kwargs: calls.append(args)
        or subprocess.CompletedProcess(args, 0, "", ""),
    )

    assert engine.ensure_volume(
        "demo_data",
        driver="vhd",
        driver_opts={"size": "10GB"},
        labels={"backup": "daily"},
    )

    assert calls == [[
        "volume", "create", "--driver", "vhd",
        "--opt", "size=10GB", "--label", "backup=daily", "demo_data",
    ]]


def test_existing_resources_are_not_recreated_for_metadata(monkeypatch):
    monkeypatch.setattr(engine, "network_names", lambda: ["demo_front"])
    monkeypatch.setattr(engine, "volume_names", lambda: ["demo_data"])

    assert not engine.ensure_network("demo_front", driver="bridge")
    assert not engine.ensure_volume("demo_data", driver="vhd")
