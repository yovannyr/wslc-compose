"""wslc-compose command line interface."""

from __future__ import annotations

import argparse
import dataclasses
import math
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from typing import Dict, List, Optional

import yaml

from wslc_compose import (
    LABEL_CONFIG_HASH,
    LABEL_INDEX,
    LABEL_SERVICE,
    __version__,
    engine,
    flags,
)
from wslc_compose.engine import WslcError
from wslc_compose.loader import (
    UNSUPPORTED_CAPABILITIES,
    ComposeError,
    find_compose_file,
    load_project,
    parse_duration,
)
from wslc_compose.model import Project, Service

PROTOCOLS = {6: "tcp", 17: "udp"}


def _err(message: str) -> None:
    print(f"wslc-compose: {message}", file=sys.stderr)


def _info(message: str) -> None:
    print(message)


# --- project loading -------------------------------------------------------


def _locate_compose_files(explicit: Optional[List[str]]) -> List[str]:
    if explicit:
        for path in explicit:
            if not os.path.isfile(path):
                raise ComposeError(f"compose file not found: {path}")
        return explicit
    directory = os.getcwd()
    while True:
        found = find_compose_file(directory)
        if found:
            return [found]
        parent = os.path.dirname(directory)
        if parent == directory:
            raise ComposeError(
                "no compose file found (looked for compose.yaml / compose.yml / "
                "docker-compose.yml / docker-compose.yaml in this directory and parents)"
            )
        directory = parent


def _load(ns: argparse.Namespace) -> Project:
    compose_files = _locate_compose_files(ns.file)
    project = load_project(
        compose_files,
        project_name=ns.project_name,
        env_file=ns.env_file,
        strict_unsupported=not ns.ignore_unsupported,
    )
    for warning in project.warnings:
        _err(f"warning: {warning}")
    return project


def _select_services(
    project: Project, names: List[str], profiles: List[str]
) -> List[Service]:
    active = set(profiles or [])
    for name in names:
        if name not in project.services:
            raise ComposeError(f"no such service: {name}")
    services = project.sorted_services(names or None)
    result = []
    for svc in services:
        if svc.profiles and not (set(svc.profiles) & active) and svc.name not in names:
            continue
        result.append(svc)
    return result


# --- container queries ------------------------------------------------------


def _project_containers(project: Project) -> List[dict]:
    """list entries enriched with inspect data (labels, status)."""
    entries = engine.list_project_containers(project.name)
    enriched = []
    for entry in entries:
        data = engine.inspect(entry.get("Id") or entry.get("Name")) or {}
        labels = data.get("Labels") or {}
        state = data.get("State") or {}
        enriched.append(
            {
                "id": entry.get("Id"),
                "name": entry.get("Name") or data.get("Name"),
                "image": entry.get("Image") or data.get("Image"),
                "service": labels.get(LABEL_SERVICE, ""),
                "index": int(labels.get(LABEL_INDEX, "1") or 1),
                "hash": labels.get(LABEL_CONFIG_HASH, ""),
                "running": bool(state.get("Running")),
                "status": state.get("Status", "unknown"),
                "exit_code": state.get("ExitCode"),
                "ports": entry.get("Ports") or [],
            }
        )
    return enriched


def _format_ports(ports: List[dict]) -> str:
    parts = []
    for port in ports:
        proto = PROTOCOLS.get(port.get("Protocol"), str(port.get("Protocol", "")))
        addr = port.get("BindingAddress") or "0.0.0.0"
        parts.append(f"{addr}:{port.get('HostPort')}->{port.get('ContainerPort')}/{proto}")
    return ", ".join(parts)


