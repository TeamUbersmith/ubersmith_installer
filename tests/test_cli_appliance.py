"""Tests for the `ubersmith-installer install-appliance` and
`upgrade-appliance` CLI commands."""

import configparser
from pathlib import Path
from unittest.mock import MagicMock

import yaml
from click.testing import CliRunner

from ubersmith_installer.cli import main
from ubersmith_installer import state as state_mod

FULLY_FLAGGED_INSTALL_ARGS = [
    "--ubersmith-major-version",
    "5",
    "--app-virtual-host",
    "appliance.example.com",
]


def _patch_home(monkeypatch, tmp_path):
    """Point Path.home() (used for the MySQL password files) at tmp_path,
    so tests never touch the real invoking user's home directory."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def _seed_password_files(home: Path, app_virtual_host: str) -> None:
    (home / f".appliance_{app_virtual_host}_root_db_pass").write_text("rootpw123")
    (home / f".appliance_{app_virtual_host}_appliance_db_pass").write_text("dbpw123")
    (home / f".appliance_{app_virtual_host}_appliance_xmlrpc_pass").write_text("xmlrpcpw123")


def _write_appliance_state(
    state_file: Path,
    *,
    appliance_home: Path,
    app_virtual_host: str = "appliance.example.com",
    app_mysql_version: str = "5.6",
    appliance_installed_version: str = "4.6.3",
) -> None:
    state_mod.write_installer_state(
        state_mod.InstallerState(
            ubersmith_home=str(appliance_home),
            virtual_host=app_virtual_host,
            appliance_home=str(appliance_home),
            app_virtual_host=app_virtual_host,
            app_mysql_version=app_mysql_version,
            appliance_installed_version=appliance_installed_version,
        ),
        path=state_file,
    )


def _patch_install_side_effects(monkeypatch):
    mocks = {
        "pull_images": MagicMock(),
        "compose_pull": MagicMock(),
        "compose_up": MagicMock(),
        "wait_for_containers_healthy": MagicMock(),
        "configure_uberapp_user_password": MagicMock(),
    }
    monkeypatch.setattr("ubersmith_installer.cli.docker_ops.pull_images", mocks["pull_images"])
    monkeypatch.setattr(
        "ubersmith_installer.cli.appliance_ops.compose_pull", mocks["compose_pull"]
    )
    monkeypatch.setattr("ubersmith_installer.cli.appliance_ops.compose_up", mocks["compose_up"])
    monkeypatch.setattr(
        "ubersmith_installer.cli.appliance_ops.wait_for_containers_healthy",
        mocks["wait_for_containers_healthy"],
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.appliance_ops.configure_uberapp_user_password",
        mocks["configure_uberapp_user_password"],
    )
    return mocks


def _patch_upgrade_side_effects(monkeypatch, *, is_local_database=True):
    mocks = {
        "pull_images": MagicMock(),
        "compose_pull": MagicMock(),
        "compose_up": MagicMock(),
        "wait_for_containers_healthy": MagicMock(),
        "get_app_web_container_env": MagicMock(
            return_value=(
                ["DATABASE_HOST=app_db"]
                if is_local_database
                else ["DATABASE_HOST=remote.example.com"]
            )
        ),
        "get_running_app_db_image": MagicMock(
            return_value="ghcr.io/teamubersmith/appliance_db_ps57:4.6.3-r4"
            if is_local_database
            else None
        ),
        "stop_containers": MagicMock(),
        "get_existing_volumes": MagicMock(return_value=["ubersmith_app_webroot"]),
        "remove_webroot_volume_if_present": MagicMock(),
        "chown_database_files": MagicMock(),
        "step_up_mysql_57": MagicMock(return_value=False),
        "run_upgrade_php": MagicMock(),
        "prune_old_images": MagicMock(),
    }
    monkeypatch.setattr("ubersmith_installer.cli.docker_ops.pull_images", mocks["pull_images"])
    monkeypatch.setattr(
        "ubersmith_installer.cli.appliance_ops.compose_pull", mocks["compose_pull"]
    )
    monkeypatch.setattr("ubersmith_installer.cli.appliance_ops.compose_up", mocks["compose_up"])
    monkeypatch.setattr(
        "ubersmith_installer.cli.appliance_ops.wait_for_containers_healthy",
        mocks["wait_for_containers_healthy"],
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.appliance_ops.get_app_web_container_env",
        mocks["get_app_web_container_env"],
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.appliance_ops.get_running_app_db_image",
        mocks["get_running_app_db_image"],
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.appliance_ops.stop_containers", mocks["stop_containers"]
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.appliance_ops.get_existing_volumes",
        mocks["get_existing_volumes"],
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.appliance_ops.remove_webroot_volume_if_present",
        mocks["remove_webroot_volume_if_present"],
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.appliance_ops.chown_database_files",
        mocks["chown_database_files"],
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.appliance_ops.step_up_mysql_57", mocks["step_up_mysql_57"]
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.appliance_ops.run_upgrade_php", mocks["run_upgrade_php"]
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.appliance_ops.prune_old_images", mocks["prune_old_images"]
    )
    return mocks


# --- install-appliance -------------------------------------------------------


def test_install_appliance_dry_run_renders_all_configs_and_writes_state(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    appliance_home = tmp_path / "appliance"
    state_file = tmp_path / "state.ini"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "install-appliance",
            "--ubersmith-major-version",
            "5",
            "--appliance-home",
            str(appliance_home),
            "--app-virtual-host",
            "appliance.example.com",
            "--state-file",
            str(state_file),
            "--skip-preflight",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output

    compose_path = appliance_home / "docker-compose.yml"
    assert compose_path.exists()
    parsed = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert "app_web" in parsed["services"]
    assert "app_db" in parsed["services"]

    override_path = appliance_home / "docker-compose.override.yml"
    assert override_path.exists()
    override_parsed = yaml.safe_load(override_path.read_text(encoding="utf-8"))
    assert override_parsed["services"]["app_db"]["environment"]["MYSQL_ROOT_PASSWORD"]

    assert (appliance_home / "conf" / "mysql" / "ubersmith.cnf").exists()

    vhost_path = appliance_home / "conf" / "httpd" / "sites-enabled" / "appliance.conf"
    assert vhost_path.exists()
    assert "appliance.appliance.example.com" in vhost_path.read_text()

    ssl_dir = appliance_home / "conf" / "ssl"
    assert (ssl_dir / "appliance.example.com.pem").exists()

    assert (appliance_home / "logs" / "appliance").is_dir()
    assert (appliance_home / "backup_rrds.sh").exists()

    assert list((tmp_path / "home").glob(".appliance_appliance.example.com_*_pass"))

    assert state_file.exists()
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(state_file, encoding="utf-8")
    assert parser.get("ubersmith_installer", "appliance_home") == str(appliance_home)
    assert parser.get("ubersmith_installer", "app_virtual_host") == "appliance.example.com"
    assert parser.get("ubersmith_installer", "ubersmith_home") == str(appliance_home)
    assert parser.get("ubersmith_installer", "virtual_host") == "appliance.example.com"
    assert parser.get("ubersmith_installer", "appliance_installed_version") == "5.1.4"
    assert parser.get("ubersmith_installer", "app_mysql_version") == "8.0"

    assert "Ubersmith Appliance install complete" in result.output
    assert "PLEASE NOTE" in result.output


def test_install_appliance_major_4_records_actual_mysql_version(tmp_path, monkeypatch):
    """Regression test: install-appliance must record the MySQL version
    actually installed for the chosen major version (57 -> "5.7" for major
    4), not a hardcoded "8.0" -- the latter is a latent bug in the Ansible
    source itself (its "Database upgrade successful..." ini_file task is
    tagged plain `upgrade`, so it also fires during install, always writing
    "8.0" regardless of major version) that this codebase deliberately does
    NOT reproduce, since it silently defeats the mysql 5.6->5.7 step-up
    migration for any appliance that later goes through a normal
    install-then-upgrade lifecycle.
    """
    _patch_home(monkeypatch, tmp_path)
    appliance_home = tmp_path / "appliance"
    state_file = tmp_path / "state.ini"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "install-appliance",
            "--ubersmith-major-version",
            "4",
            "--appliance-home",
            str(appliance_home),
            "--app-virtual-host",
            "appliance.example.com",
            "--state-file",
            str(state_file),
            "--skip-preflight",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(state_file, encoding="utf-8")
    assert parser.get("ubersmith_installer", "app_mysql_version") == "5.7"


def test_install_appliance_dry_run_skips_docker_calls(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    mocks = _patch_install_side_effects(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "install-appliance",
            "--appliance-home",
            str(tmp_path / "appliance"),
            *FULLY_FLAGGED_INSTALL_ARGS,
            "--skip-preflight",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    mocks["pull_images"].assert_not_called()
    mocks["compose_pull"].assert_not_called()
    mocks["compose_up"].assert_not_called()
    mocks["wait_for_containers_healthy"].assert_not_called()
    mocks["configure_uberapp_user_password"].assert_not_called()


def test_install_appliance_full_flow_invokes_docker_and_password_config(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    mocks = _patch_install_side_effects(monkeypatch)
    appliance_home = tmp_path / "appliance"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "install-appliance",
            "--appliance-home",
            str(appliance_home),
            *FULLY_FLAGGED_INSTALL_ARGS,
            "--skip-preflight",
        ],
    )

    assert result.exit_code == 0, result.output

    mocks["pull_images"].assert_called_once()
    (image_refs,), _ = mocks["pull_images"].call_args
    assert any("appliance_db_ps80:5.1.4-r3" in ref for ref in image_refs)
    assert any(ref.endswith("appliance:5.1.4-r3") for ref in image_refs)
    assert any("appliance_cron:5.1.4-r3" in ref for ref in image_refs)
    assert len(image_refs) == 3

    mocks["compose_pull"].assert_called_once_with(appliance_home)
    mocks["compose_up"].assert_called_once_with(appliance_home)
    mocks["wait_for_containers_healthy"].assert_called_once_with()

    mocks["configure_uberapp_user_password"].assert_called_once()
    args, _ = mocks["configure_uberapp_user_password"].call_args
    assert len(args) == 2

    assert "password:" in result.output


def test_install_appliance_unsupported_major_version_errors(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "install-appliance",
            "--appliance-home",
            str(tmp_path / "appliance"),
            "--ubersmith-major-version",
            "6",
            "--app-virtual-host",
            "appliance.example.com",
            "--skip-preflight",
            "--dry-run",
        ],
    )
    assert result.exit_code != 0
    assert "Unsupported ubersmith_major_version" in result.output


def test_install_appliance_non_interactive_aborts_when_values_missing(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "install-appliance",
            "--appliance-home",
            str(tmp_path / "appliance"),
            "--non-interactive",
            "--skip-preflight",
            "--dry-run",
        ],
    )
    assert result.exit_code == 2
    assert "--non-interactive" in result.output
    assert "--app-virtual-host" in result.output


def test_install_appliance_help_invokable():
    runner = CliRunner()
    result = runner.invoke(main, ["install-appliance", "--help"])
    assert result.exit_code == 0
    assert "--ubersmith-major-version" in result.output
    assert "--appliance-home" in result.output
    assert "--app-virtual-host" in result.output
    assert "--dry-run" in result.output


# --- upgrade-appliance --------------------------------------------------------


def test_upgrade_appliance_fails_when_state_missing(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    state_file = tmp_path / "state.ini"

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["upgrade-appliance", "--state-file", str(state_file), "--skip-preflight"],
        input="\n",
    )

    assert result.exit_code != 0
    assert "Installer configuration is not present" in result.output


def test_upgrade_appliance_local_database_full_flow(tmp_path, monkeypatch):
    home = _patch_home(monkeypatch, tmp_path)
    appliance_home = tmp_path / "appliance"
    appliance_home.mkdir(parents=True)
    (appliance_home / "docker-compose.override.yml").write_text(
        "services:\n"
        "  app_web:\n"
        "    volumes:\n"
        '      - "' + str(appliance_home) + '/conf/ssl/appliance.example.com.key:'
        '/var/www/appliance_root/conf/ssl/appliance.key"\n'
    )
    _seed_password_files(home, "appliance.example.com")
    state_file = tmp_path / "state.ini"
    _write_appliance_state(
        state_file,
        appliance_home=appliance_home,
        app_mysql_version="5.6",
        appliance_installed_version="4.6.3",
    )

    mocks = _patch_upgrade_side_effects(monkeypatch, is_local_database=True)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["upgrade-appliance", "--state-file", str(state_file), "--skip-preflight"],
        input="\n",
    )

    assert result.exit_code == 0, result.output

    # docker-compose.yml re-rendered.
    assert (appliance_home / "docker-compose.yml").exists()

    # docker-compose.override.yml only got its narrow legacy fixup applied
    # (the http vhost line was already present), NOT wholesale re-rendered.
    override_content = (appliance_home / "docker-compose.override.yml").read_text()
    assert "MYSQL_ROOT_PASSWORD" not in override_content

    # percona cnf NEVER re-rendered on upgrade (install-only template).
    assert not (appliance_home / "conf" / "mysql" / "ubersmith.cnf").exists()

    mocks["pull_images"].assert_called_once()
    mocks["compose_pull"].assert_called_once_with(appliance_home)

    # compose_up call 0: the pre-upgrade failsafe.
    failsafe_args, failsafe_kwargs = mocks["compose_up"].call_args_list[0]
    assert failsafe_args[0] == appliance_home
    assert failsafe_kwargs["services"] == ["app_web", "app_db", "app_cron"]

    # compose_up call 1: full service list (default, no `services` kwarg override).
    second_args, second_kwargs = mocks["compose_up"].call_args_list[1]
    assert second_args[0] == appliance_home
    assert "services" not in second_kwargs

    mocks["stop_containers"].assert_called_once_with(appliance_home)
    mocks["get_existing_volumes"].assert_called_once_with(appliance_home)
    mocks["remove_webroot_volume_if_present"].assert_called_once_with(
        appliance_home, ["ubersmith_app_webroot"]
    )
    mocks["chown_database_files"].assert_called_once()
    mocks["step_up_mysql_57"].assert_called_once()
    step_up_args, _ = mocks["step_up_mysql_57"].call_args
    assert step_up_args[3] == "5.6"
    assert step_up_args[4] is True

    mocks["wait_for_containers_healthy"].assert_called_once_with()
    mocks["run_upgrade_php"].assert_called_once_with(appliance_home)
    mocks["prune_old_images"].assert_called_once()

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(state_file, encoding="utf-8")
    assert parser.get("ubersmith_installer", "appliance_installed_version") == "5.1.4"
    assert parser.get("ubersmith_installer", "app_mysql_version") == "8.0"
    assert parser.get("ubersmith_installer", "appliance_home") == str(appliance_home)
    assert parser.get("ubersmith_installer", "app_virtual_host") == "appliance.example.com"

    assert "Ubersmith Appliance upgrade complete" in result.output


def test_upgrade_appliance_remote_database_skips_local_only_steps(tmp_path, monkeypatch):
    home = _patch_home(monkeypatch, tmp_path)
    appliance_home = tmp_path / "appliance"
    appliance_home.mkdir(parents=True)
    (appliance_home / "docker-compose.override.yml").write_text("services:\n  app_web: {}\n")
    _seed_password_files(home, "appliance.example.com")
    state_file = tmp_path / "state.ini"
    _write_appliance_state(state_file, appliance_home=appliance_home)

    mocks = _patch_upgrade_side_effects(monkeypatch, is_local_database=False)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["upgrade-appliance", "--state-file", str(state_file), "--skip-preflight"],
        input="\n",
    )

    assert result.exit_code == 0, result.output

    mocks["chown_database_files"].assert_not_called()
    step_up_args, _ = mocks["step_up_mysql_57"].call_args
    assert step_up_args[4] is False

    # Containers are still stopped/restarted regardless of db topology.
    mocks["stop_containers"].assert_called_once()
    mocks["compose_up"].assert_called()
    mocks["run_upgrade_php"].assert_called_once()


def test_upgrade_appliance_never_reconfigures_uberapp_password(tmp_path, monkeypatch):
    """"Configure uberapp user password" / "Output appliance xml-rpc
    username and password" are tagged `password` only -- upgrade must never
    call configure_uberapp_user_password nor print the password."""
    home = _patch_home(monkeypatch, tmp_path)
    appliance_home = tmp_path / "appliance"
    appliance_home.mkdir(parents=True)
    (appliance_home / "docker-compose.override.yml").write_text("services:\n  app_web: {}\n")
    _seed_password_files(home, "appliance.example.com")
    state_file = tmp_path / "state.ini"
    _write_appliance_state(state_file, appliance_home=appliance_home)

    _patch_upgrade_side_effects(monkeypatch, is_local_database=True)
    configure_password = MagicMock()
    monkeypatch.setattr(
        "ubersmith_installer.cli.appliance_ops.configure_uberapp_user_password",
        configure_password,
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["upgrade-appliance", "--state-file", str(state_file), "--skip-preflight"],
        input="\n",
    )

    assert result.exit_code == 0, result.output
    configure_password.assert_not_called()
    assert "PLEASE NOTE" not in result.output


def test_upgrade_appliance_non_interactive_skips_blocking_prompt(tmp_path, monkeypatch):
    home = _patch_home(monkeypatch, tmp_path)
    appliance_home = tmp_path / "appliance"
    appliance_home.mkdir(parents=True)
    (appliance_home / "docker-compose.override.yml").write_text("services:\n  app_web: {}\n")
    _seed_password_files(home, "appliance.example.com")
    state_file = tmp_path / "state.ini"
    _write_appliance_state(state_file, appliance_home=appliance_home)

    _patch_upgrade_side_effects(monkeypatch, is_local_database=True)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "upgrade-appliance",
            "--state-file",
            str(state_file),
            "--skip-preflight",
            "--non-interactive",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "[info]" in result.output


def test_upgrade_appliance_help_invokable():
    runner = CliRunner()
    result = runner.invoke(main, ["upgrade-appliance", "--help"])
    assert result.exit_code == 0
    assert "--non-interactive" in result.output
    assert "--skip-preflight" in result.output
    assert "--state-file" in result.output
