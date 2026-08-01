"""Tests for the `ubersmith-installer upgrade` CLI command."""

import configparser
from pathlib import Path
from unittest.mock import MagicMock

from click.testing import CliRunner

from ubersmith_installer.cli import main
from ubersmith_installer import state as state_mod


def _patch_home(monkeypatch, tmp_path):
    """Point Path.home() (used for the MySQL password files) at tmp_path,
    so tests never touch the real invoking user's home directory."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def _seed_password_files(home: Path, main_virtual_host: str) -> None:
    """Pre-create the mysql password files as an install would have,
    so get_or_create_password reads them back rather than generating new
    ones (per the upgrade command's contract)."""
    (home / f".ubersmith_{main_virtual_host}_root_db_pass").write_text("rootpw123")
    (home / f".ubersmith_{main_virtual_host}_ubersmith_db_pass").write_text("uberpw123")


def _write_state(
    state_file: Path,
    *,
    ubersmith_home: Path,
    virtual_host: str = "ubersmith.example.com",
    admin_email: str = "admin@example.com",
    ubersmith_installed_version: str = "4.6.4",
    lets_encrypt_certificate: str = "no",
) -> None:
    state_mod.write_installer_state(
        state_mod.InstallerState(
            ubersmith_home=str(ubersmith_home),
            virtual_host=virtual_host,
            admin_email=admin_email,
            ubersmith_installed_version=ubersmith_installed_version,
            lets_encrypt_certificate=lets_encrypt_certificate,
        ),
        path=state_file,
    )


def _patch_side_effecting_modules(monkeypatch, *, is_local_database=True, redis_migration_needed=False):
    """Replace every Docker/subprocess/network-touching call the upgrade
    command makes with a mock, so the full flow can be exercised without a
    real Docker daemon, systemctl, or a running Ubersmith stack."""
    mocks = {
        "get_web_container_env": MagicMock(return_value=(
            ["DATABASE_HOST=db"] if is_local_database else ["DATABASE_HOST=remote.example.com"]
        )),
        "pull_images": MagicMock(),
        "compose_up": MagicMock(),
        "scale_redis": MagicMock(),
        "backup_mysql_keyring": MagicMock(),
        "copy_mysql_component_files": MagicMock(),
        "copy_static_files": MagicMock(),
        "chown_database_files": MagicMock(),
        "stop_containers": MagicMock(),
        "wait_for_containers_healthy": MagicMock(),
        "check_database_container_healthy": MagicMock(),
        "run_updatedb": MagicMock(return_value=("stdout output", "stderr output")),
        "remove_setup_dir": MagicMock(),
        "prune_old_images": MagicMock(),
        "set_journald_retention": MagicMock(),
        "restart_systemd_journald": MagicMock(),
        "install_renewal_cron_task": MagicMock(),
        "migrate_redis_volume": MagicMock(return_value=redis_migration_needed),
        "remove_webroot_volume_if_present": MagicMock(),
        "copy_redis_dump_in": MagicMock(),
        "chown_redis_dump": MagicMock(),
        "migrate_caching_sha2_password_runner": MagicMock(),
    }

    monkeypatch.setattr(
        "ubersmith_installer.cli.docker_ops.get_web_container_env",
        mocks["get_web_container_env"],
    )
    monkeypatch.setattr("ubersmith_installer.cli.docker_ops.pull_images", mocks["pull_images"])
    monkeypatch.setattr("ubersmith_installer.cli.docker_ops.compose_up", mocks["compose_up"])
    monkeypatch.setattr("ubersmith_installer.cli.docker_ops.scale_redis", mocks["scale_redis"])
    monkeypatch.setattr(
        "ubersmith_installer.cli.docker_ops.backup_mysql_keyring", mocks["backup_mysql_keyring"]
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.docker_ops.copy_mysql_component_files",
        mocks["copy_mysql_component_files"],
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.docker_ops.copy_static_files", mocks["copy_static_files"]
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.docker_ops.chown_database_files", mocks["chown_database_files"]
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.docker_ops.stop_containers", mocks["stop_containers"]
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.docker_ops.wait_for_containers_healthy",
        mocks["wait_for_containers_healthy"],
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.docker_ops.check_database_container_healthy",
        mocks["check_database_container_healthy"],
    )
    monkeypatch.setattr("ubersmith_installer.cli.docker_ops.run_updatedb", mocks["run_updatedb"])
    monkeypatch.setattr(
        "ubersmith_installer.cli.docker_ops.remove_setup_dir", mocks["remove_setup_dir"]
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.docker_ops.prune_old_images", mocks["prune_old_images"]
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.system_config.set_journald_retention",
        mocks["set_journald_retention"],
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.system_config.restart_systemd_journald",
        mocks["restart_systemd_journald"],
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.certbot.install_renewal_cron_task",
        mocks["install_renewal_cron_task"],
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.redis_migration.migrate_redis_volume",
        mocks["migrate_redis_volume"],
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.redis_migration.remove_webroot_volume_if_present",
        mocks["remove_webroot_volume_if_present"],
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.redis_migration.copy_redis_dump_in",
        mocks["copy_redis_dump_in"],
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.redis_migration.chown_redis_dump", mocks["chown_redis_dump"]
    )
    # migrate_caching_sha2_password shells out via docker_ops.SubprocessRunner
    # convention -- patch subprocess.run globally as used by migrations.py's
    # _default_runner so no real `docker compose exec` is attempted.
    monkeypatch.setattr(
        "ubersmith_installer.migrations.subprocess.run", mocks["migrate_caching_sha2_password_runner"]
    )

    return mocks


def _run_upgrade(tmp_path, state_file, extra_args=None):
    runner = CliRunner()
    args = ["upgrade", "--state-file", str(state_file), "--skip-preflight"]
    if extra_args:
        args.extend(extra_args)
    return runner.invoke(main, args, input="\n\n\n")


def test_upgrade_fails_when_state_missing(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    state_file = tmp_path / "state.ini"

    result = _run_upgrade(tmp_path, state_file)

    assert result.exit_code != 0
    assert "Installer configuration is not present" in result.output


def test_upgrade_fails_when_state_missing_required_keys(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    state_file = tmp_path / "state.ini"
    # Write a state file with only ubersmith_home -- missing virtual_host/admin_email.
    state_mod.write_state({"ubersmith_home": str(tmp_path / "ubersmith")}, path=state_file)

    result = _run_upgrade(tmp_path, state_file)

    assert result.exit_code != 0
    assert "Installer configuration is not present" in result.output
    assert "virtual_host" in result.output
    assert "admin_email" in result.output


def test_upgrade_local_database_full_flow(tmp_path, monkeypatch):
    """Local-database upgrade from an old version: exercises
    caching_sha2_password migration, chown_database_files, and the
    wait_for_containers_healthy/check_database_container_healthy pair."""
    home = _patch_home(monkeypatch, tmp_path)
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir(parents=True)
    (ubersmith_home / "docker-compose.override.yml").write_text(
        "version: '3'\nservices:\n  web:\n    volumes:\n      - /etc/php/7.3:/etc/php/7.3\n"
    )
    _seed_password_files(home, "ubersmith.example.com")
    state_file = tmp_path / "state.ini"
    _write_state(
        state_file,
        ubersmith_home=ubersmith_home,
        ubersmith_installed_version="4.6.4",
        lets_encrypt_certificate="yes",
    )

    mocks = _patch_side_effecting_modules(monkeypatch, is_local_database=True)

    result = _run_upgrade(tmp_path, state_file)

    assert result.exit_code == 0, result.output

    # docker-compose.yml re-rendered.
    assert (ubersmith_home / "docker-compose.yml").exists()
    # mysql cnf re-rendered (old version < 5.2.0).
    assert (ubersmith_home / "conf" / "mysql" / "ubersmith.cnf").exists()
    assert (ubersmith_home / "conf" / "mysql" / "ubersmith_extra.cnf").exists()

    # docker-compose.override.yml got its narrow legacy fixups applied, not
    # wholesale re-rendered (no mysql_root_password key ever appears).
    override_content = (ubersmith_home / "docker-compose.override.yml").read_text()
    assert "version:" not in override_content
    assert "/etc/php/8.4" in override_content
    assert "mysql_root_password" not in override_content

    # Local-database-only steps ran.
    mocks["chown_database_files"].assert_called_once()
    mocks["wait_for_containers_healthy"].assert_called_once_with(
        ["ubersmith-web-1", "ubersmith-php-1", "ubersmith-solr-1"]
    )
    mocks["check_database_container_healthy"].assert_called_once()

    # caching_sha2_password migration ran a real `docker compose exec`
    # (mocked at the subprocess.run level).
    mocks["migrate_caching_sha2_password_runner"].assert_called_once()
    cmd = mocks["migrate_caching_sha2_password_runner"].call_args[0][0]
    assert cmd[:5] == ["docker", "compose", "exec", "db", "sh"]

    # Let's Encrypt still requested per state -> renewal cron re-installed.
    mocks["install_renewal_cron_task"].assert_called_once_with(ubersmith_home)

    # Containers stopped, redis webroot volume removed, images pulled/started.
    mocks["stop_containers"].assert_called_once_with(ubersmith_home)
    mocks["remove_webroot_volume_if_present"].assert_called_once()
    mocks["pull_images"].assert_called_once()

    # compose_up call 0: the pre-upgrade failsafe (`docker compose up -d web
    # db php`), run before anything else.
    failsafe_args, failsafe_kwargs = mocks["compose_up"].call_args_list[0]
    assert failsafe_args[0] == ubersmith_home
    assert failsafe_kwargs["services"] == ["web", "db", "php"]
    assert failsafe_kwargs["quiet_pull"] is False

    # compose_up call 1: maintenance mode enabled, full service list (default).
    first_up_args, first_up_kwargs = mocks["compose_up"].call_args_list[1]
    assert first_up_args[0] == ubersmith_home
    assert first_up_kwargs["extra_env"] == {"MAINTENANCE": "1"}

    # compose_up call 2: maintenance mode disabled, web only.
    second_up_args, second_up_kwargs = mocks["compose_up"].call_args_list[2]
    assert second_up_args[0] == ubersmith_home
    assert second_up_kwargs["extra_env"] == {"MAINTENANCE": "0"}
    assert second_up_kwargs["services"] == ["web"]
    assert second_up_kwargs["quiet_pull"] is False

    mocks["scale_redis"].assert_called_once_with(ubersmith_home)
    mocks["run_updatedb"].assert_called_once()
    mocks["remove_setup_dir"].assert_called_once()
    mocks["backup_mysql_keyring"].assert_called_once_with(ubersmith_home)
    mocks["prune_old_images"].assert_called_once()

    # No redis dump copy-in since migrate_redis_volume returned False.
    mocks["copy_redis_dump_in"].assert_not_called()
    mocks["chown_redis_dump"].assert_not_called()

    # Updatedb debug output surfaced.
    assert "stdout output" in result.output
    assert "stderr output" in result.output

    # State file updated: new version written, everything else preserved.
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(state_file, encoding="utf-8")
    assert parser.get("ubersmith_installer", "ubersmith_installed_version") == "5.2.2"
    assert parser.get("ubersmith_installer", "ubersmith_home") == str(ubersmith_home)
    assert parser.get("ubersmith_installer", "virtual_host") == "ubersmith.example.com"
    assert parser.get("ubersmith_installer", "admin_email") == "admin@example.com"
    assert parser.get("ubersmith_installer", "lets_encrypt_certificate") == "yes"

    assert "Ubersmith upgrade complete" in result.output


def test_upgrade_remote_database_skips_local_only_steps(tmp_path, monkeypatch):
    """Remote-database upgrade: skips chown_database_files,
    wait_for_containers_healthy/check_database_container_healthy, and the
    caching_sha2_password migration."""
    home = _patch_home(monkeypatch, tmp_path)
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir(parents=True)
    (ubersmith_home / "docker-compose.override.yml").write_text("services:\n  web: {}\n")
    _seed_password_files(home, "ubersmith.example.com")
    state_file = tmp_path / "state.ini"
    _write_state(
        state_file,
        ubersmith_home=ubersmith_home,
        ubersmith_installed_version="4.6.4",
        lets_encrypt_certificate="no",
    )

    mocks = _patch_side_effecting_modules(monkeypatch, is_local_database=False)

    result = _run_upgrade(tmp_path, state_file)

    assert result.exit_code == 0, result.output

    mocks["chown_database_files"].assert_not_called()
    mocks["wait_for_containers_healthy"].assert_not_called()
    mocks["check_database_container_healthy"].assert_not_called()
    mocks["migrate_caching_sha2_password_runner"].assert_not_called()

    # Let's Encrypt not requested -> renewal cron not (re-)installed.
    mocks["install_renewal_cron_task"].assert_not_called()

    # Containers are still stopped/restarted regardless of db topology.
    mocks["stop_containers"].assert_called_once()
    mocks["compose_up"].assert_called()
    mocks["run_updatedb"].assert_called_once()


def test_upgrade_redis_migration_needed_copies_dump_after_containers_up(tmp_path, monkeypatch):
    """When migrate_redis_volume() returns True, copy_redis_dump_in/
    chown_redis_dump must run AFTER compose_up/scale_redis bring up the new
    containers, not before."""
    home = _patch_home(monkeypatch, tmp_path)
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir(parents=True)
    (ubersmith_home / "docker-compose.override.yml").write_text("services:\n  web: {}\n")
    _seed_password_files(home, "ubersmith.example.com")
    state_file = tmp_path / "state.ini"
    _write_state(
        state_file,
        ubersmith_home=ubersmith_home,
        ubersmith_installed_version="5.2.2",
        lets_encrypt_certificate="no",
    )

    mocks = _patch_side_effecting_modules(
        monkeypatch, is_local_database=True, redis_migration_needed=True
    )

    call_order = []
    mocks["compose_up"].side_effect = lambda *a, **k: call_order.append("compose_up")
    mocks["scale_redis"].side_effect = lambda *a, **k: call_order.append("scale_redis")
    mocks["copy_redis_dump_in"].side_effect = lambda *a, **k: call_order.append(
        "copy_redis_dump_in"
    )
    mocks["chown_redis_dump"].side_effect = lambda *a, **k: call_order.append("chown_redis_dump")

    result = _run_upgrade(tmp_path, state_file)

    assert result.exit_code == 0, result.output

    mocks["copy_redis_dump_in"].assert_called_once_with(ubersmith_home)
    mocks["chown_redis_dump"].assert_called_once()

    # copy_redis_dump_in/chown_redis_dump happen strictly after the first
    # compose_up + scale_redis (which bring up ubersmith-redis-data-1).
    first_compose_up_index = call_order.index("compose_up")
    scale_redis_index = call_order.index("scale_redis")
    copy_in_index = call_order.index("copy_redis_dump_in")
    chown_index = call_order.index("chown_redis_dump")

    assert first_compose_up_index < copy_in_index
    assert scale_redis_index < copy_in_index
    assert copy_in_index < chown_index

    # mysql cnf NOT re-rendered this time (old version 5.2.2 >= 5.2.0 gate).
    # (file may still not exist since conf dir wasn't pre-seeded with one)
    cnf_path = ubersmith_home / "conf" / "mysql" / "ubersmith.cnf"
    assert not cnf_path.exists()


def test_upgrade_non_interactive_skips_patch_cleanup_and_blocking_prompts(tmp_path, monkeypatch):
    home = _patch_home(monkeypatch, tmp_path)
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir(parents=True)
    (ubersmith_home / "docker-compose.override.yml").write_text("services:\n  web: {}\n")
    (ubersmith_home / ".patched").write_text("some patch marker")
    (ubersmith_home / "app" / "patches").mkdir(parents=True)
    _seed_password_files(home, "ubersmith.example.com")
    state_file = tmp_path / "state.ini"
    _write_state(
        state_file,
        ubersmith_home=ubersmith_home,
        ubersmith_installed_version="5.0.0",
        lets_encrypt_certificate="no",
    )

    _patch_side_effecting_modules(monkeypatch, is_local_database=True)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "upgrade",
            "--state-file",
            str(state_file),
            "--skip-preflight",
            "--non-interactive",
        ],
    )

    assert result.exit_code == 0, result.output
    # patch_cleanup.cleanup_legacy_patches is a no-op (returns False) when
    # not interactive -- the .patched file is left untouched.
    assert (ubersmith_home / ".patched").exists()
    # Reminders logged as informational, not blocking (no input supplied and
    # the command still completed successfully).
    assert "[info]" in result.output


def test_upgrade_help_invokable():
    runner = CliRunner()
    result = runner.invoke(main, ["upgrade", "--help"])
    assert result.exit_code == 0
    assert "--non-interactive" in result.output
    assert "--skip-preflight" in result.output
    assert "--state-file" in result.output