def _print_table(rows: List[List[str]], headers: List[str]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    for row in rows:
        print(fmt.format(*row))


# --- commands ----------------------------------------------------------------


def _uses_latest_tag(image: Optional[str]) -> bool:
    if not image or "@" in image:
        return False
    last_component = image.rsplit("/", 1)[-1]
    return ":" not in last_component or last_component.endswith(":latest")


def _prepare_service_image(
    project: Project,
    service: Service,
    *,
    build: bool = False,
    no_build: bool = False,
    pull: Optional[str] = None,
    no_cache: bool = False,
    dry_run: bool = False,
) -> None:
    if build and no_build:
        raise ComposeError("--build and --no-build cannot be used together")
    policy = pull or service.pull_policy or "missing"
    image = flags.image_name(project, service)
    exists = engine.image_exists(image)

    should_pull = bool(service.image) and (
        policy == "always" or (policy == "missing" and exists and _uses_latest_tag(service.image))
    )
    if should_pull:
        _info(f"Pulling {service.name} ...")
        engine.run(["pull", service.image], dry_run=dry_run)
        exists = True

    should_build = bool(service.build) and (build or policy == "build")
    if should_build:
        if no_build:
            raise ComposeError(f"{service.name}: image build is disabled by --no-build")
        _info(f"Building {service.name} ...")
        engine.run(
            flags.build_args(
                project,
                service,
                no_cache=no_cache,
                path_mapper=engine.to_host_path,
            ),
            dry_run=dry_run,
        )
        return

    if exists:
        return
    if service.build and not no_build:
        _info(f"Building {service.name} ...")
        engine.run(
            flags.build_args(project, service, path_mapper=engine.to_host_path),
            dry_run=dry_run,
        )
    elif service.build and no_build:
        raise ComposeError(
            f"{service.name}: image {image!r} is missing and image build is disabled"
        )
    elif policy == "never":
        raise ComposeError(f"{service.name}: image {image!r} is missing and pull_policy is never")
    elif service.image:
        _info(f"Pulling {service.name} ...")
        engine.run(["pull", service.image], dry_run=dry_run)


def _wait_for_container_health(
    container_name: str,
    service: Service,
    deadline: float,
) -> None:
    healthcheck = service.healthcheck
    if healthcheck is None:
        return
    started_at = time.monotonic()
    failures = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ComposeError(f"timed out waiting for {container_name} to become healthy")

        if healthcheck.test[0] == "CMD-SHELL":
            if len(healthcheck.test) != 2:
                raise ComposeError(
                    f"{service.name}: CMD-SHELL healthcheck requires one command string"
                )
            command = ["/bin/sh", "-c", healthcheck.test[1]]
        else:
            command = healthcheck.test[1:]
        result = engine.run(
            ["exec", container_name] + command,
            capture=True,
            check=False,
            timeout=min(healthcheck.timeout, remaining),
        )
        if result.returncode == 0:
            return
        if time.monotonic() - started_at >= healthcheck.start_period:
            failures += 1
            if failures >= healthcheck.retries:
                raise ComposeError(
                    f"container {container_name} is unhealthy after {failures} attempts"
                )
        time.sleep(min(healthcheck.interval, max(0.0, deadline - time.monotonic())))


def _wait_for_service_health(
    project: Project, service: Service, deadline: float, replicas: Optional[int] = None
) -> None:
    for index in range(1, (replicas or service.replicas) + 1):
        _wait_for_container_health(project.container_name(service, index), service, deadline)


def _wait_for_service_completion(
    project: Project, service: Service, deadline: float, replicas: Optional[int] = None
) -> None:
    for index in range(1, (replicas or service.replicas) + 1):
        container_name = project.container_name(service, index)
        while True:
            if time.monotonic() >= deadline:
                raise ComposeError(f"timed out waiting for {container_name} to complete")
            data = engine.inspect(container_name)
            if data is None:
                raise ComposeError(f"cannot inspect dependency container {container_name}")
            state = data.get("State") or {}
            if not state.get("Running"):
                exit_code = state.get("ExitCode")
                if exit_code is None:
                    raise ComposeError(
                        f"wslc inspect did not report an exit code for {container_name}"
                    )
                if int(exit_code) != 0:
                    raise ComposeError(f"container {container_name} exited with code {exit_code}")
                break
            time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))


def _run_lifecycle_hooks(
    container_name: str, hooks: list, hook_name: str, dry_run: bool
) -> None:
    for index, hook in enumerate(hooks, 1):
        _info(f"Running {hook_name} hook {index} for {container_name} ...")
        args = ["exec"]
        if hook.user:
            args += ["-u", hook.user]
        if hook.working_dir:
            args += ["-w", hook.working_dir]
        for key, value in hook.environment.items():
            args += ["-e", key if value is None else f"{key}={value}"]
        args.append(container_name)
        args.extend(hook.command)
        result = engine.run(args, check=False, dry_run=dry_run)
        if result.returncode != 0:
            raise ComposeError(
                f"{container_name}: {hook_name} hook {index} exited with "
                f"code {result.returncode}"
            )


def _monitor_job_exits(
    project: Project,
    service_names: List[str],
    *,
    abort_on_exit: bool,
    abort_on_failure: bool,
    exit_code_from: Optional[str],
    dry_run: bool,
) -> int:
    selected = set(service_names)
    while True:
        containers = [
            entry
            for entry in _project_containers(project)
            if entry["service"] in selected
        ]
        if not containers:
            raise ComposeError("no containers found while monitoring job exits")
        stopped = [entry for entry in containers if not entry["running"]]
        trigger = None
        if exit_code_from:
            trigger = next(
                (entry for entry in stopped if entry["service"] == exit_code_from),
                None,
            )
        if trigger is None and abort_on_failure:
            trigger = next(
                (entry for entry in stopped if int(entry["exit_code"] or 0) != 0),
                None,
            )
        if trigger is None and abort_on_exit and stopped:
            trigger = stopped[0]
        if trigger is not None:
            for entry in _ordered_containers(project, containers, reverse=True):
                if not entry["running"]:
                    continue
                service = project.services.get(entry["service"])
                if service is not None:
                    try:
                        _run_lifecycle_hooks(
                            entry["name"], service.pre_stop, "pre_stop", dry_run
                        )
                    except ComposeError as exc:
                        _err(f"warning: {exc}")
                engine.run(
                    [
                        "stop", "-t",
                        str(_stop_timeout(project, entry["service"], 10)),
                        entry["name"],
                    ],
                    capture=True,
                    dry_run=dry_run,
                )
            return int(trigger["exit_code"] or 0)
        if len(stopped) == len(containers):
            if exit_code_from:
                target = next(
                    (entry for entry in stopped if entry["service"] == exit_code_from),
                    None,
                )
                return int((target or {}).get("exit_code") or 0)
            return max((int(entry["exit_code"] or 0) for entry in stopped), default=0)
        time.sleep(0.5)


