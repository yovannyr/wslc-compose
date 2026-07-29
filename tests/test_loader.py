import pytest

from wslc_compose.loader import ComposeError, load_project, parse_port

COMPOSE = """
name: demo
services:
  web:
    image: nginx:alpine
    ports:
      - "8088:80"
      - "127.0.0.1:9000:9000/udp"
      - target: 443
        published: 8443
    volumes:
      - ./html:/usr/share/nginx/html:ro
      - data:/data
    environment:
      - FOO=bar
      - NOVAL
    depends_on:
      - app
    networks:
      front:
        aliases: [www]
  app:
    image: alpine
    command: sh -c 'sleep 999'
    deploy:
      replicas: 2
      resources:
        limits:
          cpus: "0.5"
          memory: 256M
    networks: [front]
networks:
  front:
volumes:
  data:
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "compose.yaml").write_text(COMPOSE)
    (tmp_path / "html").mkdir()
    return load_project(str(tmp_path / "compose.yaml"))


def test_project_name_from_compose(project):
    assert project.name == "demo"


def test_networks_and_volumes_prefixed(project):
    assert project.networks["front"].name == "demo_front"
    assert project.volumes["data"].name == "demo_data"


def test_ports(project):
    web = project.services["web"]
    assert [p.to_flag() for p in web.ports] == ["8088:80", "127.0.0.1:9000:9000/udp", "8443:443"]


def test_volumes(project, tmp_path):
    web = project.services["web"]
    bind, named = web.volumes
    assert bind.type == "bind"
    assert bind.source == str(tmp_path / "html")
    assert bind.read_only
    assert named.type == "volume"
    assert named.source == "demo_data"  # resolved to project-scoped name


def test_environment_list_form(project):
    assert project.services["web"].environment == {"FOO": "bar", "NOVAL": None}


def test_command_string_is_split(project):
    assert project.services["app"].command == ["sh", "-c", "sleep 999"]


def test_deploy_limits(project):
    app = project.services["app"]
    assert app.replicas == 2
    assert app.cpus == "0.5"
    assert app.mem_limit == "256M"


def test_network_aliases(project):
    web = project.services["web"]
    assert web.networks == ["demo_front"]
    assert web.network_aliases["demo_front"] == ["www"]


def test_dependency_order(project):
    order = [s.name for s in project.sorted_services()]
    assert order.index("app") < order.index("web")


def test_container_names(project):
    web = project.services["web"]
    assert project.container_name(web, 1) == "demo-web-1"


def test_circular_dependency(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  a:
    image: x
    depends_on: [b]
  b:
    image: x
    depends_on: [a]
"""
    )
    project = load_project(str(tmp_path / "compose.yaml"))
    with pytest.raises(ValueError, match="circular"):
        project.sorted_services()


def test_undefined_network_rejected(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  a:
    image: x
    networks: [nope]
"""
    )
    with pytest.raises(ComposeError, match="undefined network"):
        load_project(str(tmp_path / "compose.yaml"))


def test_windows_path_volume(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        r"""
services:
  a:
    image: x
    volumes:
      - E:\data\www:/srv:ro
"""
    )
    project = load_project(str(tmp_path / "compose.yaml"))
    mount = project.services["a"].volumes[0]
    assert mount.type == "bind"
    assert mount.source == r"E:\data\www"
    assert mount.target == "/srv"
    assert mount.read_only


def test_port_range():
    flags = [p.to_flag() for p in parse_port("8000-8002:9000-9002")]
    assert flags == ["8000:9000", "8001:9001", "8002:9002"]


def test_unsupported_key_warns(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  a:
    image: x
    privileged: true
    restart: always
"""
    )
    project = load_project(str(tmp_path / "compose.yaml"))
    text = "\n".join(project.warnings)
    assert "privileged" in text
    assert "restart" in text


def test_unsupported_runtime_key_rejected_in_strict_mode(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  a:
    image: x
    read_only: true
"""
    )

    with pytest.raises(
        ComposeError,
        match=r"unsupported option 'read_only'.*--ignore-unsupported",
    ):
        load_project(str(tmp_path / "compose.yaml"), strict_unsupported=True)


def test_restart_policy_rejected_in_strict_mode(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  a:
    image: x
    restart: always
"""
    )

    with pytest.raises(ComposeError, match=r"unsupported option 'restart: always'"):
        load_project(str(tmp_path / "compose.yaml"), strict_unsupported=True)


