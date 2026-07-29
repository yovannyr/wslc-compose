# Changelog

All notable changes to wslc-compose are documented here.

## 0.5.0

- Add file-backed runtime secrets and materialized Compose configs.
- Add multi-file merging, `include`, `extends`, `!reset`, and `!override`.
- Add readiness healthchecks and extended `depends_on` conditions.
- Add image pull policies, one-off jobs, lifecycle hooks, and job exit workflows.
- Add dependency-aware stop/restart, anonymous volumes, orphan handling, and failed-up rollback.
- Add native `create`, recreate controls, resource metadata, and long `env_file` syntax.
- Add utility commands including `wait`, `kill`, `rm`, `images`, `push`, `port`, and `stats`.
- Add expanded `config`, pull, build, and image-cleanup options.
- Add `develop.watch` support for `rebuild` and `restart`.
- Document wslc runtime limitations and reject unsupported semantics explicitly.