def cmd_up(ns: argparse.Namespace) -> int:
    ns._created_containers = []
    try:
        return _cmd_up(ns)
    except Exception:
        if not ns.dry_run:
            for container_name in reversed(ns._created_containers):
                _err(f"rolling back container {container_name}")
                try:
                    engine.run(
                        ["remove", "-f", container_name], capture=True, check=False
                    )
                except WslcError as exc:
                    _err(f"warning: could not roll back {container_name}: {exc}")
        raise


def _cmd_up(ns: argparse.Namespace) -> int:
    project = _load(ns)
    services = _select_services(project, ns.services, ns.profile)
    if not services:
        _err("no services to start")
        return 1
    if getattr(ns, "force_recreate", False) and getattr(ns, "no_recreate", False):
        raise ComposeError("--force-recreate and --no-recreate cannot be used together")
    if getattr(ns, "no_start", False) and ns.wait:
        raise ComposeError("--no-start and --wait cannot be used together")
    abort_on_exit = getattr(ns, "abort_on_container_exit", False)
    abort_on_failure = getattr(ns, "abort_on_container_failure", False)
    exit_code_from = getattr(ns, "exit_code_from", None)
    job_exit_requested = bool(abort_on_exit or abort_on_failure or exit_code_from)
    if job_exit_requested and ns.detach:
        raise ComposeError("job exit options cannot be used with --detach")
    if exit_code_from and exit_code_from not in project.services:
        raise ComposeError(f"no such service: {exit_code_from}")

    scale: Dict[str, int] = {}
    for spec in ns.scale or []:
        name, _, count = spec.partition("=")
        if not count.isdigit():
            raise ComposeError(f"invalid --scale value: {spec!r} (expected service=N)")
        scale[name] = int(count)

    readiness_deadline = time.monotonic() + ns.wait_timeout

    needed_networks = {n for svc in services for n in svc.networks}
    for net in project.networks.values():
        if net.name not in needed_networks:
            continue
        if net.external:
            if net.name not in engine.network_names():
                raise ComposeError(f"external network {net.name!r} not found")
        elif engine.ensure_network(net.name, dry_run=ns.dry_run):
            _info(f"Network {net.name} created")

    needed_volumes = {
        m.source for svc in services for m in svc.volumes if m.type == "volume"
    }
    for vol in project.volumes.values():
        if vol.name not in needed_volumes:
            continue
        if vol.external:
            if vol.name not in engine.volume_names():
                raise ComposeError(f"external volume {vol.name!r} not found")
        elif engine.ensure_volume(vol.name, dry_run=ns.dry_run):
            _info(f"Volume {vol.name} created")

    for service in services:
        replicas = scale.get(service.name, service.replicas)
        for mount in service.volumes:
            if not mount.anonymous or mount.source is None:
                continue
            for index in range(1, replicas + 1):
                name = f"{mount.source}-{index}"
                if engine.ensure_volume(name, dry_run=ns.dry_run):
                    _info(f"Volume {name} created")

    for svc in services:
        _prepare_service_image(
            project, svc, build=ns.build, no_build=ns.no_build, pull=ns.pull, dry_run=ns.dry_run
        )

    existing_entries = _project_containers(project) if not ns.dry_run else []
    orphans = [
        entry for entry in existing_entries if entry["service"] not in project.services
    ]
    if orphans and getattr(ns, "remove_orphans", False):
        for entry in orphans:
            _info(f"Removing orphan container {entry['name']} ...")
            engine.run(
                ["remove", "-f", entry["name"]], capture=True, dry_run=ns.dry_run
            )
    elif orphans:
        names = ", ".join(sorted(entry["name"] for entry in orphans))
        _err(f"warning: found orphan containers: {names}; use --remove-orphans")
    existing = {
        entry["name"]: entry
        for entry in existing_entries
        if entry["service"] in project.services
    }
    started: List[str] = []
    ready_dependencies = set()

    for svc in services:
        if not ns.dry_run:
            for dependency_name, dependency in svc.dependencies.items():
                readiness_key = (dependency_name, dependency.condition)
                if readiness_key in ready_dependencies:
                    continue
                dependency_service = project.services[dependency_name]
                dependency_replicas = scale.get(
                    dependency_name, dependency_service.replicas
                )
                try:
                    if dependency.condition == "service_healthy":
                        _wait_for_service_health(
                            project, dependency_service, readiness_deadline, dependency_replicas
                        )
                    elif dependency.condition == "service_completed_successfully":
                        _wait_for_service_completion(
                            project, dependency_service, readiness_deadline, dependency_replicas
                        )
                except ComposeError:
                    if dependency.required:
                        raise
                    _err(f"warning: optional dependency {dependency_name} is not ready")
                ready_dependencies.add(readiness_key)
        replicas = scale.get(svc.name, svc.replicas)
        if svc.container_name and replicas > 1:
            raise ComposeError(
                f"{svc.name}: cannot scale a service with container_name set"
            )
        desired_hash = svc.config_hash()
        for index in range(1, replicas + 1):
            cname = project.container_name(svc, index)
            current = existing.pop(cname, None)
            recreate_dependency = bool(
                getattr(ns, "always_recreate_deps", False)
                and ns.services
                and svc.name not in ns.services
            )
            if current is not None:
                if getattr(ns, "no_recreate", False):
                    fresh = True
                else:
                    fresh = current["hash"] == desired_hash and not (
                        ns.force_recreate
                        or recreate_dependency
                        or (
                            getattr(ns, "renew_anon_volumes", False)
                            and any(mount.anonymous for mount in svc.volumes)
                        )
                    )
                if fresh and current["running"]:
                    _info(f"Container {cname} is up-to-date")
                    continue
                if fresh and not current["running"]:
                    if getattr(ns, "no_start", False):
                        _info(f"Container {cname} is up-to-date")
                        continue
                    _info(f"Starting {cname} ...")
                    engine.run_retried(["start", cname], dry_run=ns.dry_run)
                    started.append(cname)
                    _run_lifecycle_hooks(
                        cname, svc.post_start, "post_start", ns.dry_run
                    )
                    continue
                _info(f"Recreating {cname} ...")
                if current["running"]:
                    engine.run(
                        ["stop", "-t", str(_stop_timeout(project, svc.name, ns.timeout)), cname], capture=True, dry_run=ns.dry_run
                    )
                engine.run(["remove", "-f", cname], capture=True, dry_run=ns.dry_run)
                if getattr(ns, "renew_anon_volumes", False):
                    for mount in svc.volumes:
                        if not mount.anonymous or mount.source is None:
                            continue
                        volume_name = f"{mount.source}-{index}"
                        engine.run(
                            ["volume", "remove", volume_name],
                            capture=True,
                            check=False,
                            dry_run=ns.dry_run,
                        )
                        engine.ensure_volume(volume_name, dry_run=ns.dry_run)

            else:
                _info(f"Creating {cname} ...")
            engine.run(
                flags.run_args(
                    project,
                    svc,
                    index,
                    detach=True,
                    path_mapper=engine.to_host_path,
                    create_only=getattr(ns, "no_start", False),
                ),
                dry_run=ns.dry_run,
            )
            ns._created_containers.append(cname)
            started.append(cname)
            if not getattr(ns, "no_start", False):
                _run_lifecycle_hooks(cname, svc.post_start, "post_start", ns.dry_run)

        # drop replicas beyond the requested scale
        for name, leftover in list(existing.items()):
            if leftover["service"] == svc.name and leftover["index"] > replicas:
                _info(f"Removing surplus {name} ...")
                engine.run(["remove", "-f", name], capture=True, dry_run=ns.dry_run)
                existing.pop(name)

    if ns.wait and not ns.dry_run:
        for service in services:
            if service.healthcheck is not None:
                _wait_for_service_health(
                    project, service, readiness_deadline, scale.get(service.name)
                )

    if job_exit_requested and not ns.dry_run:
        return _monitor_job_exits(
            project,
            [service.name for service in services],
            abort_on_exit=abort_on_exit or bool(exit_code_from),
            abort_on_failure=abort_on_failure,
            exit_code_from=exit_code_from,
            dry_run=ns.dry_run,
        )

    if (
        ns.wait
        or ns.detach
        or ns.dry_run
        or getattr(ns, "no_start", False)
        or not started
    ):
        return 0
    _info("Attaching to logs (Ctrl+C to detach; containers keep running)")
    return _follow_logs(project, service_names=[s.name for s in services], follow=True)


