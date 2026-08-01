"""Tests for ubersmith_installer.redis_migration.

These exercise the redis-volume migration dance with a mocked docker SDK
client and a mocked subprocess runner -- no real Docker daemon or network
access is required.
"""

from pathlib import Path
from unittest.mock import MagicMock

import docker.errors

from ubersmith_installer import redis_migration


def test_volume_exists_true_when_client_finds_volume():
    client = MagicMock()
    client.volumes.get.return_value = MagicMock()

    assert redis_migration.volume_exists("ubersmith_redis", client=client) is True
    client.volumes.get.assert_called_once_with("ubersmith_redis")


def test_volume_exists_false_when_not_found():
    client = MagicMock()
    client.volumes.get.side_effect = docker.errors.NotFound("no such volume")

    assert redis_migration.volume_exists("ubersmith_redis", client=client) is False


def test_trigger_redis_save_swallows_failure_without_raising():
    client = MagicMock()
    client.containers.get.side_effect = docker.errors.NotFound("no such container")

    # Should not raise, mirroring `failed_when: false`.
    redis_migration.trigger_redis_save(client=client)


def test_trigger_redis_save_execs_expected_command():
    client = MagicMock()
    container = MagicMock()
    client.containers.get.return_value = container

    redis_migration.trigger_redis_save(client=client)

    client.containers.get.assert_called_once_with("ubersmith-redis-1")
    container.exec_run.assert_called_once_with(
        ["/bin/bash", "-c", "/usr/local/bin/redis-cli SAVE"]
    )


def test_copy_redis_dump_out_invokes_runner_with_expected_command_and_cwd(tmp_path):
    runner = MagicMock()

    redis_migration.copy_redis_dump_out(tmp_path, runner=runner)

    runner.assert_called_once_with(
        ["docker", "cp", "ubersmith-redis-1:/data/dump.rdb", "."], cwd=Path(tmp_path)
    )


def test_copy_redis_dump_in_invokes_runner_with_expected_command_and_cwd(tmp_path):
    runner = MagicMock()

    redis_migration.copy_redis_dump_in(tmp_path, runner=runner)

    runner.assert_called_once_with(
        ["docker", "cp", "dump.rdb", "ubersmith-redis-data-1:/data/dump.rdb"],
        cwd=Path(tmp_path),
    )


def test_chown_redis_dump_execs_expected_command():
    client = MagicMock()
    container = MagicMock()
    client.containers.get.return_value = container

    redis_migration.chown_redis_dump(client=client)

    client.containers.get.assert_called_once_with("ubersmith-redis-data-1")
    container.exec_run.assert_called_once_with(
        ["/bin/bash", "-c", "chown redis:redis /data/dump.rdb"]
    )


def test_chown_redis_dump_swallows_failure_without_raising():
    client = MagicMock()
    client.containers.get.side_effect = docker.errors.NotFound("no such container")

    redis_migration.chown_redis_dump(client=client)


def test_remove_webroot_volume_if_present_removes_when_exists():
    client = MagicMock()
    volume = MagicMock()
    client.volumes.get.return_value = volume

    redis_migration.remove_webroot_volume_if_present(client=client)

    client.volumes.get.assert_any_call("ubersmith_webroot")
    volume.remove.assert_called_once()


def test_remove_webroot_volume_if_present_does_nothing_when_absent():
    client = MagicMock()
    client.volumes.get.side_effect = docker.errors.NotFound("no such volume")

    redis_migration.remove_webroot_volume_if_present(client=client)

    client.volumes.get.assert_called_once_with("ubersmith_webroot")


def test_migrate_redis_volume_returns_false_and_does_nothing_when_volume_exists(
    tmp_path,
):
    client = MagicMock()
    client.volumes.get.return_value = MagicMock()
    runner = MagicMock()

    result = redis_migration.migrate_redis_volume(
        tmp_path, client=client, runner=runner
    )

    assert result is False
    client.containers.get.assert_not_called()
    runner.assert_not_called()


def test_migrate_redis_volume_returns_true_and_calls_save_and_copy_out_when_missing(
    tmp_path,
):
    client = MagicMock()
    client.volumes.get.side_effect = docker.errors.NotFound("no such volume")
    container = MagicMock()
    client.containers.get.return_value = container
    runner = MagicMock()

    result = redis_migration.migrate_redis_volume(
        tmp_path, client=client, runner=runner
    )

    assert result is True
    client.containers.get.assert_called_once_with("ubersmith-redis-1")
    container.exec_run.assert_called_once_with(
        ["/bin/bash", "-c", "/usr/local/bin/redis-cli SAVE"]
    )
    runner.assert_called_once_with(
        ["docker", "cp", "ubersmith-redis-1:/data/dump.rdb", "."], cwd=Path(tmp_path)
    )
