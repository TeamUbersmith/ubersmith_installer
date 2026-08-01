"""Tests for the `ubersmith-installer install` CLI command."""

import configparser

import yaml
from click.testing import CliRunner

from ubersmith_installer.cli import main


def test_install_dry_run_renders_configs_and_writes_state(tmp_path):
    output_dir = tmp_path / "output"
    state_file = tmp_path / "state.ini"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "install",
            "--ubersmith-major-version",
            "5",
            "--ubersmith-home",
            "/opt/ubersmith",
            "--virtual-host",
            "ubersmith.example.com",
            "--admin-email",
            "admin@example.com",
            "--output-dir",
            str(output_dir),
            "--state-file",
            str(state_file),
            "--skip-preflight",
        ],
    )

    assert result.exit_code == 0, result.output

    compose_path = output_dir / "docker-compose.yml"
    assert compose_path.exists()
    parsed = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    assert "services" in parsed
    for expected_service in ("web", "db", "php", "redis", "haproxy"):
        assert expected_service in parsed["services"]

    assert (output_dir / ".env").exists()
    assert "MAINTENANCE" in (output_dir / ".env").read_text(encoding="utf-8")

    assert (output_dir / "ubersmith.ini").exists()

    assert state_file.exists()
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(state_file, encoding="utf-8")
    assert parser.get("ubersmith_installer", "ubersmith_home") == "/opt/ubersmith"
    assert parser.get("ubersmith_installer", "virtual_host") == "ubersmith.example.com"
    assert parser.get("ubersmith_installer", "admin_email") == "admin@example.com"
    assert parser.get("ubersmith_installer", "ubersmith_installed_version") == "5.2.2"

    assert "Dry run complete" in result.output


def test_install_fails_when_preflight_fails(tmp_path, monkeypatch):
    from ubersmith_installer import preflight

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
            "--output-dir",
            str(tmp_path / "output"),
            "--state-file",
            str(tmp_path / "state.ini"),
        ],
    )

    assert result.exit_code != 0
    assert "simulated preflight failure" in result.output
    assert not (tmp_path / "output" / "docker-compose.yml").exists()


def test_install_help_invokable():
    runner = CliRunner()
    result = runner.invoke(main, ["install", "--help"])
    assert result.exit_code == 0
    assert "--ubersmith-major-version" in result.output