def _stop_timeout(project: Project, service_name: str, fallback: int) -> int:
    service = project.services.get(service_name)
    if service is None or service.stop_grace_period is None:
        return fallback
    return max(0, math.ceil(service.stop_grace_period))


def _ordered_containers(
    project: Project, containers: List[dict], reverse: bool = False
) -> List[dict]:
    by_service = {}
    for entry in containers:
        by_service.setdefault(entry["service"], []).append(entry)
    services = project.sorted_services()
    if reverse:
        services = list(reversed(services))
    ordered = []
    for service in services:
        entries = by_service.pop(service.name, [])
        ordered.extend(sorted(entries, key=lambda item: item["index"], reverse=reverse))
    for entries in by_service.values():
        ordered.extend(entries)
    return ordered


def _ensure_service_resources(
    project: Project, service: Service, dry_run: bool, anonymous_suffix: str = "1"
) -> None:
    networks_by_name = {network.name: network for network in project.networks.values()}
    for name in service.networks:
        network = networks_by_name[name]
        if network.external:
            if name not in engine.network_names():
                raise ComposeError(f"external network {name!r} not found")
        elif engine.ensure_network(name, dry_run=dry_run):
            _info(f"Network {name} created")

    volumes_by_name = {volume.name: volume for volume in project.volumes.values()}
    for mount in service.volumes:
        if mount.type != "volume" or mount.source is None:
            continue
        if mount.anonymous:
            name = f"{mount.source}-{anonymous_suffix}"
            if engine.ensure_volume(name, dry_run=dry_run):
                _info(f"Volume {name} created")
            continue
        volume = volumes_by_name[mount.source]
        if volume.external:
            if volume.name not in engine.volume_names():
                raise ComposeError(f"external volume {volume.name!r} not found")
        elif engine.ensure_volume(volume.name, dry_run=dry_run):
            _info(f"Volume {volume.name} created")


