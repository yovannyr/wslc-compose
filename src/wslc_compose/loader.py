"""Load, interpolate and normalize a compose file into a Project."""

from __future__ import annotations

import copy
import hashlib
import os
import re
import shlex
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from wslc_compose.interpolation import interpolate_tree
from wslc_compose.model import (
    BuildConfig,
    Config,
    ConfigMount,
    Dependency,
    Healthcheck,
    Network,
    PortMapping,
    Project,
    Secret,
    SecretMount,
    Service,
    Volume,
    VolumeMount,
)

COMPOSE_FILENAMES = (
    "compose.yaml",
    "compose.yml",
    "docker-compose.yml",
    "docker-compose.yaml",
)

# Compose keys the current wslc runtime cannot enforce. Ignoring these keys changes
# container semantics, so the CLI rejects them unless compatibility mode is requested.
UNSUPPORTED_KEYS = {
    "cap_add": "capabilities are not configurable with wslc",
    "cap_drop": "capabilities are not configurable with wslc",
    "privileged": "privileged mode is not supported by wslc",
    "devices": "device mapping is not supported by wslc (see 'gpus' for GPUs)",
    "extra_hosts": "extra_hosts is not supported by wslc",
    "sysctls": "sysctls are not supported by wslc",
    "init": "init is not supported by wslc",
    "pid": "pid mode is not supported by wslc",
    "ipc": "ipc mode is not supported by wslc",
    "read_only": "read_only rootfs is not supported by wslc",
    "security_opt": "security_opt is not supported by wslc",
    "logging": "logging drivers are not configurable with wslc",
    "healthcheck_disable": "",
}

UNSUPPORTED_CAPABILITIES = {
    key: message for key, message in UNSUPPORTED_KEYS.items() if message
}

_WIN_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


class ComposeError(ValueError):
    pass


def _unique_resource_key(kind: str, item: Any) -> Optional[Tuple[Any, ...]]:
    """Return the Compose uniqueness key for mergeable service resources."""
    if kind == "volumes":
        if isinstance(item, dict):
            return (item.get("target"),)
        parts = _split_volume_spec(str(item))
        return (parts[0] if len(parts) == 1 else parts[1],)
    if kind in ("secrets", "configs"):
        if isinstance(item, dict):
            return (item.get("target") or item.get("source"),)
        return (str(item),)
    if kind == "ports":
        if isinstance(item, dict):
            return (
                item.get("host_ip"),
                item.get("target"),
                str(item.get("published")) if item.get("published") is not None else None,
                item.get("protocol", "tcp"),
            )
        return tuple(port.to_flag() for port in parse_port(item))
    return None


def merge_compose_models(base: Any, override: Any, path: Tuple[str, ...] = ()) -> Any:
    """Merge two interpolated Compose models using specification-aware rules."""
    if path and path[-1] in ("command", "entrypoint"):
        return copy.deepcopy(override)
    if len(path) >= 2 and path[-2:] == ("healthcheck", "test"):
        return copy.deepcopy(override)

    if isinstance(base, dict) and isinstance(override, dict):
        result = copy.deepcopy(base)
        for key, value in override.items():
            key = str(key)
            if key in result:
                result[key] = merge_compose_models(result[key], value, path + (key,))
            else:
                result[key] = copy.deepcopy(value)
        return result

    if isinstance(base, list) and isinstance(override, list):
        kind = path[-1] if path else ""
        if kind not in ("ports", "volumes", "secrets", "configs"):
            return copy.deepcopy(base) + copy.deepcopy(override)

        result = copy.deepcopy(base)
        indexes = {
            _unique_resource_key(kind, item): index for index, item in enumerate(result)
        }
        for item in override:
            unique_key = _unique_resource_key(kind, item)
            if unique_key in indexes:
                index = indexes[unique_key]
                result[index] = merge_compose_models(
                    result[index], item, path + (str(index),)
                )
            else:
                indexes[unique_key] = len(result)
                result.append(copy.deepcopy(item))
        return result

    return copy.deepcopy(override)


def find_compose_file(directory: str) -> Optional[str]:
    for name in COMPOSE_FILENAMES:
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            return path
    return None


