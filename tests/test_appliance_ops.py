"""Tests for ubersmith_installer.appliance_ops.

These exercise the appliance container/filesystem lifecycle layer with a
mocked docker SDK client and a mocked subprocess runner -- no real Docker
daemon or network access is required.
"""

import stat
from unittest.mock import MagicMock

from ubersmith_installer import appliance_ops


def test_create_config_directories_creates_every_directory_in_the_list(tmp_path):
    appliance_home = tmp_path / "appliance"

    appliance_ops.create_config_directories(
        appliance_home, owner_uid=1000, owner_gid=1000, chown=MagicMock()
    )

    for relative_path in appliance_ops.CONFIG_DIRECTORIES:
        directory = appliance_home / relative_path
        assert directory.is_dir()
        mode = stat.S_IMODE(directory.stat().st_mode)
        assert mode == appliance_ops.CONFIG_DIRECTORY_MODE


def test_create_config_directories_invokes_chown_with_given_owner(tmp_path):
    appliance_home = tmp_path / "appliance"
    chown = MagicMock()

    appliance_ops.create_config_directories(
        appliance_home, owner_uid=1234, owner_gid=5678, chown=chown
    )

    assert chown.call_count == len(appliance_ops.CONFIG_DIRECTORIES)
    for call in chown.call_args_list:
        args, _ = call
        assert args[1] == 1234
        assert args[2] == 5678


def test_create_config_directories_swallows_permission_errors(tmp_path):
    appliance_home = tmp_path / "appliance"

    def raising_chown(path, uid, gid):
        raise PermissionError("nope")

    # Should not raise -- directories are still created.
    appliance_ops.create_config_directories(
        appliance_home, owner_uid=1000, owner_gid=1000, chown=raising_chown
    )

    for relative_path in appliance_ops.CONFIG_DIRECTORIES:
        assert (appliance_home / relative_path).is_dir()


def test_copy_static_files_copies_all_expected_files_with_correct_mode(tmp_path):
    appliance_home = tmp_path / "appliance"

    appliance_ops.copy_static_files(appliance_home)

    all_files = (
        appliance_ops.ALWAYS_OVERWRITE_HELPER_SCRIPTS
        + appliance_ops.IF_ABSENT_HELPER_SCRIPTS
    )
    for filename in all_files:
        dest = appliance_home / filename
        assert dest.is_file(), f"expected {dest} to exist"
        mode = stat.S_IMODE(dest.stat().st_mode)
        assert mode == appliance_ops.HELPER_SCRIPT_MODE
        assert dest.read_bytes() == (appliance_ops.FILES_DIR / filename).read_bytes()


def test_copy_static_files_always_overwrites_backup_script(tmp_path):
    appliance_home = tmp_path / "appliance"
    appliance_home.mkdir()
    dest = appliance_home / "backup_rrds.sh"
    dest.write_text("stale content")

    appliance_ops.copy_static_files(appliance_home)

    assert dest.read_bytes() == (
        appliance_ops.FILES_DIR / "backup_rrds.sh"
    ).read_bytes()
    assert dest.read_text() != "stale content"


def test_copy_static_files_leaves_existing_restart_start_upgrade_scripts_alone(
    tmp_path,
):
    appliance_home = tmp_path / "appliance"
    appliance_home.mkdir()

    for filename in appliance_ops.IF_ABSENT_HELPER_SCRIPTS:
        dest = appliance_home / filename
        dest.write_text("custom site-specific content")
        dest.chmod(0o600)

    appliance_ops.copy_static_files(appliance_home)

    for filename in appliance_ops.IF_ABSENT_HELPER_SCRIPTS:
        dest = appliance_home / filename
        assert dest.read_text() == "custom site-specific content"
        # Mode is also left untouched since the file was never re-copied.
        assert stat.S_IMODE(dest.stat().st_mode) == 0o600


def test_get_app_web_container_env_returns_config_env():
    client = MagicMock()
    container = MagicMock()
    container.attrs = {"Config": {"Env": ["DATABASE_HOST=app_db", "FOO=bar"]}}
    client.containers.get.return_value = container

    env = appliance_ops.get_app_web_container_env(client=client)

    client.containers.get.assert_called_once_with("ubersmith-app_web-1")
    assert env == ["DATABASE_HOST=app_db", "FOO=bar"]