def cmd_run(ns: argparse.Namespace) -> int:
    project = _load(ns)
    if ns.service not in project.services:
        raise ComposeError(f"no such service: {ns.service}")
    service = project.services[ns.service]

    if service.depends_on and not ns.no_deps:
        dependency_ns = argparse.Namespace(**vars(ns))
        dependency_ns.services = list(service.depends_on)
        dependency_ns.detach = True
        dependency_ns.force_recreate = False
        dependency_ns.scale = []
        dependency_ns.timeout = 10
        dependency_ns.wait = True
        dependency_ns.build = ns.build
        dependency_ns.no_build = ns.no_build
        if cmd_up(dependency_ns) != 0:
            return 1

    run_suffix = uuid.uuid4().hex[:8]
    container_name = ns.name or (
        f"{project.name}-{service.name}-run-{run_suffix}"
    )
    _ensure_service_resources(project, service, ns.dry_run, run_suffix)
    _prepare_service_image(
        project,
        service,
        build=ns.build,
        no_build=ns.no_build,
        pull=ns.pull,
        dry_run=ns.dry_run,
    )

    environment = dict(service.environment)
    for item in ns.env or []:
        key, separator, value = item.partition("=")
        if not key:
            raise ComposeError(f"invalid environment override: {item!r}")
        environment[key] = value if separator else None
    one_off = dataclasses.replace(
        service,
        environment=environment,
        tty=service.tty and not ns.no_tty,
    )

    command = list(ns.command)
    if command[:1] == ["--"]:
        command = command[1:]
    entrypoint = shlex.split(ns.entrypoint) if ns.entrypoint is not None else None
    args = flags.run_args(
        project,
        one_off,
        detach=ns.detach,
        path_mapper=engine.to_host_path,
        container_name=container_name,
        command_override=command or None,
        entrypoint_override=entrypoint,
        remove=ns.rm,
        include_ports=ns.service_ports,
        anonymous_volume_suffix=run_suffix,
    )
    result = engine.run(args, check=False, dry_run=ns.dry_run)
    return result.returncode


def cmd_down(ns: argparse.Namespace) -> int:
    project = _load(ns)
    containers = _project_containers(project)
    orphans = [entry for entry in containers if entry["service"] not in project.services]
    if orphans and not getattr(ns, "remove_orphans", False):
        names = ", ".join(sorted(entry["name"] for entry in orphans))
        _err(f"warning: found orphan containers: {names}; use --remove-orphans")
        containers = [
            entry for entry in containers if entry["service"] in project.services
        ]
    for entry in _ordered_containers(project, containers, reverse=True):
        if entry["running"]:
            service = project.services.get(entry["service"])
            if service is not None:
                _run_lifecycle_hooks(
                    entry["name"], service.pre_stop, "pre_stop", ns.dry_run
                )
            _info(f"Stopping {entry['name']} ...")
            engine.run(
                ["stop", "-t", str(_stop_timeout(project, entry["service"], ns.timeout)), entry["name"]], capture=True, dry_run=ns.dry_run
            )
        _info(f"Removing {entry['name']} ...")
        engine.run(["remove", "-f", entry["name"]], capture=True, dry_run=ns.dry_run)

    existing_networks = engine.network_names()
    for net in project.networks.values():
        if not net.external and net.name in existing_networks:
            _info(f"Removing network {net.name}")
            try:
                engine.run(["network", "remove", net.name], capture=True, dry_run=ns.dry_run)
            except WslcError:
                _err(f"warning: could not remove network {net.name} (still in use?)")

    if ns.volumes:
        existing_volumes = engine.volume_names()
        for vol in project.volumes.values():
            if not vol.external and vol.name in existing_volumes:
                _info(f"Removing volume {vol.name}")
                try:
                    engine.run(["volume", "remove", vol.name], capture=True, dry_run=ns.dry_run)
                except WslcError:
                    _err(f"warning: could not remove volume {vol.name}")
        anonymous_prefixes = {
            mount.source + "-"
            for service in project.services.values()
            for mount in service.volumes
            if mount.anonymous and mount.source is not None
        }
        for volume_name in existing_volumes:
            if not any(volume_name.startswith(prefix) for prefix in anonymous_prefixes):
                continue
            _info(f"Removing volume {volume_name}")
            try:
                engine.run(
                    ["volume", "remove", volume_name], capture=True, dry_run=ns.dry_run
                )
            except WslcError:
                _err(f"warning: could not remove volume {volume_name}")
    return 0


def cmd_ps(ns: argparse.Namespace) -> int:
    project = _load(ns)
    containers = _project_containers(project)
    if ns.services:
        containers = [c for c in containers if c["service"] in ns.services]
    if ns.quiet:
        for entry in containers:
            print(entry["id"])
        return 0
    rows = [
        [
            entry["name"] or "",
            entry["service"] or "",
            entry["image"] or "",
            entry["status"] or "",
            _format_ports(entry["ports"]),
        ]
        for entry in sorted(containers, key=lambda c: (c["service"], c["index"]))
    ]
    _print_table(rows, ["NAME", "SERVICE", "IMAGE", "STATUS", "PORTS"])
    return 0


def _normalize_log_time(value: str) -> str:
    try:
        seconds_ago = parse_duration(value, "log time")
    except ComposeError:
        return value
    return str(max(0, int(time.time() - seconds_ago)))