def load_dotenv(path: str) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not os.path.isfile(path):
        return env
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            env[key] = value
    return env


def normalize_project_name(raw: str) -> str:
    name = raw.strip().lower()
    name = re.sub(r"[^a-z0-9_-]", "", name)
    name = name.lstrip("_-")
    if not name:
        raise ComposeError(f"cannot derive a valid project name from {raw!r}")
    return name


def _as_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _as_command(value) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        return shlex.split(value)
    return [str(v) for v in value]


def _as_mapping(value, what: str) -> Dict[str, str]:
    """Accept both list ("K=V") and mapping syntax for labels/build args."""
    result: Dict[str, str] = {}
    if value is None:
        return result
    if isinstance(value, dict):
        for k, v in value.items():
            result[str(k)] = "" if v is None else str(v)
        return result
    if isinstance(value, list):
        for item in value:
            key, sep, val = str(item).partition("=")
            result[key] = val if sep else ""
        return result
    raise ComposeError(f"{what}: expected list or mapping, got {type(value).__name__}")


def _as_environment(value) -> Dict[str, Optional[str]]:
    result: Dict[str, Optional[str]] = {}
    if value is None:
        return result
    if isinstance(value, dict):
        for k, v in value.items():
            result[str(k)] = None if v is None else str(v)
        return result
    if isinstance(value, list):
        for item in value:
            key, sep, val = str(item).partition("=")
            result[key] = val if sep else None
        return result
    raise ComposeError(f"environment: expected list or mapping, got {type(value).__name__}")


def parse_port(spec) -> List[PortMapping]:
    if isinstance(spec, dict):  # long syntax
        target = int(spec["target"])
        published = spec.get("published")
        host_ip = spec.get("host_ip")
        pub = str(published) if published is not None else None
        if host_ip and pub:
            pub = f"{host_ip}:{pub}"
        return [PortMapping(target, pub, str(spec.get("protocol", "tcp")))]

    text = str(spec)
    protocol = "tcp"
    if "/" in text:
        text, protocol = text.rsplit("/", 1)

    parts = text.split(":")
    if len(parts) == 1:
        host, target = None, parts[0]
    elif len(parts) == 2:
        host, target = parts[0], parts[1]
    elif len(parts) == 3:
        host, target = f"{parts[0]}:{parts[1]}", parts[2]
    else:
        raise ComposeError(f"invalid port mapping: {spec!r}")

    def expand(rng: str) -> List[int]:
        if "-" in rng:
            lo, hi = rng.split("-", 1)
            return list(range(int(lo), int(hi) + 1))
        return [int(rng)]

    targets = expand(target)
    if host is None:
        return [PortMapping(t, None, protocol) for t in targets]
    ip_prefix = ""
    host_ports = host
    if host.count(":") == 1:  # ip:port form
        ip_prefix, host_ports = host.split(":", 1)
        ip_prefix += ":"
    hosts = expand(host_ports)
    if len(hosts) != len(targets):
        raise ComposeError(f"port range mismatch in {spec!r}")
    return [PortMapping(t, f"{ip_prefix}{h}", protocol) for h, t in zip(hosts, targets)]


def _split_volume_spec(spec: str) -> Tuple[str, ...]:
    """Split 'src:dst[:opts]' respecting Windows drive letters (E:\\x:/data)."""
    parts = spec.split(":")
    merged: List[str] = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if (
            len(part) == 1
            and part.isalpha()
            and i + 1 < len(parts)
            and parts[i + 1][:1] in ("\\", "/")
        ):
            merged.append(part + ":" + parts[i + 1])
            i += 2
        else:
            merged.append(part)
            i += 1
    return tuple(merged)


def _is_host_path(source: str) -> bool:
    return (
        source.startswith(("/", "./", "../", "~", "\\\\"))
        or source in (".", "..")
        or bool(_WIN_PATH_RE.match(source))
    )