def test_is_local_database_true_when_database_host_app_db_present():
    assert appliance_ops.is_local_database(["DATABASE_HOST=app_db"]) is True


def test_is_local_database_false_when_remote():
    assert appliance_ops.is_local_database(["DATABASE_HOST=remote.example.com"]) is False


def test_is_local_database_false_for_ubersmith_style_host():
    # Sanity check the appliance check is distinct from docker_ops'.
    assert appliance_ops.is_local_database(["DATABASE_HOST=db"]) is False


def test_compose_pull_invokes_expected_command(tmp_path):
    appliance_home = tmp_path / "appliance"
    runner = MagicMock()

    appliance_ops.compose_pull(appliance_home, runner=runner, env={})

    runner.assert_called_once()
    args, kwargs = runner.call_args
    assert args[0] == ["docker", "compose", "-p", "ubersmith", "pull"]
    assert kwargs["cwd"] == appliance_home


def test_stop_containers_invokes_expected_command(tmp_path):
    appliance_home = tmp_path / "appliance"
    runner = MagicMock()

    appliance_ops.stop_containers(appliance_home, runner=runner, env={})

    args, kwargs = runner.call_args
    assert args[0] == ["docker", "compose", "-p", "ubersmith", "rm", "-sf"]
    assert kwargs["cwd"] == appliance_home


def test_get_existing_volumes_parses_stdout(tmp_path):
    appliance_home = tmp_path / "appliance"
    runner = MagicMock(
        return_value=MagicMock(stdout="ubersmith_app_webroot\nubersmith_app_database\n")
    )

    volumes = appliance_ops.get_existing_volumes(appliance_home, runner=runner, env={})

    args, kwargs = runner.call_args
    assert args[0] == ["docker", "volume", "ls", "-q"]
    assert kwargs["cwd"] == appliance_home
    assert volumes == ["ubersmith_app_webroot", "ubersmith_app_database"]


def test_get_existing_volumes_handles_empty_output(tmp_path):
    appliance_home = tmp_path / "appliance"
    runner = MagicMock(return_value=MagicMock(stdout=""))

    volumes = appliance_ops.get_existing_volumes(appliance_home, runner=runner, env={})

    assert volumes == []


def test_remove_webroot_volume_if_present_removes_when_present(tmp_path):
    appliance_home = tmp_path / "appliance"
    runner = MagicMock()

    ran = appliance_ops.remove_webroot_volume_if_present(
        appliance_home,
        ["ubersmith_app_webroot", "other_volume"],
        runner=runner,
        env={},
    )

    assert ran is True
    args, kwargs = runner.call_args
    assert args[0] == ["docker", "volume", "rm", "ubersmith_app_webroot"]
    assert kwargs["cwd"] == appliance_home


def test_remove_webroot_volume_if_present_skips_when_absent(tmp_path):
    appliance_home = tmp_path / "appliance"
    runner = MagicMock()

    ran = appliance_ops.remove_webroot_volume_if_present(
        appliance_home, ["other_volume"], runner=runner, env={}
    )

    assert ran is False
    runner.assert_not_called()


def test_chown_database_files_invokes_expected_container():
    client = MagicMock()

    appliance_ops.chown_database_files(client=client)

    client.containers.run.assert_called_once()
    kwargs = client.containers.run.call_args.kwargs
    assert kwargs["image"] == "busybox"
    assert kwargs["command"] == "chown -R 1001:1001 /mysql"
    assert kwargs["user"] == "root"
    assert kwargs["remove"] is True
    assert kwargs["volumes"]["ubersmith_app_database"] == {
        "bind": "/mysql",
        "mode": "rw",
    }


def test_compose_up_invokes_expected_command(tmp_path):
    appliance_home = tmp_path / "appliance"
    runner = MagicMock()

    appliance_ops.compose_up(appliance_home, runner=runner, env={})

    args, kwargs = runner.call_args
    assert args[0] == ["docker", "compose", "-p", "ubersmith", "up", "-d"]
    assert kwargs["cwd"] == appliance_home