def test_file_secrets_short_and_long_syntax(tmp_path):
    (tmp_path / "nuget.config").write_text("secret-value-must-not-leak")
    (tmp_path / "feed.token").write_text("token-value-must-not-leak")
    (tmp_path / "compose.yaml").write_text(
        """
services:
  restore:
    image: dotnet-sdk
    secrets:
      - nuget_config
      - source: feed_token
        target: /root/.nuget/feed.token
        uid: "1000"
        gid: "1000"
        mode: 0400
secrets:
  nuget_config:
    file: ./nuget.config
  feed_token:
    file: ./feed.token
"""
    )

    project = load_project(str(tmp_path / "compose.yaml"))
    service = project.services["restore"]

    assert project.secrets["nuget_config"].file == str(tmp_path / "nuget.config")
    assert service.secrets[0].source == "nuget_config"
    assert service.secrets[0].file == str(tmp_path / "nuget.config")
    assert service.secrets[0].target == "/run/secrets/nuget_config"
    assert service.secrets[1].target == "/root/.nuget/feed.token"
    assert "uid, gid, mode cannot be enforced" in "\n".join(project.warnings)
    assert "secret-value-must-not-leak" not in repr(project)
    assert "token-value-must-not-leak" not in repr(project)


def test_undefined_secret_rejected(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: x
    secrets: [missing]
"""
    )

    with pytest.raises(ComposeError, match="undefined secret 'missing'"):
        load_project(str(tmp_path / "compose.yaml"))


def test_missing_secret_file_rejected(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: x
    secrets: [token]
secrets:
  token:
    file: ./missing.token
"""
    )

    with pytest.raises(ComposeError, match="file not found"):
        load_project(str(tmp_path / "compose.yaml"))


@pytest.mark.parametrize(
    ("definition", "message"),
    [
        ("external: true", "external secrets are not supported"),
        ("environment: FEED_TOKEN", "environment-backed secrets are not supported"),
    ],
)
def test_unsupported_secret_sources_rejected(tmp_path, definition, message):
    (tmp_path / "compose.yaml").write_text(
        f"""
services:
  app:
    image: x
    secrets: [token]
secrets:
  token:
    {definition}
"""
    )

    with pytest.raises(ComposeError, match=message):
        load_project(str(tmp_path / "compose.yaml"))


def test_build_secrets_rejected(tmp_path):
    (tmp_path / "nuget.config").write_text("<configuration />")
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    build:
      context: .
      secrets: [nuget_config]
secrets:
  nuget_config:
    file: ./nuget.config
"""
    )

    with pytest.raises(ComposeError, match=r"build\.secrets are not supported"):
        load_project(str(tmp_path / "compose.yaml"))


def test_invalid_relative_secret_target_rejected(tmp_path):
    (tmp_path / "token").write_text("value")
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: x
    secrets:
      - source: token
        target: ../token
secrets:
  token:
    file: ./token
"""
    )

    with pytest.raises(ComposeError, match="invalid target"):
        load_project(str(tmp_path / "compose.yaml"))


def test_duplicate_secret_target_rejected(tmp_path):
    (tmp_path / "one").write_text("one")
    (tmp_path / "two").write_text("two")
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: x
    secrets:
      - source: one
        target: token
      - source: two
        target: token
secrets:
  one:
    file: ./one
  two:
    file: ./two
"""
    )

    with pytest.raises(ComposeError, match="multiple secrets target"):
        load_project(str(tmp_path / "compose.yaml"))


def test_secret_volume_target_conflict_rejected(tmp_path):
    (tmp_path / "token").write_text("value")
    (tmp_path / "mounted").mkdir()
    (tmp_path / "compose.yaml").write_text(
        """
services:
  app:
    image: x
    volumes:
      - ./mounted:/run/secrets/token
    secrets: [token]
secrets:
  token:
    file: ./token
"""
    )

    with pytest.raises(ComposeError, match="conflicts with a volume"):
        load_project(str(tmp_path / "compose.yaml"))