def parse_volume(spec, project_dir: str) -> VolumeMount:
    if isinstance(spec, dict):  # long syntax
        vtype = spec.get("type", "volume")
        source = spec.get("source")
        target = spec.get("target")
        if not target:
            raise ComposeError(f"volume entry missing target: {spec!r}")
        read_only = bool(spec.get("read_only", False))
        if vtype == "bind" and source:
            source = _resolve_bind_source(source, project_dir)
        return VolumeMount(vtype, source, target, read_only)

    parts = _split_volume_spec(str(spec))
    if len(parts) == 1:
        raise ComposeError(
            f"anonymous volumes are not supported by wslc: {spec!r}; name the volume or use a bind mount"
        )
    if len(parts) == 2:
        source, target, opts = parts[0], parts[1], ""
    elif len(parts) == 3:
        source, target, opts = parts
    else:
        raise ComposeError(f"invalid volume spec: {spec!r}")
    read_only = "ro" in opts.split(",") if opts else False
    if _is_host_path(source):
        return VolumeMount("bind", _resolve_bind_source(source, project_dir), target, read_only)
    return VolumeMount("volume", source, target, read_only)


def _resolve_bind_source(source: str, project_dir: str) -> str:
    if _WIN_PATH_RE.match(source) or source.startswith("\\\\"):
        return source  # already a Windows path, hand to wslc as-is
    source = os.path.expanduser(source)
    if not os.path.isabs(source):
        source = os.path.normpath(os.path.join(project_dir, source))
    return source


def _parse_secret_definitions(value, project_dir: str) -> Dict[str, Secret]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ComposeError(f"secrets: expected mapping, got {type(value).__name__}")

    result: Dict[str, Secret] = {}
    for raw_key, cfg in value.items():
        key = str(raw_key)
        if not isinstance(cfg, dict):
            raise ComposeError(
                f"secret {key!r}: expected a mapping with a 'file' source"
            )
        if cfg.get("external"):
            raise ComposeError(
                f"secret {key!r}: external secrets are not supported by wslc; "
                "use a file-backed secret"
            )
        if "environment" in cfg:
            raise ComposeError(
                f"secret {key!r}: environment-backed secrets are not supported by "
                "wslc; write the value to a protected host file and use 'file'"
            )
        if not cfg.get("file"):
            raise ComposeError(
                f"secret {key!r}: only file-backed secrets are supported by wslc"
            )

        source = _resolve_bind_source(str(cfg["file"]), project_dir)
        is_windows_path_from_wsl = os.name != "nt" and (
            bool(_WIN_PATH_RE.match(source)) or source.startswith("\\\\")
        )
        if not is_windows_path_from_wsl and not os.path.isfile(source):
            raise ComposeError(f"secret {key!r}: file not found: {source}")
        result[key] = Secret(key=key, file=source)
    return result


def _parse_service_secrets(
    value,
    definitions: Dict[str, Secret],
    service_name: str,
) -> Tuple[List[SecretMount], List[str]]:
    if value is None:
        return [], []
    if not isinstance(value, list):
        raise ComposeError(
            f"{service_name}: secrets: expected list, got {type(value).__name__}"
        )

    mounts: List[SecretMount] = []
    warnings: List[str] = []
    targets = set()
    for spec in value:
        if isinstance(spec, str):
            source = spec
            target = spec
            ignored = []
        elif isinstance(spec, dict):
            if not spec.get("source"):
                raise ComposeError(
                    f"{service_name}: secret entry missing source: {spec!r}"
                )
            source = str(spec["source"])
            target = str(spec.get("target") or source)
            ignored = [key for key in ("uid", "gid", "mode") if key in spec]
        else:
            raise ComposeError(
                f"{service_name}: invalid secret entry: expected string or mapping"
            )

        definition = definitions.get(source)
        if definition is None:
            raise ComposeError(
                f"service {service_name!r} references undefined secret {source!r}"
            )

        if target.startswith("/"):
            container_target = target
        else:
            if target in ("", ".", "..") or "/" in target or "\\" in target:
                raise ComposeError(
                    f"{service_name}: secret {source!r} has invalid target {target!r}; "
                    "use a filename or an absolute container path"
                )
            container_target = f"/run/secrets/{target}"

        if container_target in targets:
            raise ComposeError(
                f"{service_name}: multiple secrets target {container_target!r}"
            )
        targets.add(container_target)
        mounts.append(
            SecretMount(
                source=source,
                file=definition.file,
                target=container_target,
            )
        )
        if ignored:
            warnings.append(
                f"secret {source!r}: {', '.join(ignored)} cannot be enforced by "
                "wslc file mounts and will be ignored"
            )
    return mounts, warnings