def test_compose_up_with_services_appends_service_list(tmp_path):
    appliance_home = tmp_path / "appliance"
    runner = MagicMock()

    appliance_ops.compose_up(
        appliance_home, services=["app_web", "app_db", "app_cron"], runner=runner, env={}
    )

    args, kwargs = runner.call_args
    assert args[0] == [
        "docker",
        "compose",
        "-p",
        "ubersmith",
        "up",
        "-d",
        "app_web",
        "app_db",
        "app_cron",
    ]
    assert kwargs["cwd"] == appliance_home


def test_wait_for_containers_healthy_polls_appliance_container_names():
    client = MagicMock()
    container = MagicMock()
    container.attrs = {"State": {"Health": {"Status": "healthy"}}}
    client.containers.get.return_value = container
    sleep = MagicMock()

    appliance_ops.wait_for_containers_healthy(client=client, sleep=sleep)

    expected_calls = [
        ((name,),) for name in appliance_ops.WAIT_FOR_CONTAINERS
    ]
    actual_names = [call.args[0] for call in client.containers.get.call_args_list]
    assert actual_names == appliance_ops.WAIT_FOR_CONTAINERS


def test_wait_for_containers_healthy_raises_on_timeout():
    client = MagicMock()
    container = MagicMock()
    container.attrs = {"State": {"Health": {"Status": "starting"}}}
    client.containers.get.return_value = container
    sleep = MagicMock()

    try:
        appliance_ops.wait_for_containers_healthy(
            client=client, retries=2, delay=1, sleep=sleep
        )
        assert False, "expected TimeoutError"
    except TimeoutError:
        pass


def test_prune_old_images_invokes_expected_filter():
    client = MagicMock()

    appliance_ops.prune_old_images(client=client)

    client.images.prune.assert_called_once_with(filters={"until": "2160h"})


def test_prune_old_images_respects_custom_until():
    client = MagicMock()

    appliance_ops.prune_old_images(client=client, until="720h")

    client.images.prune.assert_called_once_with(filters={"until": "720h"})


# --- mysql 5.7 step-up gating and sequence -----------------------------------


def test_step_up_mysql_57_skipped_when_not_5_6():
    client = MagicMock()

    ran = appliance_ops.step_up_mysql_57(
        "registry.example.com",
        "5.2.2",
        "r3",
        "5.7",
        True,
        client=client,
    )

    assert ran is False
    client.containers.run.assert_not_called()


def test_step_up_mysql_57_skipped_when_remote_database():
    client = MagicMock()

    ran = appliance_ops.step_up_mysql_57(
        "registry.example.com",
        "5.2.2",
        "r3",
        "5.6",
        False,
        client=client,
    )

    assert ran is False
    client.containers.run.assert_not_called()


def test_step_up_mysql_57_skipped_when_neither_condition_met():
    client = MagicMock()

    ran = appliance_ops.step_up_mysql_57(
        "registry.example.com",
        "5.2.2",
        "r3",
        "8.0",
        False,
        client=client,
    )

    assert ran is False
    client.containers.run.assert_not_called()


def test_step_up_mysql_57_runs_full_sequence_when_gated_conditions_met():
    client = MagicMock()
    stepup_container = MagicMock()
    stepup_container.attrs = {"State": {"Health": {"Status": "healthy"}}}
    stepup_container.exec_run.return_value = (0, b"")
    client.containers.get.return_value = stepup_container
    sleep = MagicMock()

    ran = appliance_ops.step_up_mysql_57(
        "registry.example.com",
        "5.2.2",
        "r3",
        "5.6",
        True,
        client=client,
        sleep=sleep,
    )

    assert ran is True

    # 1. Container started with expected image/command/volume.
    client.containers.run.assert_called_once()
    run_kwargs = client.containers.run.call_args.kwargs
    assert run_kwargs["name"] == "ubersmith-app_db_57"
    assert run_kwargs["image"] == "registry.example.com/ps57:5.2.2-r3"
    assert run_kwargs["command"] == "mysqld --skip-grant-tables"
    assert run_kwargs["volumes"]["ubersmith_app_database"] == {
        "bind": "/var/lib/mysql",
        "mode": "rw",
    }

    # 2. Waited for health via containers.get on the stepup container.
    assert client.containers.get.call_args_list[0].args[0] == "ubersmith-app_db_57"

    # 3. Ran mysql_upgrade.
    stepup_container.exec_run.assert_called_once_with(
        "/bin/sh -c 'mysql_upgrade -u root --skip-password'"
    )

    # 4. Removed the container.
    stepup_container.remove.assert_called_once_with(force=True)


