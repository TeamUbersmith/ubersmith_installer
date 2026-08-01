"""Tests for the `ubersmith-installer install` CLI command."""

import configparser
from pathlib import Path
from unittest.mock import MagicMock

import yaml
from click.testing import CliRunner

from ubersmith_installer.cli import main

FULLY_FLAGGED_ARGS = [
    "--ubersmith-major-version",
    "5",
    "--virtual-host",
    "ubersmith.example.com",
    "--admin-email",
    "admin@example.com",
    "--lets-encrypt-certificate",
    "no",
]


def _patch_home(monkeypatch, tmp_path):
    """Point Path.home() (used for the MySQL password files) at tmp_path,
    so tests never touch the real invoking user's home directory."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def _patch_side_effecting_modules(monkeypatch):
    """Replace every Docker/subprocess/network-touching call the install
    command makes with a mock, so the full (non---dry-run) flow can be
    exercised without a real Docker daemon, systemctl, or port 80."""
    pull_images = MagicMock()
    compose_up = MagicMock()
    scale_redis = MagicMock()
    backup_mysql_keyring = MagicMock()
    stop_and_disable_mtas = MagicMock()
    request_letsencrypt_certificates = MagicMock()
    set_journald_retention = MagicMock()
    restart_systemd_journald = MagicMock()

    monkeypatch.setattr("ubersmith_installer.cli.docker_ops.pull_images", pull_images)
    monkeypatch.setattr("ubersmith_installer.cli.docker_ops.compose_up", compose_up)
    monkeypatch.setattr("ubersmith_installer.cli.docker_ops.scale_redis", scale_redis)
    monkeypatch.setattr(
        "ubersmith_installer.cli.docker_ops.backup_mysql_keyring", backup_mysql_keyring
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.mta.stop_and_disable_mtas", stop_and_disable_mtas
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.certbot.request_letsencrypt_certificates",
        request_letsencrypt_certificates,
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.system_config.set_journald_retention",
        set_journald_retention,
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.system_config.restart_systemd_journald",
        restart_systemd_journald,
    )
    return {
        "pull_images": pull_images,
        "compose_up": compose_up,
        "scale_redis": scale_redis,
        "backup_mysql_keyring": backup_mysql_keyring,
        "stop_and_disable_mtas": stop_and_disable_mtas,
        "request_letsencrypt_certificates": request_letsencrypt_certificates,
        "set_journald_retention": set_journald_retention,
        "restart_systemd_journald": restart_systemd_journald,
    }


def test_install_dry_run_renders_all_configs_and_writes_state(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    ubersmith_home = tmp_path / "ubersmith"
    state_file = tmp_path / "state.ini"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "install",
            "--ubersmith-major-version",
            "5",
            "--ubersmith-home",
            str(ubersmith_home),
            "--virtual-host",
            "ubersmith.example.com",
            "--admin-email",
            "admin@example.com",
            "--lets-encrypt-certificate",
            "no",
            "--state-file",
            str(state_file),
            "--skip-preflight",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output

    # docker-compose.yml
    compose_path = ubersmith_home / "docker-compose.yml"
    assert compose_path.exists()
    parsed = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    assert "services" in parsed
    for expected_service in ("web", "db", "php", "redis", "haproxy"):
        assert expected_service in parsed["services"]

    # .env
    assert (ubersmith_home / ".env").exists()
    assert "MAINTENANCE" in (ubersmith_home / ".env").read_text(encoding="utf-8")

    # docker-compose.override.yml
    override_path = ubersmith_home / "docker-compose.override.yml"
    assert override_path.exists()
    override_parsed = yaml.safe_load(override_path.read_text(encoding="utf-8"))
    assert override_parsed["services"]["db"]["environment"]["MYSQL_ROOT_PASSWORD"]

    # ubersmith.ini
    assert (ubersmith_home / "conf" / "php" / "ubersmith.ini").exists()

    # rwhois
    rwhois_content = (ubersmith_home / "conf" / "rwhois" / "rwhois").read_text()
    assert "ubersmith.example.com" in rwhois_content

    # percona configs
    assert (ubersmith_home / "conf" / "mysql" / "ubersmith.cnf").exists()
    assert (ubersmith_home / "conf" / "mysql" / "ubersmith_extra.cnf").exists()

    # vhost
    vhost_path = ubersmith_home / "conf" / "httpd" / "sites-enabled" / "ubersmith.example.com.conf"
    assert vhost_path.exists()
    assert "ServerName ubersmith.example.com" in vhost_path.read_text()

    # certbot deploy hooks + renewal script
    hooks_dir = ubersmith_home / "conf" / "certbot" / "etc" / "renewal-hooks" / "deploy"
    assert (hooks_dir / "ubersmith-deploy.sh").exists()
    assert (hooks_dir / "postfix-deploy.sh").exists()
    assert (ubersmith_home / "ubersmith_certbot_renew.sh").exists()

    # self-signed certs (always generated, regardless of LE answer)
    ssl_dir = ubersmith_home / "conf" / "ssl"
    assert (ssl_dir / "ubersmith.example.com.key").exists()
    assert (ssl_dir / "ubersmith.example.com.csr").exists()
    assert (ssl_dir / "ubersmith.example.com.pem").exists()

    # config directories + static helper files
    assert (ubersmith_home / "logs" / "ubersmith").is_dir()
    assert (ubersmith_home / "ubersmith_restart.sh").exists()
    assert (ubersmith_home / "ubersmith_start.sh").exists()
    assert (ubersmith_home / "conf" / "falco" / "falco_rules.local.yaml").exists()

    # mysql password files were generated under the (patched) home dir
    assert list((tmp_path / "home").glob(".ubersmith_ubersmith.example.com_*_db_pass"))

    # state file
    assert state_file.exists()
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(state_file, encoding="utf-8")
    assert parser.get("ubersmith_installer", "ubersmith_home") == str(ubersmith_home)
    assert parser.get("ubersmith_installer", "virtual_host") == "ubersmith.example.com"
    assert parser.get("ubersmith_installer", "admin_email") == "admin@example.com"
    assert parser.get("ubersmith_installer", "ubersmith_installed_version") == "5.2.2"
    assert parser.get("ubersmith_installer", "lets_encrypt_certificate") == "no"

    assert "Ubersmith install complete" in result.output
    assert "--dry-run" in result.output


def test_install_dry_run_skips_docker_mta_and_certbot(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    mocks = _patch_side_effecting_modules(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "install",
            "--ubersmith-home",
            str(tmp_path / "ubersmith"),
            *FULLY_FLAGGED_ARGS,
            "--skip-preflight",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    mocks["pull_images"].assert_not_called()
    mocks["compose_up"].assert_not_called()
    mocks["scale_redis"].assert_not_called()
    mocks["backup_mysql_keyring"].assert_not_called()
    mocks["stop_and_disable_mtas"].assert_not_called()
    mocks["request_letsencrypt_certificates"].assert_not_called()
    mocks["set_journald_retention"].assert_not_called()
    mocks["restart_systemd_journald"].assert_not_called()


def test_install_full_flow_invokes_docker_mta_and_certbot(tmp_path, monkeypatch):
    """Exercises the real (non-dry-run) flow, with every Docker/subprocess/
    network touching call replaced by a mock -- this is the integration
    check that the install command wires docker_ops/mta/certbot together
    with the right arguments."""
    _patch_home(monkeypatch, tmp_path)
    mocks = _patch_side_effecting_modules(monkeypatch)
    ubersmith_home = tmp_path / "ubersmith"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "install",
            "--ubersmith-home",
            str(ubersmith_home),
            "--ubersmith-major-version",
            "5",
            "--virtual-host",
            "ubersmith.example.com,billing.example.com",
            "--admin-email",
            "admin@example.com",
            "--lets-encrypt-certificate",
            "yes",
            "--skip-preflight",
        ],
    )

    assert result.exit_code == 0, result.output

    mocks["stop_and_disable_mtas"].assert_called_once()

    mocks["pull_images"].assert_called_once()
    (image_refs,), _ = mocks["pull_images"].call_args
    assert any("solr:5.2.2-r3" in ref for ref in image_refs)
    assert "busybox:latest" in image_refs
    assert "ghcr.io/teamubersmith/certbot:v3.2.0" in image_refs

    mocks["compose_up"].assert_called_once_with(ubersmith_home)
    mocks["scale_redis"].assert_called_once_with(ubersmith_home)
    mocks["backup_mysql_keyring"].assert_called_once_with(ubersmith_home)
    mocks["set_journald_retention"].assert_called_once()
    mocks["restart_systemd_journald"].assert_called_once()

    mocks["request_letsencrypt_certificates"].assert_called_once()
    args, kwargs = mocks["request_letsencrypt_certificates"].call_args
    assert args[0] == ["ubersmith.example.com", "billing.example.com"]
    assert args[1] == ubersmith_home
    assert args[2] == "admin@example.com"
    assert args[3] == "v3.2.0"
    assert args[4] == "yes"

    # Both vhosts got their own self-signed cert + apache vhost conf.
    ssl_dir = ubersmith_home / "conf" / "ssl"
    assert (ssl_dir / "billing.example.com.pem").exists()
    sites_enabled = ubersmith_home / "conf" / "httpd" / "sites-enabled"
    assert (sites_enabled / "billing.example.com.conf").exists()


def test_install_fails_when_preflight_fails(tmp_path, monkeypatch):
    from ubersmith_installer import preflight

    _patch_home(monkeypatch, tmp_path)

    def fake_run_preflight_checks(*args, **kwargs):
        result = preflight.PreflightResult()
        result.add_error("simulated preflight failure")
        return result

    monkeypatch.setattr(
        "ubersmith_installer.cli.preflight.run_preflight_checks",
        fake_run_preflight_checks,
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "install",
            "--ubersmith-home",
            str(tmp_path / "ubersmith"),
            *FULLY_FLAGGED_ARGS,
            "--state-file",
            str(tmp_path / "state.ini"),
        ],
    )

    assert result.exit_code != 0
    assert "simulated preflight failure" in result.output
    assert not (tmp_path / "ubersmith" / "docker-compose.yml").exists()
    assert not (tmp_path / "state.ini").exists()


def test_install_help_invokable():
    runner = CliRunner()
    result = runner.invoke(main, ["install", "--help"])
    assert result.exit_code == 0
    assert "--ubersmith-major-version" in result.output
    assert "--non-interactive" in result.output
    assert "--dry-run" in result.output


def test_install_unsupported_major_version_errors(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "install",
            "--ubersmith-home",
            str(tmp_path / "ubersmith"),
            "--ubersmith-major-version",
            "6",
            "--virtual-host",
            "ubersmith.example.com",
            "--admin-email",
            "admin@example.com",
            "--lets-encrypt-certificate",
            "no",
            "--skip-preflight",
            "--dry-run",
        ],
    )
    assert result.exit_code != 0
    assert "Unsupported ubersmith_major_version" in result.output


def test_install_non_interactive_aborts_when_values_missing(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "install",
            "--ubersmith-home",
            str(tmp_path / "ubersmith"),
            "--non-interactive",
            "--skip-preflight",
            "--dry-run",
        ],
    )
    assert result.exit_code == 2
    assert "--non-interactive" in result.output
    assert "--admin-email" in result.output


def test_install_prompts_for_all_values_when_none_supplied(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    ubersmith_home = tmp_path / "ubersmith"

    runner = CliRunner()
    # Answer every vars_prompt-equivalent question in order, accepting the
    # stock install_ubersmith.yml defaults except for ubersmith_home and
    # virtual_host (so the test doesn't touch a real /usr/local/ubersmith).
    prompt_input = "\n".join(
        [
            "",  # ubersmith_major_version -> default "5"
            str(ubersmith_home),  # ubersmith_home
            "no",  # lets_encrypt_certificate
            "ubersmith.example.com",  # virtual_host
            "",  # admin_email -> default admin@example.org
        ]
    )
    result = runner.invoke(
        main,
        ["install", "--skip-preflight", "--dry-run"],
        input=prompt_input + "\n",
    )

    assert result.exit_code == 0, result.output
    assert "Choose which version of Ubersmith to install (4 or 5)" in result.output
    assert (ubersmith_home / "docker-compose.yml").exists()


def test_install_partial_flags_only_prompts_for_missing_values(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    ubersmith_home = tmp_path / "ubersmith"

    runner = CliRunner()
    # ubersmith_home/admin_email/major_version supplied via flags: since not
    # all 5 required values were supplied, every question is still asked
    # (matching vars_prompt's "always prompts" behavior), but the
    # flag-supplied values are pre-seeded as the shown default -- pressing
    # enter keeps them. Only lets_encrypt_certificate/virtual_host (not
    # supplied via flag) need a real answer typed in.
    result = runner.invoke(
        main,
        [
            "install",
            "--ubersmith-home",
            str(ubersmith_home),
            "--ubersmith-major-version",
            "5",
            "--admin-email",
            "admin@example.com",
            "--skip-preflight",
            "--dry-run",
        ],
        input="\n\nno\nubersmith.example.com\n\n",
    )

    assert result.exit_code == 0, result.output
    assert f"[{ubersmith_home}]" in result.output
    assert "[admin@example.com]" in result.output
    assert (ubersmith_home / "docker-compose.yml").exists()


def test_install_env_file_is_not_overwritten_on_rerun(tmp_path, monkeypatch):
    """Mirrors the "Create docker compose env file" task's `force: false`:
    an existing .env is left untouched."""
    _patch_home(monkeypatch, tmp_path)
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir(parents=True)
    (ubersmith_home / ".env").write_text("MAINTENANCE=1\nCUSTOM=yes\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "install",
            "--ubersmith-home",
            str(ubersmith_home),
            *FULLY_FLAGGED_ARGS,
            "--skip-preflight",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (ubersmith_home / ".env").read_text(encoding="utf-8") == (
        "MAINTENANCE=1\nCUSTOM=yes\n"
    )