def _materialize_config(key: str, content: str) -> str:
    digest = hashlib.sha256(content.encode()).hexdigest()[:16]
    safe_key = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
    directory = os.path.join(tempfile.gettempdir(), "wslc-compose", "configs")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{safe_key}-{digest}")
    if not os.path.isfile(path):
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
    return path


def _parse_config_definitions(
    value: Any, project_dir: str, env: Dict[str, str]
) -> Dict[str, Config]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ComposeError(f"configs: expected mapping, got {type(value).__name__}")

    result = {}
    for raw_key, cfg in value.items():
        key = str(raw_key)
        if not isinstance(cfg, dict):
            raise ComposeError(f"config {key!r}: expected a mapping")
        if cfg.get("external"):
            raise ComposeError(
                f"config {key!r}: external configs are not supported because wslc "
                "has no config object store"
            )
        sources = [source for source in ("file", "content", "environment") if source in cfg]
        if len(sources) != 1:
            raise ComposeError(
                f"config {key!r}: exactly one of file, content, or environment is required"
            )
        source = sources[0]
        if source == "file":
            path = _resolve_bind_source(str(cfg[source]), project_dir)
            if not os.path.isfile(path):
                raise ComposeError(f"config {key!r}: file not found: {path}")
        elif source == "environment":
            variable = str(cfg[source])
            if variable not in env:
                raise ComposeError(
                    f"config {key!r}: environment variable {variable!r} is not set"
                )
            path = _materialize_config(key, env[variable])
        else:
            path = _materialize_config(key, str(cfg[source]))
        result[key] = Config(key=key, file=path)
    return result


def _parse_service_configs(
    value: Any,
    definitions: Dict[str, Config],
    service_name: str,
) -> Tuple[List[ConfigMount], List[str]]:
    if value is None:
        return [], []
    if not isinstance(value, list):
        raise ComposeError(
            f"{service_name}: configs: expected list, got {type(value).__name__}"
        )

    mounts = []
    warnings = []
    targets = set()
    for spec in value:
        if isinstance(spec, str):
            source = spec
            target = spec
            ignored = []
        elif isinstance(spec, dict) and spec.get("source"):
            source = str(spec["source"])
            target = str(spec.get("target") or source)
            ignored = [key for key in ("uid", "gid", "mode") if key in spec]
        else:
            raise ComposeError(
                f"{service_name}: config entry must be a name or mapping with source"
            )
        definition = definitions.get(source)
        if definition is None:
            raise ComposeError(
                f"service {service_name!r} references undefined config {source!r}"
            )
        if target.startswith("/"):
            container_target = target
        else:
            if target in ("", ".", "..") or "/" in target or "\\" in target:
                raise ComposeError(
                    f"{service_name}: config {source!r} has invalid target {target!r}"
                )
            container_target = f"/{target}"
        if container_target in targets:
            raise ComposeError(
                f"{service_name}: multiple configs target {container_target!r}"
            )
        targets.add(container_target)
        mounts.append(
            ConfigMount(source=source, file=definition.file, target=container_target)
        )
        if ignored:
            warnings.append(
                f"config {source!r}: {', '.join(ignored)} cannot be enforced by "
                "wslc file mounts and will be ignored"
            )
    return mounts, warnings


_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)(ns|us|ms|s|m|h)")
_DURATION_FACTORS = {
    "ns": 0.000000001,
    "us": 0.000001,
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
}