def test_step_up_mysql_57_accepts_exit_code_2_as_success():
    client = MagicMock()
    stepup_container = MagicMock()
    stepup_container.attrs = {"State": {"Health": {"Status": "healthy"}}}
    stepup_container.exec_run.return_value = (2, b"")
    client.containers.get.return_value = stepup_container
    sleep = MagicMock()

    ran = appliance_ops.step_up_mysql_57(
        "registry.example.com",
        "5.2.2",
        "r3",
        "5.6",
        True,
        client=client,
        sleep=sleep,
    )

    assert ran is True
    stepup_container.remove.assert_called_once_with(force=True)


def test_step_up_mysql_57_raises_and_still_removes_on_bad_exit_code():
    client = MagicMock()
    stepup_container = MagicMock()
    stepup_container.attrs = {"State": {"Health": {"Status": "healthy"}}}
    stepup_container.exec_run.return_value = (1, b"error")
    client.containers.get.return_value = stepup_container
    sleep = MagicMock()

    try:
        appliance_ops.step_up_mysql_57(
            "registry.example.com",
            "5.2.2",
            "r3",
            "5.6",
            True,
            client=client,
            sleep=sleep,
        )
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass

    # Container is still cleaned up even though the upgrade failed.
    stepup_container.remove.assert_called_once_with(force=True)


def test_step_up_mysql_57_raises_on_wait_timeout_but_still_starts_container():
    client = MagicMock()
    stepup_container = MagicMock()
    stepup_container.attrs = {"State": {"Health": {"Status": "starting"}}}
    client.containers.get.return_value = stepup_container
    sleep = MagicMock()

    try:
        appliance_ops.step_up_mysql_57(
            "registry.example.com",
            "5.2.2",
            "r3",
            "5.6",
            True,
            client=client,
            retries=2,
            delay=1,
            sleep=sleep,
        )
        assert False, "expected TimeoutError"
    except TimeoutError:
        pass

    client.containers.run.assert_called_once()
    # The container is removed even though the wait never succeeded, since
    # the removal happens in a `finally`.
    stepup_container.remove.assert_called_once_with(force=True)


# --- configure_uberapp_user_password / run_upgrade_php -----------------------


def test_configure_uberapp_user_password_invokes_expected_mysql_command():
    runner = MagicMock()

    appliance_ops.configure_uberapp_user_password(
        "dbpassword123", "xmlrpcpassword456", runner=runner
    )

    runner.assert_called_once()
    (cmd,), _ = runner.call_args
    assert cmd[0] == "mysql"
    assert "--host=127.0.0.1" in cmd
    assert "--port=3307" in cmd
    assert "--user=uberapp" in cmd
    assert "--password=dbpassword123" in cmd
    assert "uberapp" in cmd
    query = cmd[-1]
    assert "xmlrpcpassword456" in query
    assert "login = 'ubersmith'" in query


def test_configure_uberapp_user_password_escapes_single_quotes():
    runner = MagicMock()

    appliance_ops.configure_uberapp_user_password(
        "dbpassword123", "weird'pass", runner=runner
    )

    (cmd,), _ = runner.call_args
    query = cmd[-1]
    assert "weird''pass" in query


def test_run_upgrade_php_invokes_expected_command(tmp_path):
    appliance_home = tmp_path / "appliance"
    runner = MagicMock()

    appliance_ops.run_upgrade_php(appliance_home, runner=runner, env={})

    args, kwargs = runner.call_args
    assert args[0] == [
        "docker",
        "compose",
        "-p",
        "ubersmith",
        "exec",
        "-T",
        "app_web",
        "php",
        "/var/www/appliance_root/www/upgrade.php",
    ]
    assert kwargs["cwd"] == appliance_home
