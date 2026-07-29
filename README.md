# wslc-compose

[![CI](https://github.com/yovannyr/wslc-compose/actions/workflows/ci.yml/badge.svg)](https://github.com/yovannyr/wslc-compose/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)

**docker-compose style orchestration for [WSL containers (wslc)](https://learn.microsoft.com/windows/wsl/wsl-container).**

Microsoft's new `wslc` CLI (the *WSL container* public preview, June 2026) manages single
containers much like `docker run`, but it has **no Compose support yet** — the WSL team
tracks the feature request in [microsoft/WSL#40948](https://github.com/microsoft/WSL/issues/40948)
and a Docker-compatible API endpoint in [microsoft/WSL#40976](https://github.com/microsoft/WSL/issues/40976).

`wslc-compose` fills that gap **today**: point it at the `docker-compose.yml` /
`compose.yaml` you already use with Docker or Podman, and it drives `wslc` for you —
networks with DNS, named volumes, bind mounts, dependency ordering, project-scoped
naming, config-drift detection and scaling included. It also supports Compose model
merging, `include`/`extends`, startup health checks, file-backed secrets and configs,
pull policies, anonymous volumes, and one-off service commands.

---

## Table of contents

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Migrating an existing wslc setup](#migrating-an-existing-wslc-setup)
- [Command reference](#command-reference)
- [Compose file support](#compose-file-support)
- [Compose model composition](#compose-model-composition)
- [Variable interpolation](#variable-interpolation)
- [Readiness and dependency conditions](#readiness-and-dependency-conditions)
- [Image policies and one-off jobs](#image-policies-and-one-off-jobs)
- [Configs](#configs)
- [Secrets](#secrets)
- [Naming conventions and labels](#naming-conventions-and-labels)
- [How `up` decides what to do](#how-up-decides-what-to-do)
- [Networking](#networking)
- [Volumes and path translation](#volumes-and-path-translation)
- [Known wslc preview limitations](#known-wslc-preview-limitations)
- [Troubleshooting](#troubleshooting)
- [Examples](#examples)
- [Project layout](#project-layout)
- [Development](#development)
- [License](#license)

---

## How it works

`wslc-compose` is a thin, dependency-light Python CLI (only PyYAML) that:

1. **Loads** your compose file (`compose.yaml`, `compose.yml`, `docker-compose.yml` or
   `docker-compose.yaml`, searched in the current directory and its parents), applies
   `.env` + environment variable interpolation, and normalizes everything into an
   internal model.
2. **Plans** the work: which networks/volumes must exist, which images must be built or
   pulled, in which order services must start (`depends_on` topological sort), and which
   existing containers are stale (config-hash comparison).
3. **Executes** plain `wslc` commands (`run`, `build`, `network create`, `volume create`,
   `stop`, `remove`, `logs`, ...). There is no daemon, no state file, no magic — run any
   step with `--dry-run` to see the exact `wslc` invocations and paste them in a terminal
   yourself if you want.

Because state lives entirely in wslc (via container labels), `wslc-compose` and the raw
`wslc` CLI can be mixed freely.

Two executables are installed:

| Command | Purpose |
|---|---|
| `wslc-compose` | the standalone compose CLI |
| `wslc` | a wrapper giving the docker/podman UX: `wslc compose up -d`; **any other subcommand is forwarded verbatim to the real wslc CLI** (`wslc images`, `wslc attach`, ...). A recursion guard makes sure the wrapper never invokes itself. |

## Requirements

- **Windows 11** with the **WSL container preview** installed — `wslc` must work in a
  terminal. See the [official documentation](https://learn.microsoft.com/windows/wsl/wsl-container).
- **Python ≥ 3.9**, inside a WSL distro or on Windows.
  (No pip/venv on your distro? The installer below handles that.)

## Installation

### One-liner (recommended)

Works even on WSL distros without `pip`, `venv` or `pipx` — it bootstraps a standalone
[uv](https://docs.astral.sh/uv/) if no Python package manager is found:

```console
curl -fsSL https://raw.githubusercontent.com/yovannyr/wslc-compose/main/install.sh | sh
```

Commands are installed into `~/.local/bin` (make sure it is on your `PATH`).

### With an existing package manager

```console
pipx install git+https://github.com/yovannyr/wslc-compose
# or
uv tool install --from git+https://github.com/yovannyr/wslc-compose wslc-compose
# or
pip install --user git+https://github.com/yovannyr/wslc-compose
```

### From a local checkout

```console
git clone https://github.com/yovannyr/wslc-compose
cd wslc-compose
WSLC_COMPOSE_SOURCE=$PWD sh install.sh     # or: pip install -e .
```

### Locating the wslc binary

`wslc-compose` finds the wslc CLI automatically, in this order:

1. `$WSLC_COMPOSE_BIN` (explicit override)
2. `wslc.exe` / `wslc` on `PATH` (Windows interop makes `wslc.exe` visible inside WSL)
3. `C:\Program Files\WSL\wslc.exe` (default install location)

## Quick start

```console
$ cd my-project              # contains compose.yaml
$ wslc compose up -d
Network my-project_default created
Volume my-project_data created
Creating my-project-db-1 ...
Creating my-project-web-1 ...

$ wslc compose ps
NAME               SERVICE  IMAGE         STATUS   PORTS
my-project-db-1    db       postgres:16   running
my-project-web-1   web      nginx:alpine  running  0.0.0.0:8080->80/tcp

$ wslc compose logs -f web
$ wslc compose exec web sh
$ wslc compose down -v

$ wslc images                # ← not a compose command: forwarded to wslc.exe verbatim
```

`wslc-compose up -d`, `wslc-compose ps`, ... work identically if you prefer the
standalone command.

## Migrating an existing wslc setup

Already running containers with raw `wslc run` commands or a home-grown script?
**[docs/MIGRATION.md](docs/MIGRATION.md)** walks through the whole move, end to end:
inventorying what runs, reusing already-built images (retag instead of rebuild),
retiring the old containers without breaking published ports, verifying, and rolling
back — plus a recovery cheat sheet for the preview's sharp edges (per-elevation
sessions, mount budget, wedged sessions).

## Command reference

### Global options

Apply to every subcommand and go **before** it: `wslc-compose -f other.yml up -d`.

| Option | Description |
|---|---|
| `-f, --file FILE` | compose file to use; repeat for specification-aware overrides |
| `-p, --project-name NAME` | project name (default: `name:` key, else directory name) |
| `--env-file FILE` | alternate `.env` file for interpolation |
| `--profile NAME` | enable a compose profile (repeatable) |
| `--dry-run` | print every `wslc` command instead of executing it |
| `--ignore-unsupported` | warn instead of failing when wslc cannot honor an option |
| `--version` | show the wslc-compose version |

### `up [SERVICE...]`

Create networks and volumes, build missing images, then create/start containers in
`depends_on` order. Idempotent: up-to-date running containers are left alone.

| Option | Description |
|---|---|
| `-d, --detach` | do not attach to logs after starting |
| `--build` | rebuild images of services that have a `build:` section |
| `--no-build` | never build missing service images |
| `--pull POLICY` | override image pull policy: `always`, `missing`, or `never` |
| `--wait` | wait for running/healthy services before returning |
| `--wait-timeout SEC` | global readiness timeout (default 60) |
| `--force-recreate` | recreate containers even if their configuration is unchanged |
| `--no-recreate` | never recreate existing containers, even when configuration changed |
| `--always-recreate-deps` | recreate dependencies of explicitly selected services |
| `--renew-anon-volumes` | recreate generated anonymous volumes with their containers |
| `--scale SERVICE=N` | override the number of replicas (repeatable) |
| `--remove-orphans` | remove project containers whose service is no longer declared |
| `--no-start` | create containers with native `wslc create` without starting them |
| `--abort-on-container-exit` | stop the remaining services when any container exits |
| `--abort-on-container-failure` | stop the remaining services after the first non-zero exit |
| `--exit-code-from SERVICE` | return this service's exit code and imply abort-on-exit |
| `-t, --timeout SEC` | stop timeout when recreating (default 10) |

Without `-d`, logs of the started services are followed after startup; `Ctrl+C`
detaches **without stopping the containers**.

### `create [SERVICE...]`

Creates containers with the native `wslc create` command but does not start them.
It supports the build, pull, scale, recreate, anonymous-volume, and orphan options
from `up`. `up --no-start` is equivalent.

For test and migration stacks, the `up` job-exit options monitor container state and
return the relevant workload exit code. When an exit condition triggers, remaining
containers are stopped in reverse dependency order and their `pre_stop` hooks are
attempted. These foreground-only options cannot be combined with `--detach`.

### `down`

Stop and remove all of the project's containers, then remove its non-external networks.

| Option | Description |
|---|---|
| `-v, --volumes` | also remove non-external named and generated anonymous volumes |
| `--remove-orphans` | also remove containers for services no longer in the model |
| `-t, --timeout SEC` | stop timeout (default 10) |

### `ps [SERVICE...]`

List the project's containers (name, service, image, status, ports).
`-q` prints container IDs only.

### `logs [SERVICE...]`

Show container logs, multiplexed and prefixed per container
(`my-project-web-1 | ...`).

| Option | Description |
|---|---|
| `-f, --follow` | follow log output |
| `-n, --tail N` | show only the last N lines |
| `-t, --timestamps` | show timestamps |
| `--since VALUE` | show logs newer than a timestamp or relative duration |
| `--until VALUE` | show logs older than a timestamp or relative duration |

### `exec SERVICE CMD [ARG...]`

Run a command in a running container of SERVICE.

| Option | Description |
|---|---|
| `--index N` | pick the Nth replica (default 1) |
| `-u, --user USER` | run as user |
| `-w, --workdir DIR` | working directory |
| `-e, --env K=V` | extra environment variables (repeatable) |
| `-T, --no-tty` | disable TTY allocation (for scripts/pipes) |

A TTY is allocated automatically when stdin is a terminal.

### `run [OPTIONS] SERVICE [COMMAND...]`

Run a one-off service command. Dependencies start automatically unless `--no-deps`
is used. The one-off container receives the service environment, mounts, secrets,
configs, network, user, working directory, and resource limits.

| Option | Description |
|---|---|
| `--rm` | remove the one-off container after it exits |
| `--name NAME` | choose an explicit container name |
| `--no-deps` | do not start service dependencies |
| `--service-ports` | publish the service's declared ports |
| `-e, --env K=V` | add or override environment variables (repeatable) |
| `--entrypoint COMMAND` | override the image/service entrypoint |
| `-d, --detach` | run the one-off container in the background |
| `-T, --no-tty` | disable the service's TTY setting |
| `--pull POLICY` | override `pull_policy` |
| `--build` / `--no-build` | force or prohibit image builds |

### Utility commands

- `wait [SERVICE...]` waits for containers and returns their highest exit code.
- `kill [-s SIGNAL] [SERVICE...]` force-stops containers in reverse dependency order.
- `rm [--stop] [SERVICE...]` removes service containers.
- `images [SERVICE...]` reports required images and local availability.
- `push [SERVICE...]` pushes explicitly named service images.
- `port SERVICE PRIVATE_PORT` prints the published host binding.

### `start / stop / restart [SERVICE...]`

Lifecycle of existing project containers, without recreating them.
`restart` is emulated as stop + start (wslc has no native restart).

### `pull [SERVICE...]`

Pull the images of services that have an `image:` key.

### `build [--no-cache] [SERVICE...]`

Build every selected service that has a `build:` section, tagging the result
`<project>-<service>` (unless `image:` names it explicitly).

### `config`

Print the fully resolved configuration (after interpolation, normalization, name
prefixing) as YAML — useful to debug what wslc-compose actually sees.

`config --capabilities` does not require a Compose file. It prints the options the
current wslc runtime cannot honor and hard runtime boundaries such as one network per
container, missing persistent health monitoring, restart policies, external configs,
and build secrets.

### `version`

Show the wslc-compose and wslc versions.

## Compose file support

| Compose key | Mapping to wslc |
|---|---|
| `image` | `wslc run <image>` / `wslc pull` |
| `build` (`context`, `dockerfile`, `args`, `target`, `pull`) | `wslc build -t <project>-<service>` |
| `command`, `entrypoint` (string or list) | trailing args / `--entrypoint` |
| `container_name` | `--name` (disables scaling for that service) |
| `environment` (list & map), `env_file` | `-e`, `--env-file` |
| `ports` (short & long syntax, `ip:host:container`, `/udp`, ranges `8000-8005`) | `-p` |
| `volumes` — named volumes | `wslc volume create` + `-v name:/path` |
| `secrets` (file source; short & long service syntax) | read-only file mount at `/run/secrets/<name>` or `target` |
| `configs` (`file`, `content`, `environment`) | materialized read-only file mount |
| anonymous volumes | generated project/service/replica-scoped wslc volumes |
| `volumes` — bind mounts (`./rel`, `/abs`, `~`, `E:\win\path`), `:ro` | `-v` with [path translation](#volumes-and-path-translation) |
| `tmpfs` (top-level list or `type: tmpfs`) | `--tmpfs` |
| `networks` incl. aliases, external/name, driver/options/labels | `wslc network create`, `--network`, `--network-alias` |
| named `volumes` incl. external/name, driver/options/labels | `wslc volume create`, `-v` |
| `depends_on` conditions | start order plus `service_healthy` / `service_completed_successfully` waits |
| `healthcheck` | startup/readiness checks executed with `wslc exec` |
| `deploy.replicas` | number of containers (see also `--scale`) |
| `deploy.resources.limits.cpus` / `.memory`, `cpus`, `mem_limit` | `--cpus`, `-m` |
| `deploy.resources.reservations.devices` (gpu), `gpus` | `--gpus` |
| `post_start`, `pre_stop` | ordered lifecycle commands executed with `wslc exec` |
| `shm_size`, `ulimits`, `stop_signal`, `stop_grace_period` | wslc runtime and stop flags |
| `hostname`, `domainname`, `dns`, `dns_search`, `dns_opt` | `-h`, `--domainname`, `--dns*` |
| `user`, `working_dir` | `-u`, `-w` |
| `labels` (list & map) | `-l` (merged with the tracking labels below) |
| `profiles` | service skipped unless its profile is enabled or it is named explicitly |
| `stdin_open`, `tty` | `-i`, `-t` |
| `name` (top level) | default project name |
| `pull_policy` | image pull/build decision during `up` and `run` |
| repeated `-f` files | specification-aware model merge and resource uniqueness |
| `include`, `extends` | modular and inherited Compose service models |

Keys that wslc cannot honor are rejected by default so security or runtime semantics
are never silently weakened. `--ignore-unsupported` restores warning-only compatibility:
`restart`, `privileged`, `cap_add`/`cap_drop`, `devices`, `extra_hosts`, `sysctls`, `init`,
`pid`, `ipc`, `read_only`, `security_opt`, `logging`.

Always rejected: references to undeclared networks/volumes, circular `depends_on`,
scaling a service that sets `container_name`, external configs, unsupported secret
sources, and build-time secrets. `config --capabilities` prints the current runtime
capability report without requiring a Compose file.

## Compose model composition

### Multiple `-f` files

Repeat `-f` to apply development, CI, or machine-specific overrides:

```powershell
wslc-compose `
  -f compose.yaml `
  -f compose.development.yaml `
  config
```

Mappings are merged recursively. Scalar values are replaced by the later file;
`command`, `entrypoint`, and `healthcheck.test` are replaced rather than appended.
Ports, volumes, secrets, and configs use their Compose uniqueness keys, so an
override of the same container target replaces the original resource. Other lists
are appended. Relative paths in an `-f` stack are resolved from the first file's
project directory.

Compose's YAML merge tags are supported. `!reset` clears an inherited value before
normalization, while `!override` replaces it without applying normal merge rules:

```yaml
services:
  api:
    ports: !override ["9090:90"]
    environment: !reset null
```


### `include`

Included Compose applications contribute services and top-level resources:

```yaml
include:
  - ./database/compose.yaml
  - path: ./workers/compose.yaml
    project_directory: ./workers/runtime
    env_file: ./workers.env

services:
  api:
    image: example/api
```

Short syntax resolves relative paths from the included file's directory. Long syntax
can set a separate `project_directory`, an include-specific `env_file`, or a list of
paths. Recursive includes are supported; include cycles fail with an explicit error.

### `extends`

A service can inherit another service from the same file:

```yaml
services:
  app-base:
    image: example/app
    environment:
      LOG_LEVEL: information

  app:
    extends: app-base
    environment:
      LOG_LEVEL: debug
```

External Compose files use long syntax:

```yaml
services:
  app:
    extends:
      file: ./common-services.yaml
      service: app-base
```

The child uses the same Compose merge rules as repeated `-f` files. Referenced
networks, volumes, secrets, and configs must still exist in the resulting project.
Circular inheritance is rejected.

## Variable interpolation

Identical to docker compose:

| Syntax | Behavior |
|---|---|
| `$VAR`, `${VAR}` | value, empty if unset |
| `${VAR:-default}` | default if unset **or empty** |
| `${VAR-default}` | default only if unset |
| `${VAR:?message}` / `${VAR?message}` | abort with message if missing |
| `${VAR:+alt}` / `${VAR+alt}` | alt if set |
| `$$` | literal `$` (e.g. `$$(cmd)` reaches the container shell as `$(cmd)`) |

Precedence: process environment > `.env` file (in the project directory, or
`--env-file`). Shell constructs like `$(date)` are left untouched.

Service `env_file` supports both short syntax and Compose long syntax:

```yaml
services:
  api:
    env_file:
      - path: ./defaults.env
        required: false
      - path: ./credentials.env
        format: raw
```

Missing optional files are skipped; missing required files fail during model loading.
`format: raw` preserves `$` and quote characters without Compose-side interpretation.

## Readiness and dependency conditions

Healthchecks are executed through `wslc exec` while `up` is orchestrating the
project. Both exec and shell forms are supported, together with Compose durations:

```yaml
services:
  postgres:
    image: postgres:17
    healthcheck:
      test: [CMD-SHELL, "pg_isready -U postgres"]
      interval: 2s
      timeout: 1s
      retries: 20
      start_period: 5s

  migrate:
    image: example/migrator
    depends_on:
      postgres:
        condition: service_healthy

  api:
    image: example/api
    depends_on:
      migrate:
        condition: service_completed_successfully
      postgres:
        condition: service_healthy
        required: true
```

Supported conditions:

- `service_started`: dependency containers have been started; this is the default.
- `service_healthy`: every dependency replica must pass its healthcheck.
- `service_completed_successfully`: every dependency replica must stop with exit code 0.
- `required: false`: report an optional dependency failure as a warning and continue.

Use `up --wait --wait-timeout 120` to wait for all selected services with a declared
healthcheck. This is startup/readiness orchestration only: wslc has no daemon that can
continue evaluating health or restart unhealthy containers after wslc-compose exits.

## Image policies and one-off jobs

`pull_policy` controls image preparation during `up` and `run`:

| Policy | Behavior |
|---|---|
| `always` | pull an explicitly named image before starting |
| `missing` / `if_not_present` | reuse local images; refresh `latest`; otherwise pull or build |
| `never` | fail if the named image is absent locally |
| `build` | build the service image even when it already exists |

Command-line `--pull always|missing|never` overrides the service policy. `--build`
forces builds, while `--no-build` prevents accidental source builds and fails when a
required build-only image is missing.

One-off jobs reuse the normalized service model. This is useful for migrations,
tests, maintenance, or runtime NuGet restore operations:

```powershell
wslc-compose run --rm migrate dotnet ef database update

wslc-compose run `
  --rm `
  --no-deps `
  restore `
  dotnet restore --configfile /run/secrets/nuget_config
```

Service ports are not published by default for one-off containers; opt in with
`--service-ports`. Anonymous volumes receive a unique suffix so concurrent one-off
jobs do not share their per-container data.

## Configs

Compose configs are exposed as read-only files without rebuilding the image. File,
inline content, and environment-variable sources are supported:

```yaml
services:
  api:
    image: example/api
    configs:
      - app_settings
      - source: feature_flags
        target: /app/config/features.json

configs:
  app_settings:
    file: ./appsettings.Production.json

  feature_flags:
    content: |
      {
        "newCheckout": ${NEW_CHECKOUT:-false}
      }

  simple_value:
    environment: SIMPLE_CONFIG_VALUE
```

Short syntax mounts to `/<config-name>`. Long syntax accepts an absolute `target` or
a filename under `/`. `content` and `environment` values are materialized into stable
host-side files and mounted read-only; only the path enters the service model and
config hash.

Current boundaries:

- `external: true` is rejected because wslc has no config object store.
- `uid`, `gid`, and `mode` are accepted with a warning because wslc file mounts cannot
  enforce them.
- Configs, secrets, and ordinary bind/volume mounts all consume the same limited wslc
  session mount budget.
- A config target cannot overlap another config, secret, or volume target.

## Naming conventions and labels

Everything is scoped by project so multiple projects coexist cleanly:

| Object | Name |
|---|---|
| container | `<project>-<service>-<n>` (or `container_name:`) |
| network | `<project>_<network-key>` (external networks keep their name) |
| volume | `<project>_<volume-key>` (external volumes keep their name) |
| built image | `<project>-<service>` (unless `image:` is set) |

Each container gets tracking labels, which is how commands find project containers
(`wslc list -f label=com.wslc-compose.project=<name>`):

```
com.wslc-compose.project           project name
com.wslc-compose.service           service name
com.wslc-compose.container-number  replica index (1..N)
com.wslc-compose.config-hash       hash of the resolved service config
```

## How `up` decides what to do

For every desired container, `up` compares the stored `config-hash` label against the
hash of the freshly resolved service configuration:

| Existing container | Action |
|---|---|
| running, hash matches | *"is up-to-date"* — untouched |
| stopped, hash matches | started |
| hash differs, or `--force-recreate` | stopped, removed, recreated |
| missing | created |
| replica index above the requested scale | removed |

Changing anything in the service definition (image, env, ports, mounts, ...) or in
interpolated variables therefore triggers a clean recreation of just the affected
services on the next `up`.

Before container reconciliation, each service image is prepared according to
`pull_policy`, `--pull`, `--build`, and `--no-build`. Services are then processed in
dependency order. Readiness conditions are evaluated before dependents start; `down`,
`stop`, and `restart` use reverse dependency order and honor `stop_grace_period`.

Compose lifecycle hooks are also honored. `post_start` runs after a container is
created or started, while `pre_stop` runs immediately before a running container is
stopped by `stop`, `restart`, or `down`. Hook `command`, `user`, `working_dir`, and
`environment` are supported. Hook failures abort the operation; `privileged: true`
is rejected because wslc cannot execute privileged commands.

`up` and `down` warn when labeled project containers refer to services that are no
longer declared. They are preserved unless `--remove-orphans` is supplied. If `up`
fails partway through reconciliation, container instances created or recreated by
that invocation are removed in reverse creation order; untouched pre-existing
containers are never included in this rollback set.

## Networking

- Services without a `networks:` key join the project's `default` network
  (`<project>_default`), created on demand.
- On user-defined wslc networks, containers resolve each other by **container name**
  and by **network alias** — `wslc-compose` always adds the service name as an alias,
  so `db:5432` style URLs from your Docker compose files work unchanged. (Verified
  against the preview: alias and name DNS both resolve.)
- `external: true` networks are required to exist and are never created/removed.
- Non-external network and volume definitions forward `driver`, `driver_opts`, and
  `labels` to native `wslc network create` / `wslc volume create`. Metadata changes
  do not recreate resources that already exist.
- ⚠️ wslc accepts a **single `--network` per container**. If a service lists several
  networks, the first one is used (documented preview limitation).

## Volumes and path translation

- **Named volumes** are created on demand (`wslc volume create`) and removed by
  `down -v` (external ones never).
- **Anonymous volumes** such as `- /data` are emulated with stable generated names
  derived from project, service, and target. Each replica gets its own suffixed wslc
  volume; one-off `run` containers receive a unique suffix. `down -v` removes only
  generated volumes matching the project's anonymous-volume prefixes.
- **Bind mounts**: relative paths are resolved against the compose file's directory,
  `~` is expanded. wslc expects **Windows host paths**, so when running inside WSL,
  Linux paths are translated automatically with `wslpath -w`
  (`/mnt/e/proj/html` → `E:\proj\html`), with a manual `/mnt/<drive>/...` fallback.
  Paths already in Windows form (`E:\...`, `\\server\...`) are passed through as-is.
- Paths living **inside a distro filesystem** translate to `\\wsl.localhost\<distro>\...`;
  whether wslc can mount those depends on the preview build — prefer paths under a
  drive (`/mnt/c`, `/mnt/e`, ...).
- `tmpfs` mounts map to `--tmpfs`.

## Secrets

File-backed runtime secrets use standard Compose syntax. Each secret is exposed only
to services that explicitly request it and is mounted read-only. Short syntax mounts
to `/run/secrets/<name>`:

```yaml
services:
  restore:
    image: mcr.microsoft.com/dotnet/sdk:10.0
    working_dir: /src
    volumes:
      - .:/src
    secrets:
      - nuget_config
    command:
      - dotnet
      - restore
      - --configfile
      - /run/secrets/nuget_config

secrets:
  nuget_config:
    file: C:/Users/me/.nuget/private.NuGet.Config
```

Long syntax can choose a filename under `/run/secrets` or an absolute container path:

```yaml
services:
  restore:
    image: mcr.microsoft.com/dotnet/sdk:10.0
    secrets:
      - source: nuget_config
        target: /root/.nuget/NuGet.Config

secrets:
  nuget_config:
    file: ./private.NuGet.Config
```

Keep secret files outside source control and restrict their host permissions. Secret
contents are never copied into the normalized model, labels, config hash, or generated
command line; only the host path is passed to `wslc -v`.

Current boundaries:

- `file:` sources are supported; `environment:` and `external:` sources fail clearly.
- `uid`, `gid`, and `mode` are accepted with a warning because wslc bind mounts cannot
  enforce them.
- `build.secrets` fails clearly. The current `wslc build` has no `--secret` option, so
  a Dockerfile `RUN dotnet restore` cannot receive credentials safely. Do not replace
  build secrets with `build.args`; use a runtime restore container or a builder with
  native BuildKit secret support.
- Every secret is a mount and counts toward the current wslc session mount limit.

## Known wslc preview limitations

`wslc` is a public preview. `wslc-compose` rejects unsupported runtime and security
semantics by default. Use `--ignore-unsupported` only when degraded, warning-only
behavior is intentional:

- **`restart:` policies** — no wslc equivalent yet; restart after a reboot is manual:
  `wslc compose up -d`.
- **Healthchecks are orchestrator-scoped**: `up --wait` and dependency conditions
  execute checks while wslc-compose is running. There is no persistent daemon health
  state or automatic unhealthy-container restart after the command exits.
- **One network per container** (see [Networking](#networking)).
- **Session mount limit**: the current preview caps mounted volumes at ~15 per WSL
  session — error `Too many volumes have been mounted (limit: 15)` / `0x8007000e`.
  This also affects `wslc build` (the build context is mounted). Microsoft says it
  will be fixed; meanwhile `wsl --shutdown` resets the session (⚠️ stops **all** WSL
  containers and distros).
- **Sessions are per Windows user *and per elevation***: an elevated terminal and a
  normal one talk to two different wslc sessions with separate containers, images,
  networks and volumes — invisible to each other, but competing for the same
  `127.0.0.1` published ports. Pick one elevation and stick to it (details and
  recovery steps in [docs/MIGRATION.md](docs/MIGRATION.md)).
- **Sessions live in the Windows service and survive `wsl --shutdown`.** A wedged
  session (every wslc command hangs) can only be cleared by restarting the service
  from an admin PowerShell: `Restart-Service WslService -Force`. If that restart
  itself hangs (service stuck in `StopPending`), see the force-kill recovery in
  [Troubleshooting](#troubleshooting).
- **Avoid `wslc system session terminate` while containers run** — in the current
  preview it can deadlock the whole wslc service (see above for the recovery).
- **Published ports bind the Windows loopback**, not the distro's: test them from
  the Windows side (browser, `powershell.exe Invoke-WebRequest`), not with a `curl
  localhost` inside WSL.
- `privileged`, `cap_add`, `devices`, `sysctls`, `extra_hosts`, and logging drivers
  are rejected unless `--ignore-unsupported` is explicitly used.

When wslc gains native Compose support ([#40948](https://github.com/microsoft/WSL/issues/40948))
or a Docker Engine API endpoint ([#40976](https://github.com/microsoft/WSL/issues/40976)),
migrating away from this tool is trivial — your compose files never stopped being
standard compose files.

## Troubleshooting

**`wslc CLI not found`** — install the WSL container preview, or point
`WSLC_COMPOSE_BIN` at the binary (e.g. `/mnt/c/Program Files/WSL/wslc.exe`).

**`Too many volumes have been mounted (limit: 15)` / error `0x8007000e`** — preview
session limit (see above). Restart WSL (`wsl --shutdown` from Windows) — this stops
all running WSL containers — then `wslc compose up -d` again.

**A bind-mounted directory appears empty in the container** — the path probably
reached wslc as a Linux path. Check the exact flags with `--dry-run`; sources should
show as `E:\...` style Windows paths. Paths inside the distro filesystem
(`\\wsl.localhost\...`) may not be mountable by the preview.

**`port is already allocated` style errors** — another container (maybe from a raw
`wslc run`) publishes the same host port; `wslc list -a` shows everything, not just
this project. If `wslc list -a` shows *nothing* on that port, the culprit likely
lives in the other elevation's session (see below).

**Containers/images/networks suddenly "gone"** — you probably switched between an
elevated and a normal terminal: each elevation has its own wslc session with its own
objects. `wslc system session list` shows them; go back to the terminal elevation
that created your containers.

**Every wslc command hangs, even `wslc list`** — the session's Windows-side relay is
deadlocked (known preview issue, e.g. after a `system session terminate` with running
containers). `wsl --shutdown` does **not** fix this — sessions survive it. From an
**admin** PowerShell: `Restart-Service WslService -Force` (older builds:
`Restart-Service LxssManager -Force`); this restarts all of WSL.

**`Restart-Service WslService -Force` hangs / the service is stuck in `StopPending`** —
the `-Force` restart asks the service to stop, but a wedged utility VM (a `vmmem`
process that refuses to die) keeps it from stopping, so it sits in `StopPending`
forever and every `wsl.exe` call stays suspended. Don't wait on it — kill the old
service process directly; because `WslService` is set to *Automatic*, Windows
restarts it cleanly with a fresh PID. From an **admin** PowerShell:

```powershell
# 1) confirm the state and grab the stuck PID
Get-Service WslService | Select-Object Status          # -> StopPending
$svcpid = (Get-CimInstance Win32_Service -Filter "Name='WslService'").ProcessId

# 2) force-kill the wedged service process; it auto-restarts (Automatic start)
taskkill /F /PID $svcpid

# 3) verify it came back, then start clean
Get-Service WslService | Select-Object Status          # -> Running (new PID)
wsl.exe --shutdown                                     # clear leftover sessions
```

A leftover `vmmemwslc-*` process may survive even `wsl --shutdown` (it is a protected
Hyper-V worker, not killable with `taskkill`, even as admin). It is inert and no
longer blocks the service — only a full Windows reboot clears it. Prefer killing the
stuck service PID (above) over rebooting.

> Tip: run WSL commands over SSH from another machine, or from a Windows terminal
> that is **not** itself inside WSL — a `wsl --shutdown` (or the kill above) then
> can't cut your own session out from under you.

**`ERROR_SHARING_VIOLATION` on every session command, from every terminal** —
`wslc list`/`run` fail with *"the process cannot access the file because it is
being used by another process"* everywhere, while `wslc --version` still answers:
the session store is locked machine-wide. We've hit this after many concurrent
wslc invocations (the preview tolerates only one at a time — serialize yours).
Recovery: `wsl --shutdown`; if it persists, the `Restart-Service` procedure above.

**A published port works in the browser but not with `curl localhost` inside WSL** —
expected: wslc publishes on the *Windows* loopback, which the distro's loopback
doesn't see. Test from the Windows side.

**`wslc-compose: command not found` inside a script run from PowerShell** —
`bash script.sh` from PowerShell starts a non-login shell without `~/.local/bin` on
PATH. Add `export PATH="$HOME/.local/bin:$PATH"` at the top of the script.

**What is it actually running?** — add `--dry-run` to any command to see every
`wslc` invocation verbatim.

## Examples

Both examples live in this repository and double as integration tests:

- [`examples/demo`](examples/demo) — the full tour: nginx serving a **bind-mounted**
  page on port 8088, an alpine worker writing to a **shared named volume**, redis
  reachable through a **network alias** (`cache`), `.env` **interpolation**,
  `depends_on` ordering:

  ```console
  cd examples/demo
  wslc compose up -d
  curl http://localhost:8088
  wslc compose exec app cat /data/log.txt     # "... (redis: up)" every 5 s
  wslc compose logs -f
  wslc compose down -v
  ```

- [`examples/build`](examples/build) — building an image from a local Dockerfile with
  `build.args`.

## Project layout

```
src/wslc_compose/
  cli.py            argument parsing + the up/down/ps/logs/... commands
  shim.py           the `wslc` wrapper command (compose → cli, rest → wslc.exe)
  loader.py         compose file discovery, parsing, normalization, validation
  interpolation.py  ${VAR...} substitution engine
  model.py          dataclasses: Project / Service / Network / Volume / mounts
  flags.py          pure functions building wslc argv from the model (unit-tested)
  engine.py         wslc binary discovery, subprocess layer, path translation,
                    container/network/volume queries
tests/              25+ unit tests (no wslc needed — pure logic)
examples/           runnable demos (see above)
install.sh          curl-able installer, bootstraps uv when pip/pipx are missing
```

## Development

```console
git clone https://github.com/yovannyr/wslc-compose
cd wslc-compose
pip install -e . pytest ruff      # or the uv equivalent
pytest                            # unit tests, no wslc required
ruff check src tests
wslc-compose --dry-run -f examples/demo/compose.yaml up -d   # inspect generated commands
```

CI runs the test matrix (Python 3.9 / 3.12 / 3.14) and ruff on every push and PR.

Issues and PRs are welcome — especially reports of wslc preview behavior changes,
since the CLI surface is still evolving.

## License

[MIT](LICENSE) © Yovanny Rodríguez, 2026. See the LICENSE file for details.