def parse_duration(value: Any, what: str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    total = 0.0
    position = 0
    for match in _DURATION_RE.finditer(text):
        if match.start() != position:
            raise ComposeError(f"{what}: invalid duration {value!r}")
        total += float(match.group(1)) * _DURATION_FACTORS[match.group(2)]
        position = match.end()
    if position != len(text) or not text:
        raise ComposeError(f"{what}: invalid duration {value!r}")
    return total


def _parse_healthcheck(value: Any, service_name: str) -> Optional[Healthcheck]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ComposeError(f"{service_name}: healthcheck must be a mapping")
    if value.get("disable"):
        return None
    test = value.get("test")
    if isinstance(test, str):
        test = ["CMD-SHELL", test]
    elif isinstance(test, list):
        test = [str(part) for part in test]
    else:
        raise ComposeError(f"{service_name}: healthcheck.test must be a string or list")
    if not test or test[0] not in ("CMD", "CMD-SHELL", "NONE"):
        raise ComposeError(
            f"{service_name}: healthcheck.test must start with CMD, CMD-SHELL, or NONE"
        )
    if test[0] == "NONE":
        return None
    retries = int(value.get("retries", 3))
    if retries < 1:
        raise ComposeError(f"{service_name}: healthcheck.retries must be at least 1")
    return Healthcheck(
        test=test,
        interval=parse_duration(value.get("interval", "30s"), "healthcheck.interval"),
        timeout=parse_duration(value.get("timeout", "30s"), "healthcheck.timeout"),
        retries=retries,
        start_period=parse_duration(
            value.get("start_period", "0s"), "healthcheck.start_period"
        ),
    )


def _parse_depends_on(value) -> Tuple[List[str], Dict[str, Dependency]]:
    if value is None:
        return [], {}
    if isinstance(value, list):
        names = [str(v) for v in value]
        return names, {name: Dependency() for name in names}
    if isinstance(value, dict):
        deps = []
        conditions = {}
        for name, cfg in value.items():
            name = str(name)
            deps.append(name)
            condition = (cfg or {}).get("condition", "service_started")
            if condition not in (
                "service_started",
                "service_healthy",
                "service_completed_successfully",
            ):
                raise ComposeError(
                    f"depends_on condition {condition!r} on {name!r} is invalid"
                )
            conditions[name] = Dependency(
                condition=condition,
                required=bool((cfg or {}).get("required", True)),
            )
        return deps, conditions
    raise ComposeError("depends_on: expected list or mapping")


def _parse_build(value, project_dir: str, service_name: str) -> BuildConfig:
    if isinstance(value, str):
        return BuildConfig(context=_resolve_bind_source(value, project_dir))
    if "secrets" in value:
        raise ComposeError(
            f"{service_name}: build.secrets are not supported because "
            "'wslc build' has no --secret option; do not pass credentials as build args"
        )
    context = _resolve_bind_source(value.get("context", "."), project_dir)
    return BuildConfig(
        context=context,
        dockerfile=value.get("dockerfile"),
        args=_as_mapping(value.get("args"), "build.args"),
        target=value.get("target"),
        pull=bool(value.get("pull", False)),
    )


def load_project(
    compose_file: Sequence[str],
    project_name: Optional[str] = None,
    env_file: Optional[str] = None,
    strict_unsupported: bool = False,
) -> Project:
    compose_files = [compose_file] if isinstance(compose_file, str) else list(compose_file)
    if not compose_files:
        raise ComposeError("at least one compose file is required")
    compose_files = [os.path.abspath(path) for path in compose_files]
    project_dir = os.path.dirname(compose_files[0])

    dotenv_path = env_file or os.path.join(project_dir, ".env")
    env = dict(load_dotenv(dotenv_path))
    env.update(os.environ)  # process env wins

    raw: Dict[str, Any] = {}
    for path in compose_files:
        with open(path, encoding="utf-8") as fh:
            document = yaml.safe_load(fh)
        if document is None:
            document = {}
        if not isinstance(document, dict):
            raise ComposeError(f"{path}: expected a mapping at the document root")
        raw = merge_compose_models(raw, interpolate_tree(document, env))
    if not isinstance(raw, dict) or "services" not in raw:
        raise ComposeError(f"{compose_files[0]}: no 'services' section found")

    name = normalize_project_name(
        project_name
        or env.get("COMPOSE_PROJECT_NAME")
        or (str(raw["name"]) if raw.get("name") else "")
        or os.path.basename(project_dir)
    )
    project = Project(name=name, directory=project_dir)

    # --- top level networks & volumes -------------------------------------
    raw_networks = raw.get("networks") or {}
    for key, cfg in raw_networks.items():
        cfg = cfg or {}
        external = bool(cfg.get("external", False))
        net_name = cfg.get("name") or (key if external else f"{name}_{key}")
        project.networks[key] = Network(key=key, name=net_name, external=external)

    raw_volumes = raw.get("volumes") or {}
    for key, cfg in raw_volumes.items():
        cfg = cfg or {}
        external = bool(cfg.get("external", False))
        vol_name = cfg.get("name") or (key if external else f"{name}_{key}")
        project.volumes[key] = Volume(key=key, name=vol_name, external=external)

    project.secrets = _parse_secret_definitions(raw.get("secrets"), project_dir)
    project.configs = _parse_config_definitions(raw.get("configs"), project_dir, env)

    # --- services ----------------------------------------------------------
    for svc_name, cfg in (raw.get("services") or {}).items():
        if cfg is None:
            raise ComposeError(f"service {svc_name!r} is empty")
        svc = Service(name=str(svc_name))

        for key, message in UNSUPPORTED_CAPABILITIES.items():
            if key not in cfg:
                continue
            detail = f"{svc_name}: {message} (unsupported option '{key}')"
            if strict_unsupported:
                raise ComposeError(
                    f"{detail}; remove it or use --ignore-unsupported to accept "
                    "degraded behavior"
                )
            project.warnings.append(f"{detail} (ignoring)")

        svc.image = cfg.get("image")
        if "build" in cfg:
            svc.build = _parse_build(cfg["build"], project_dir, str(svc_name))
        if not svc.image and not svc.build:
            raise ComposeError(f"service {svc_name!r} needs 'image' or 'build'")

        svc.command = _as_command(cfg.get("command"))
        svc.entrypoint = _as_command(cfg.get("entrypoint"))
        svc.container_name = cfg.get("container_name")
        svc.environment = _as_environment(cfg.get("environment"))
        svc.env_files = [
            f if os.path.isabs(f) else os.path.join(project_dir, f)
            for f in _as_list(cfg.get("env_file"))
        ]
        for spec in cfg.get("ports") or []:
            svc.ports.extend(parse_port(spec))
        for spec in cfg.get("volumes") or []:
            svc.volumes.append(parse_volume(spec, project_dir))
        svc.secrets, secret_warnings = _parse_service_secrets(
            cfg.get("secrets"), project.secrets, str(svc_name)
        )
        svc.configs, config_warnings = _parse_service_configs(
            cfg.get("configs"), project.configs, str(svc_name)
        )
        project.warnings.extend(f"{svc_name}: {w}" for w in secret_warnings)
        project.warnings.extend(f"{svc_name}: {w}" for w in config_warnings)
        occupied_targets = {mount.target for mount in svc.volumes}
        for secret in svc.secrets:
            if secret.target in occupied_targets:
                raise ComposeError(
                    f"{svc_name}: secret target {secret.target!r} conflicts with a volume"
                )
            occupied_targets.add(secret.target)
        for config in svc.configs:
            if config.target in occupied_targets:
                raise ComposeError(
                    f"{svc_name}: config target {config.target!r} conflicts with "
                    "another mount"
                )
            occupied_targets.add(config.target)
        svc.tmpfs = _as_list(cfg.get("tmpfs"))

        # networks: list or mapping (with aliases); default network otherwise
        raw_svc_networks = cfg.get("networks")
        if raw_svc_networks is None:
            keys = ["default"]
            aliases: Dict[str, List[str]] = {}
        elif isinstance(raw_svc_networks, list):
            keys = [str(k) for k in raw_svc_networks]
            aliases = {}
        else:
            keys = [str(k) for k in raw_svc_networks]
            aliases = {
                str(k): _as_list((v or {}).get("aliases"))
                for k, v in raw_svc_networks.items()
            }
        for key in keys:
            if key not in project.networks:
                if key == "default":
                    project.networks["default"] = Network(
                        key="default", name=f"{name}_default", external=False
                    )
                else:
                    raise ComposeError(
                        f"service {svc_name!r} references undefined network {key!r}"
                    )
            net = project.networks[key]
            svc.networks.append(net.name)
            svc.network_aliases[net.name] = aliases.get(key, [])

        # named volumes must be declared
        for mount in svc.volumes:
            if mount.type == "volume":
                if mount.source not in project.volumes:
                    raise ComposeError(
                        f"service {svc_name!r} references undefined volume {mount.source!r}"
                    )
                mount.source = project.volumes[mount.source].name

        svc.depends_on, svc.dependencies = _parse_depends_on(cfg.get("depends_on"))
        svc.healthcheck = _parse_healthcheck(cfg.get("healthcheck"), str(svc_name))

        svc.hostname = cfg.get("hostname")
        svc.domainname = cfg.get("domainname")
        svc.dns = _as_list(cfg.get("dns"))
        svc.dns_search = _as_list(cfg.get("dns_search"))
        svc.dns_opt = _as_list(cfg.get("dns_opt"))
        svc.user = str(cfg["user"]) if cfg.get("user") is not None else None
        svc.working_dir = cfg.get("working_dir")
        svc.labels = _as_mapping(cfg.get("labels"), "labels")
        svc.stop_signal = cfg.get("stop_signal")
        svc.shm_size = str(cfg["shm_size"]) if cfg.get("shm_size") is not None else None
        svc.stdin_open = bool(cfg.get("stdin_open", False))
        svc.tty = bool(cfg.get("tty", False))
        svc.profiles = _as_list(cfg.get("profiles"))
        svc.restart = cfg.get("restart")
        if svc.restart and svc.restart not in ("no", '"no"'):
            detail = (
                f"{svc_name}: restart policies are not supported by wslc "
                f"(unsupported option 'restart: {svc.restart}')"
            )
            if strict_unsupported:
                raise ComposeError(
                    f"{detail}; remove it or use --ignore-unsupported to accept "
                    "degraded behavior"
                )
            project.warnings.append(f"{detail} (ignoring)")

        raw_ulimits = cfg.get("ulimits")
        if isinstance(raw_ulimits, dict):
            for item, limit in raw_ulimits.items():
                if isinstance(limit, dict):
                    svc.ulimits.append(f"{item}={limit.get('soft', -1)}:{limit.get('hard', -1)}")
                else:
                    svc.ulimits.append(f"{item}={limit}")

        # resource limits: v2 style and deploy.resources.limits
        svc.mem_limit = cfg.get("mem_limit")
        svc.cpus = str(cfg["cpus"]) if cfg.get("cpus") is not None else None
        deploy = cfg.get("deploy") or {}
        limits = ((deploy.get("resources") or {}).get("limits")) or {}
        svc.mem_limit = limits.get("memory", svc.mem_limit)
        if limits.get("cpus") is not None:
            svc.cpus = str(limits["cpus"])
        if deploy.get("replicas"):
            svc.replicas = int(deploy["replicas"])
        reservations = (deploy.get("resources") or {}).get("reservations") or {}
        for device in reservations.get("devices") or []:
            if "gpu" in (device.get("capabilities") or []):
                svc.gpus = "all" if device.get("count", "all") == "all" else str(device["count"])

        if cfg.get("gpus"):  # shorthand
            svc.gpus = str(cfg["gpus"])

        project.services[svc.name] = svc

    for service in project.services.values():
        for dependency_name, dependency in service.dependencies.items():
            dependency_service = project.services.get(dependency_name)
            if dependency_service is None:
                raise ComposeError(
                    f"service {service.name!r} depends on undefined service "
                    f"{dependency_name!r}"
                )
            if (
                dependency.condition == "service_healthy"
                and dependency_service.healthcheck is None
            ):
                raise ComposeError(
                    f"service {service.name!r} requires {dependency_name!r} to be healthy, "
                    "but it has no healthcheck"
                )
    return project