def _follow_logs(
    project: Project,
    service_names: List[str],
    follow: bool,
    tail: Optional[int] = None,
    timestamps: bool = False,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> int:
    containers = [
        c
        for c in _project_containers(project)
        if not service_names or c["service"] in service_names
    ]
    if not containers:
        _err("no containers found")
        return 1

    width = max(len(c["name"]) for c in containers)
    procs: List[subprocess.Popen] = []
    threads: List[threading.Thread] = []
    lock = threading.Lock()

    def pump(entry: dict, proc: subprocess.Popen) -> None:
        prefix = entry["name"].ljust(width)
        for line in proc.stdout:  # type: ignore[union-attr]
            with lock:
                sys.stdout.write(f"{prefix} | {line}")
                sys.stdout.flush()

    for entry in containers:
        args = ["logs"]
        if follow and entry["running"]:
            args.append("-f")
        if tail is not None:
            args += ["-n", str(tail)]
        if timestamps:
            args.append("-t")
        if since is not None:
            args += ["--since", _normalize_log_time(since)]
        if until is not None:
            args += ["--until", _normalize_log_time(until)]
        args.append(entry["name"])
        proc = engine.popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        procs.append(proc)
        thread = threading.Thread(target=pump, args=(entry, proc), daemon=True)
        thread.start()
        threads.append(thread)

    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        for proc in procs:
            proc.send_signal(signal.SIGTERM)
        return 0
    return 0


def cmd_logs(ns: argparse.Namespace) -> int:
    project = _load(ns)
    return _follow_logs(
        project,
        service_names=ns.services,
        follow=ns.follow,
        tail=ns.tail,
        timestamps=ns.timestamps,
        since=ns.since,
        until=ns.until,
    )


def cmd_exec(ns: argparse.Namespace) -> int:
    project = _load(ns)
    if ns.service not in project.services:
        raise ComposeError(f"no such service: {ns.service}")
    cname = project.container_name(project.services[ns.service], ns.index)
    args = ["exec"]
    if not ns.no_tty and sys.stdin.isatty():
        args += ["-i", "-t"]
    if ns.user:
        args += ["-u", ns.user]
    if ns.workdir:
        args += ["-w", ns.workdir]
    for env in ns.env or []:
        args += ["-e", env]
    args.append(cname)
    args += ns.command
    proc = engine.run(args, check=False, dry_run=ns.dry_run)
    return proc.returncode


def _selected_containers(project: Project, services: List[str]) -> List[dict]:
    for service in services:
        if service not in project.services:
            raise ComposeError(f"no such service: {service}")
    containers = _project_containers(project)
    if services:
        containers = [entry for entry in containers if entry["service"] in services]
    return containers


def cmd_kill(ns: argparse.Namespace) -> int:
    project = _load(ns)
    containers = _selected_containers(project, ns.services)
    if not containers:
        raise ComposeError("no containers found")
    for entry in _ordered_containers(project, containers, reverse=True):
        if entry["running"]:
            engine.run(
                ["kill", "-s", ns.signal, entry["name"]],
                dry_run=ns.dry_run,
            )
    return 0


def cmd_rm(ns: argparse.Namespace) -> int:
    project = _load(ns)
    containers = _selected_containers(project, ns.services)
    if not containers:
        raise ComposeError("no containers found")
    for entry in _ordered_containers(project, containers, reverse=True):
        if entry["running"]:
            if not ns.stop:
                _err(f"warning: {entry['name']} is running; use --stop to remove it")
                continue
            engine.run(
                [
                    "stop",
                    "-t",
                    str(_stop_timeout(project, entry["service"], ns.timeout)),
                    entry["name"],
                ],
                capture=True,
                dry_run=ns.dry_run,
            )
        engine.run(["remove", "-f", entry["name"]], dry_run=ns.dry_run)
    return 0


def cmd_wait(ns: argparse.Namespace) -> int:
    project = _load(ns)
    deadline = time.monotonic() + ns.timeout if ns.timeout is not None else None
    while True:
        containers = _selected_containers(project, ns.services)
        if not containers:
            raise ComposeError("no containers found")
        if not any(entry["running"] for entry in containers):
            exit_codes = [
                int(entry["exit_code"])
                for entry in containers
                if entry["exit_code"] is not None
            ]
            return max(exit_codes, default=0)
        if deadline is not None and time.monotonic() >= deadline:
            raise ComposeError("timed out waiting for project containers")
        time.sleep(0.5)


def cmd_images(ns: argparse.Namespace) -> int:
    project = _load(ns)
    rows = []
    for service in _select_services(project, ns.services, ns.profile):
        image = flags.image_name(project, service)
        rows.append([service.name, image, "yes" if engine.image_exists(image) else "no"])
    _print_table(rows, ["SERVICE", "IMAGE", "AVAILABLE"])
    return 0


def cmd_push(ns: argparse.Namespace) -> int:
    project = _load(ns)
    pushed = 0
    for service in _select_services(project, ns.services, ns.profile):
        if not service.image:
            _err(f"warning: {service.name} has no image name; skipping")
            continue
        engine.run(["push", service.image], dry_run=ns.dry_run)
        pushed += 1
    if not pushed:
        raise ComposeError("no service images to push")
    return 0


def cmd_port(ns: argparse.Namespace) -> int:
    project = _load(ns)
    containers = [
        entry
        for entry in _selected_containers(project, [ns.service])
        if entry["index"] == ns.index
    ]
    if not containers:
        raise ComposeError(f"no container found for {ns.service} index {ns.index}")
    port_text = str(ns.private_port)
    private_port, _, protocol = port_text.partition("/")
    expected_protocol = protocol or "tcp"
    matches = []
    for port in containers[0]["ports"]:
        actual_protocol = PROTOCOLS.get(port.get("Protocol"), str(port.get("Protocol", "")))
        if (
            str(port.get("ContainerPort")) == private_port
            and actual_protocol == expected_protocol
        ):
            address = port.get("BindingAddress") or "0.0.0.0"
            matches.append(f"{address}:{port.get('HostPort')}")
    if not matches:
        raise ComposeError(f"port {port_text} is not published for service {ns.service}")
    for match in matches:
        print(match)
    return 0


def cmd_create(ns: argparse.Namespace) -> int:
    ns.detach = True
    ns.no_start = True
    ns.wait = False
    return cmd_up(ns)


def _lifecycle(ns: argparse.Namespace, action: str) -> int:
    project = _load(ns)
    containers = _project_containers(project)
    if ns.services:
        containers = [c for c in containers if c["service"] in ns.services]
    if not containers:
        _err("no containers found")
        return 1
    if action in ("stop", "restart"):
        for entry in _ordered_containers(project, containers, reverse=True):
            if not entry["running"]:
                continue
            service = project.services.get(entry["service"])
            if service is not None:
                _run_lifecycle_hooks(
                    entry["name"], service.pre_stop, "pre_stop", ns.dry_run
                )
            _info(f"Stopping {entry['name']} ...")
            engine.run(
                [
                    "stop",
                    "-t",
                    str(_stop_timeout(project, entry["service"], ns.timeout)),
                    entry["name"],
                ],
                capture=True,
                dry_run=ns.dry_run,
            )
    if action in ("start", "restart"):
        for entry in _ordered_containers(project, containers):
            _info(f"Starting {entry['name']} ...")
            engine.run_retried(["start", entry["name"]], dry_run=ns.dry_run)
            service = project.services.get(entry["service"])
            if service is not None:
                _run_lifecycle_hooks(
                    entry["name"], service.post_start, "post_start", ns.dry_run
                )
    return 0


def cmd_pull(ns: argparse.Namespace) -> int:
    project = _load(ns)
    for svc in _select_services(project, ns.services, ns.profile):
        if svc.image:
            _info(f"Pulling {svc.image} ...")
            engine.run(["pull", svc.image], dry_run=ns.dry_run)
    return 0


def cmd_build(ns: argparse.Namespace) -> int:
    project = _load(ns)
    built = 0
    for svc in _select_services(project, ns.services, ns.profile):
        if not svc.build:
            continue
        _info(f"Building {svc.name} ...")
        engine.run(
            flags.build_args(
                project, svc, no_cache=ns.no_cache, path_mapper=engine.to_host_path
            ),
            dry_run=ns.dry_run,
        )
        built += 1
    if not built:
        _err("no services with a build section")
        return 1
    return 0


def cmd_config(ns: argparse.Namespace) -> int:
    if ns.capabilities:
        report = {
            "runtime": "wslc",
            "unsupported": UNSUPPORTED_CAPABILITIES,
            "limitations": {
                "networks_per_container": 1,
                "persistent_health_monitor": False,
                "restart_policies": False,
                "build_secrets": False,
                "external_configs": False,
            },
        }
        print(yaml.safe_dump(report, sort_keys=False, default_flow_style=False))
        return 0
    project = _load(ns)
    print(yaml.safe_dump(dataclasses.asdict(project), sort_keys=False, default_flow_style=False))
    return 0


def cmd_version(ns: argparse.Namespace) -> int:
    print(f"wslc-compose {__version__}")
    try:
        engine.run(["version"], check=False)
    except WslcError as exc:
        _err(str(exc))
    return 0


# --- argument parsing ---------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wslc-compose",
        description="docker-compose style orchestration for WSL containers (wslc)",
    )
    parser.add_argument(
        "-f",
        "--file",
        action="append",
        help="compose file; repeat to merge overrides (default: auto-detect)",
    )
    parser.add_argument("-p", "--project-name", help="project name (default: directory name)")
    parser.add_argument("--env-file", help="alternate .env file")
    parser.add_argument("--dry-run", action="store_true", help="print wslc commands instead of running them")
    parser.add_argument("--profile", action="append", default=[], help="enable a compose profile")
    parser.add_argument(
        "--ignore-unsupported",
        action="store_true",
        help="warn and continue when wslc cannot honor a Compose option",
    )
    parser.add_argument("--version", action="version", version=f"wslc-compose {__version__}")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("up", help="create and start services")
    p.add_argument("services", nargs="*")
    p.add_argument("-d", "--detach", action="store_true")
    p.add_argument("--build", action="store_true", help="rebuild images before starting")
    p.add_argument("--no-build", action="store_true", help="never build service images")
    p.add_argument("--pull", choices=("always", "missing", "never"))
    p.add_argument("--force-recreate", action="store_true")
    p.add_argument("--scale", action="append", metavar="SERVICE=N")
    p.add_argument("--wait", action="store_true", help="wait for services to be ready")
    p.add_argument("--wait-timeout", type=float, default=60.0, metavar="SECONDS")
    p.add_argument("--remove-orphans", action="store_true")
    p.add_argument("--no-start", action="store_true")
    p.add_argument("--no-recreate", action="store_true")
    p.add_argument("--always-recreate-deps", action="store_true")
    p.add_argument("--renew-anon-volumes", action="store_true")
    p.add_argument("--abort-on-container-exit", action="store_true")
    p.add_argument("--abort-on-container-failure", action="store_true")
    p.add_argument("--exit-code-from", metavar="SERVICE")
    p.add_argument("-t", "--timeout", type=int, default=10)
    p.set_defaults(func=cmd_up)

    p = sub.add_parser("create", help="create services without starting them")
    p.add_argument("services", nargs="*")
    p.add_argument("--build", action="store_true")
    p.add_argument("--no-build", action="store_true")
    p.add_argument("--pull", choices=("always", "missing", "never"))
    p.add_argument("--force-recreate", action="store_true")
    p.add_argument("--no-recreate", action="store_true")
    p.add_argument("--always-recreate-deps", action="store_true")
    p.add_argument("--renew-anon-volumes", action="store_true")
    p.add_argument("--remove-orphans", action="store_true")
    p.add_argument("--scale", action="append", metavar="SERVICE=N")
    p.add_argument("--wait-timeout", type=float, default=60.0, metavar="SECONDS")
    p.add_argument("-t", "--timeout", type=int, default=10)
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("down", help="stop and remove project containers and networks")
    p.add_argument("-v", "--volumes", action="store_true", help="also remove named volumes")
    p.add_argument("--remove-orphans", action="store_true")
    p.add_argument("-t", "--timeout", type=int, default=10)
    p.set_defaults(func=cmd_down)

    p = sub.add_parser("ps", help="list project containers")
    p.add_argument("services", nargs="*")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_ps)

    p = sub.add_parser("logs", help="show container logs")
    p.add_argument("services", nargs="*")
    p.add_argument("-f", "--follow", action="store_true")
    p.add_argument("-n", "--tail", type=int)
    p.add_argument("-t", "--timestamps", action="store_true")
    p.add_argument("--since")
    p.add_argument("--until")
    p.set_defaults(func=cmd_logs)

    p = sub.add_parser("run", help="run a one-off command for a service")
    p.add_argument("-d", "--detach", action="store_true")
    p.add_argument("--name")
    p.add_argument("--rm", action="store_true", help="remove the container after it exits")
    p.add_argument("--no-deps", action="store_true", help="do not start dependencies")
    p.add_argument("--service-ports", action="store_true")
    p.add_argument("-T", "--no-tty", action="store_true")
    p.add_argument("-e", "--env", action="append")
    p.add_argument("--entrypoint")
    p.add_argument("--pull", choices=("always", "missing", "never"))
    p.add_argument("--build", action="store_true")
    p.add_argument("--no-build", action="store_true")
    p.add_argument("--wait-timeout", type=float, default=60.0, metavar="SECONDS")
    p.add_argument("service")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("exec", help="run a command in a service container")
    p.add_argument("service")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.add_argument("--index", type=int, default=1)
    p.add_argument("-u", "--user")
    p.add_argument("-w", "--workdir")
    p.add_argument("-e", "--env", action="append")
    p.add_argument("-T", "--no-tty", action="store_true")
    p.set_defaults(func=cmd_exec)

    for action in ("start", "stop", "restart"):
        p = sub.add_parser(action, help=f"{action} project containers")
        p.add_argument("services", nargs="*")
        p.add_argument("-t", "--timeout", type=int, default=10)
        p.set_defaults(func=lambda ns, a=action: _lifecycle(ns, a))

    p = sub.add_parser("kill", help="force stop service containers")
    p.add_argument("services", nargs="*")
    p.add_argument("-s", "--signal", default="KILL")
    p.set_defaults(func=cmd_kill)

    p = sub.add_parser("rm", help="remove service containers")
    p.add_argument("services", nargs="*")
    p.add_argument("-f", "--force", action="store_true")
    p.add_argument("-s", "--stop", action="store_true")
    p.add_argument("-t", "--timeout", type=int, default=10)
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser("wait", help="wait for service containers to stop")
    p.add_argument("services", nargs="*")
    p.add_argument("--timeout", type=float)
    p.set_defaults(func=cmd_wait)

    p = sub.add_parser("images", help="list images used by services")
    p.add_argument("services", nargs="*")
    p.set_defaults(func=cmd_images)

    p = sub.add_parser("push", help="push service images")
    p.add_argument("services", nargs="*")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("port", help="print a published port for a service")
    p.add_argument("service")
    p.add_argument("private_port")
    p.add_argument("--index", type=int, default=1)
    p.set_defaults(func=cmd_port)

    p = sub.add_parser("pull", help="pull service images")
    p.add_argument("services", nargs="*")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("build", help="build service images")
    p.add_argument("services", nargs="*")
    p.add_argument("--no-cache", action="store_true")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("config", help="print the resolved configuration")
    p.add_argument(
        "--capabilities",
        action="store_true",
        help="print wslc-compose runtime capabilities without loading a project",
    )
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("version", help="show version information")
    p.set_defaults(func=cmd_version)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    try:
        return ns.func(ns)
    except (ComposeError, WslcError) as exc:
        _err(str(exc))
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
