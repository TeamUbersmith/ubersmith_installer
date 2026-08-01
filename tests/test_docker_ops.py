"""Tests for ubersmith_installer.docker_ops.

These exercise the container/filesystem lifecycle layer with a mocked
docker SDK client and a mocked subprocess runner -- no real Docker daemon
or network access is required.
"""

import stat
from pathlib import Path
from unittest.mock import MagicMock

from ubersmith_installer import docker_ops


def test_pull_images_calls_client_pull_for_each_ref():
    client = MagicMock()
    refs = [
        "ghcr.io/teamubersmith/solr:5.2.2-r3",
        "ghcr.io/teamubersmith/ps84:5.2.2-r3",
        "busybox:latest",
    ]

    docker_ops.pull_images(refs, client=client)

    assert client.images.pull.call_count == len(refs)
    client.images.pull.assert_any_call("ghcr.io/teamubersmith/solr:5.2.2-r3")
    client.images.pull.assert_any_call("ghcr.io/teamubersmith/ps84:5.2.2-r3")
    client.images.pull.assert_any_call("busybox:latest")


def test_create_config_directories_creates_expected_subset(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"

    # Avoid requiring real chown privileges in the test environment.
    docker_ops.create_config_directories(
        ubersmith_home, owner_uid=1000, owner_gid=1000, chown=MagicMock()
    )

    representative_paths = [
        "logs/ubersmith",
        "conf/mysql",
        "conf/ssl",
        "conf/certbot/etc/renewal-hooks/deploy",
        "app/custom/.well-known/acme-challenge",
    ]
    for relative_path in representative_paths:
        directory = ubersmith_home / relative_path
        assert directory.is_dir(), f"expected {directory} to exist"
        mode = stat.S_IMODE(directory.stat().st_mode)
        assert mode == docker_ops.CONFIG_DIRECTORY_MODE


def test_create_config_directories_creates_every_directory_in_the_list(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"

    docker_ops.create_config_directories(
        ubersmith_home, owner_uid=1000, owner_gid=1000, chown=MagicMock()
    )

    for relative_path in docker_ops.CONFIG_DIRECTORIES:
        assert (ubersmith_home / relative_path).is_dir()


def test_create_config_directories_invokes_chown_with_given_owner(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    chown = MagicMock()

    docker_ops.create_config_directories(
        ubersmith_home, owner_uid=1234, owner_gid=5678, chown=chown
    )

    assert chown.call_count == len(docker_ops.CONFIG_DIRECTORIES)
    for call in chown.call_args_list:
        args, _ = call
        assert args[1] == 1234
        assert args[2] == 5678


def test_copy_static_files_copies_expected_files_with_correct_modes(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()

    docker_ops.copy_static_files(ubersmith_home)

    restart = ubersmith_home / "ubersmith_restart.sh"
    start = ubersmith_home / "ubersmith_start.sh"
    falco = ubersmith_home / "conf" / "falco" / "falco_rules.local.yaml"

    assert restart.is_file()
    assert start.is_file()
    assert falco.is_file()

    assert stat.S_IMODE(restart.stat().st_mode) == docker_ops.HELPER_SCRIPT_MODE
    assert stat.S_IMODE(start.stat().st_mode) == docker_ops.HELPER_SCRIPT_MODE
    assert stat.S_IMODE(falco.stat().st_mode) == docker_ops.FALCO_RULES_MODE

    # Content should be byte-identical to the shipped source files.
    assert restart.read_bytes() == (docker_ops.FILES_DIR / "ubersmith_restart.sh").read_bytes()
    assert start.read_bytes() == (docker_ops.FILES_DIR / "ubersmith_start.sh").read_bytes()
    assert falco.read_bytes() == (docker_ops.FILES_DIR / "falco_rules.local.yaml").read_bytes()


def test_compose_up_invokes_runner_with_expected_command_cwd_and_env(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    runner = MagicMock()

    docker_ops.compose_up(
        ubersmith_home, extra_env={"MAINTENANCE": "1"}, runner=runner, env={}
    )

    runner.assert_called_once()
    args, kwargs = runner.call_args
    cmd = args[0]
    assert cmd == [
        "docker",
        "compose",
        "up",
        "-d",
        "--quiet-pull",
        "--no-color",
        "web",
        "cron",
        "db",
        "php",
        "solr",
        "mail",
        "rsyslog",
        "rwhois",
        "redis-data",
    ]
    assert kwargs["cwd"] == ubersmith_home
    assert kwargs["env"]["MAINTENANCE"] == "1"


def test_compose_up_with_no_extra_env_does_not_require_maintenance(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    runner = MagicMock()

    docker_ops.compose_up(ubersmith_home, runner=runner, env={"PATH": "/usr/bin"})

    runner.assert_called_once()
    _, kwargs = runner.call_args
    assert kwargs["env"] == {"PATH": "/usr/bin"}


def test_scale_redis_invokes_runner_with_expected_command_and_cwd(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    runner = MagicMock()

    docker_ops.scale_redis(ubersmith_home, runner=runner, env={})

    runner.assert_called_once()
    args, kwargs = runner.call_args
    cmd = args[0]
    assert cmd == ["docker", "compose", "up", "-d", "--scale", "redis=3", "redis"]
    assert kwargs["cwd"] == ubersmith_home


def test_scale_redis_respects_custom_count(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    runner = MagicMock()

    docker_ops.scale_redis(ubersmith_home, count=5, runner=runner, env={})

    args, _ = runner.call_args
    assert args[0] == ["docker", "compose", "up", "-d", "--scale", "redis=5", "redis"]
