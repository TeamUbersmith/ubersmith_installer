"""Tests for the `configure`, `retry-letsencrypt`, `add-brand`, and `patch`
CLI subcommands."""

import configparser
from pathlib import Path
from unittest.mock import MagicMock

from click.testing import CliRunner

from ubersmith_installer.cli import main
from ubersmith_installer import state as state_mod


# ---------------------------------------------------------------------------
# configure
# ---------------------------------------------------------------------------


def _make_install(tmp_path, name="ubersmith"):
    ubersmith_home = tmp_path / name
    ubersmith_home.mkdir(parents=True, exist_ok=True)
    (ubersmith_home / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    return ubersmith_home


def test_configure_full_flow_with_flags(tmp_path):
    ubersmith_home = _make_install(tmp_path)
    state_file = tmp_path / "state.ini"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "configure",
            "--ubersmith-home",
            str(ubersmith_home),
            "--virtual-host",
            "ubersmith.example.com",
            "--admin-email",
            "admin@example.com",
            "--state-file",
            str(state_file),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Ubersmith configuration updated" in result.output

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(state_file, encoding="utf-8")
    assert parser.get("ubersmith_installer", "ubersmith_home") == str(ubersmith_home)
    assert parser.get("ubersmith_installer", "virtual_host") == "ubersmith.example.com"
    assert parser.get("ubersmith_installer", "admin_email") == "admin@example.com"
    assert parser.get("ubersmith_installer", "appliance_home") == str(ubersmith_home)
    assert parser.get("ubersmith_installer", "app_virtual_host") == "ubersmith.example.com"


def test_configure_prompts_when_no_flags_supplied(tmp_path):
    ubersmith_home = _make_install(tmp_path)
    state_file = tmp_path / "state.ini"

    runner = CliRunner()
    prompt_input = "\n".join(
        [str(ubersmith_home), "ubersmith.example.com", "admin@example.com"]
    )
    result = runner.invoke(
        main,
        ["configure", "--state-file", str(state_file)],
        input=prompt_input + "\n",
    )

    assert result.exit_code == 0, result.output
    assert "Current path in use by Ubersmith / Appliance" in result.output

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(state_file, encoding="utf-8")
    assert parser.get("ubersmith_installer", "ubersmith_home") == str(ubersmith_home)


def test_configure_non_interactive_aborts_when_values_missing(tmp_path):
    state_file = tmp_path / "state.ini"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "configure",
            "--ubersmith-home",
            str(tmp_path / "ubersmith"),
            "--non-interactive",
            "--state-file",
            str(state_file),
        ],
    )

    assert result.exit_code == 2
    assert "--non-interactive" in result.output
    assert "--virtual-host" in result.output
    assert "--admin-email" in result.output


def test_configure_fails_when_path_is_not_an_existing_install(tmp_path):
    not_an_install = tmp_path / "not_an_install"
    not_an_install.mkdir()
    state_file = tmp_path / "state.ini"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "configure",
            "--ubersmith-home",
            str(not_an_install),
            "--virtual-host",
            "ubersmith.example.com",
            "--admin-email",
            "admin@example.com",
            "--state-file",
            str(state_file),
        ],
    )

    assert result.exit_code != 0
    assert "Provided path does not contain an existing Ubersmith installation" in result.output
    assert not state_file.exists()


def test_configure_help_invokable():
    runner = CliRunner()
    result = runner.invoke(main, ["configure", "--help"])
    assert result.exit_code == 0
    assert "--ubersmith-home" in result.output
    assert "--non-interactive" in result.output
    assert "--state-file" in result.output


# ---------------------------------------------------------------------------
# retry-letsencrypt
# ---------------------------------------------------------------------------


def _write_full_state(state_file, ubersmith_home, virtual_host="ubersmith.example.com,billing.example.com"):
    state_mod.write_installer_state(
        state_mod.InstallerState(
            ubersmith_home=str(ubersmith_home),
            virtual_host=virtual_host,
            admin_email="admin@example.com",
            ubersmith_installed_version="5.2.2",
        ),
        path=state_file,
    )


def test_retry_letsencrypt_fails_when_state_missing(tmp_path):
    state_file = tmp_path / "state.ini"
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["retry-letsencrypt", "--state-file", str(state_file), "--skip-preflight"],
    )

    assert result.exit_code != 0
    assert "Installer configuration is not present" in result.output


def test_retry_letsencrypt_full_flow(tmp_path, monkeypatch):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()
    state_file = tmp_path / "state.ini"
    _write_full_state(state_file, ubersmith_home)

    retry_mock = MagicMock()
    monkeypatch.setattr(
        "ubersmith_installer.cli.retry_letsencrypt_module.retry_letsencrypt", retry_mock
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["retry-letsencrypt", "--state-file", str(state_file), "--skip-preflight"],
    )

    assert result.exit_code == 0, result.output
    retry_mock.assert_called_once_with(
        ["ubersmith.example.com", "billing.example.com"],
        ubersmith_home,
        "admin@example.com",
        "v3.2.0",
    )
    assert "Let's Encrypt certificate retry complete" in result.output


def test_retry_letsencrypt_help_invokable():
    runner = CliRunner()
    result = runner.invoke(main, ["retry-letsencrypt", "--help"])
    assert result.exit_code == 0
    assert "--non-interactive" in result.output
    assert "--skip-preflight" in result.output
    assert "--state-file" in result.output


# ---------------------------------------------------------------------------
# add-brand
# ---------------------------------------------------------------------------


def test_add_brand_fails_when_state_missing(tmp_path):
    state_file = tmp_path / "state.ini"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "add-brand",
            "--new-virtual-host",
            "newbrand.example.com",
            "--state-file",
            str(state_file),
            "--skip-preflight",
        ],
    )

    assert result.exit_code != 0
    assert "Installer configuration is not present" in result.output


def test_add_brand_non_interactive_aborts_when_new_host_missing(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()
    state_file = tmp_path / "state.ini"
    _write_full_state(state_file, ubersmith_home, virtual_host="ubersmith.example.com")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "add-brand",
            "--non-interactive",
            "--state-file",
            str(state_file),
            "--skip-preflight",
        ],
    )

    assert result.exit_code == 2
    assert "--new-virtual-host" in result.output


def test_add_brand_full_flow(tmp_path, monkeypatch):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()
    state_file = tmp_path / "state.ini"
    _write_full_state(state_file, ubersmith_home, virtual_host="ubersmith.example.com")

    retry_mock = MagicMock()
    monkeypatch.setattr(
        "ubersmith_installer.cli.retry_letsencrypt_module.retry_letsencrypt", retry_mock
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "add-brand",
            "--new-virtual-host",
            "billing.example.com",
            "--state-file",
            str(state_file),
            "--skip-preflight",
        ],
    )

    assert result.exit_code == 0, result.output

    # Self-signed cert + vhost config generated ONLY for the new host.
    ssl_dir = ubersmith_home / "conf" / "ssl"
    assert (ssl_dir / "billing.example.com.pem").exists()
    assert not (ssl_dir / "ubersmith.example.com.pem").exists()

    sites_enabled = ubersmith_home / "conf" / "httpd" / "sites-enabled"
    assert (sites_enabled / "billing.example.com.conf").exists()
    assert not (sites_enabled / "ubersmith.example.com.conf").exists()

    # State updated with the combined host list.
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(state_file, encoding="utf-8")
    assert parser.get("ubersmith_installer", "virtual_host") == (
        "ubersmith.example.com,billing.example.com"
    )
    assert parser.get("ubersmith_installer", "app_virtual_host") == (
        "ubersmith.example.com,billing.example.com"
    )

    # retry_letsencrypt called with the FULL combined host list.
    retry_mock.assert_called_once_with(
        ["ubersmith.example.com", "billing.example.com"],
        ubersmith_home,
        "admin@example.com",
        "v3.2.0",
    )

    assert "Brand added" in result.output


def test_add_brand_prompts_when_no_flag_supplied(tmp_path, monkeypatch):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()
    state_file = tmp_path / "state.ini"
    _write_full_state(state_file, ubersmith_home, virtual_host="ubersmith.example.com")

    monkeypatch.setattr(
        "ubersmith_installer.cli.retry_letsencrypt_module.retry_letsencrypt", MagicMock()
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["add-brand", "--state-file", str(state_file), "--skip-preflight"],
        input="billing.example.com\n",
    )

    assert result.exit_code == 0, result.output
    assert "Enter the hostname(s) for the new brand" in result.output


def test_add_brand_help_invokable():
    runner = CliRunner()
    result = runner.invoke(main, ["add-brand", "--help"])
    assert result.exit_code == 0
    assert "--new-virtual-host" in result.output
    assert "--non-interactive" in result.output
    assert "--state-file" in result.output


# ---------------------------------------------------------------------------
# patch
# ---------------------------------------------------------------------------


def test_patch_fails_when_state_missing(tmp_path):
    state_file = tmp_path / "state.ini"
    runner = CliRunner()
    result = runner.invoke(main, ["patch", "--state-file", str(state_file)])

    assert result.exit_code != 0
    assert "Installer configuration is not present" in result.output


def test_patch_fails_when_patches_not_supported(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()
    (ubersmith_home / "docker-compose.override.yml").write_text(
        "services:\n  web: {}\n", encoding="utf-8"
    )
    state_file = tmp_path / "state.ini"
    state_mod.write_installer_state(
        state_mod.InstallerState(
            ubersmith_home=str(ubersmith_home),
            ubersmith_installed_version="5.2.2",
        ),
        path=state_file,
    )

    runner = CliRunner()
    result = runner.invoke(main, ["patch", "--state-file", str(state_file)])

    assert result.exit_code != 0
    assert "Ubersmith is not currently configured to accept patches" in result.output
    assert "support@ubersmith.com" in result.output


def test_patch_non_interactive_aborts_when_patch_id_missing(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()
    (ubersmith_home / "docker-compose.override.yml").write_text(
        "services:\n  web:\n    volumes:\n"
        "      - ./app/patches:/var/www/ubersmith_root/app/patches\n",
        encoding="utf-8",
    )
    state_file = tmp_path / "state.ini"
    state_mod.write_installer_state(
        state_mod.InstallerState(
            ubersmith_home=str(ubersmith_home),
            ubersmith_installed_version="5.2.2",
        ),
        path=state_file,
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["patch", "--state-file", str(state_file), "--non-interactive"],
    )

    assert result.exit_code == 2
    assert "--patch-id" in result.output


def test_patch_full_flow_with_patch_id_flag(tmp_path, monkeypatch):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()
    (ubersmith_home / "docker-compose.override.yml").write_text(
        "services:\n  web:\n    volumes:\n"
        "      - ./app/patches:/var/www/ubersmith_root/app/patches\n",
        encoding="utf-8",
    )
    state_file = tmp_path / "state.ini"
    state_mod.write_installer_state(
        state_mod.InstallerState(
            ubersmith_home=str(ubersmith_home),
            ubersmith_installed_version="5.2.2",
        ),
        path=state_file,
    )

    patches = [
        {
            "id": 42,
            "name": "Patch for 5.2.2 - hotfix 1",
            "html_url": "https://github.com/TeamUbersmith/ubersmith-patches/releases/42",
            "asset_url": "https://example.com/releases/download/patch.tar.gz",
        }
    ]
    list_patches_mock = MagicMock(return_value=patches)
    download_mock = MagicMock(return_value=["fix.php"])
    apply_mock = MagicMock()
    record_mock = MagicMock()

    monkeypatch.setattr(
        "ubersmith_installer.cli.patch_apply.list_available_patches", list_patches_mock
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.patch_apply.download_and_unpack_patch", download_mock
    )
    monkeypatch.setattr("ubersmith_installer.cli.patch_apply.apply_patch", apply_mock)
    monkeypatch.setattr(
        "ubersmith_installer.cli.patch_apply.record_patch_metadata", record_mock
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["patch", "--state-file", str(state_file), "--patch-id", "42"],
    )

    assert result.exit_code == 0, result.output

    list_patches_mock.assert_called_once_with("5.2.2")
    download_mock.assert_called_once_with(
        "42", "https://example.com/releases/download/patch.tar.gz", ubersmith_home
    )
    apply_mock.assert_called_once_with(ubersmith_home, "42")
    record_mock.assert_called_once()
    args, kwargs = record_mock.call_args
    assert args[0] == ubersmith_home
    assert args[1] == "42"
    assert kwargs["github_page"] == (
        "https://github.com/TeamUbersmith/ubersmith-patches/releases/42"
    )

    assert "Ubersmith patch applied" in result.output


def test_patch_prompts_for_patch_id_when_not_supplied(tmp_path, monkeypatch):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()
    (ubersmith_home / "docker-compose.override.yml").write_text(
        "services:\n  web:\n    volumes:\n"
        "      - ./app/patches:/var/www/ubersmith_root/app/patches\n",
        encoding="utf-8",
    )
    state_file = tmp_path / "state.ini"
    state_mod.write_installer_state(
        state_mod.InstallerState(
            ubersmith_home=str(ubersmith_home),
            ubersmith_installed_version="5.2.2",
        ),
        path=state_file,
    )

    patches = [
        {
            "id": 42,
            "name": "Patch for 5.2.2 - hotfix 1",
            "html_url": "https://github.com/TeamUbersmith/ubersmith-patches/releases/42",
            "asset_url": "https://example.com/releases/download/patch.tar.gz",
        }
    ]
    monkeypatch.setattr(
        "ubersmith_installer.cli.patch_apply.list_available_patches",
        MagicMock(return_value=patches),
    )
    monkeypatch.setattr(
        "ubersmith_installer.cli.patch_apply.download_and_unpack_patch",
        MagicMock(return_value=["fix.php"]),
    )
    monkeypatch.setattr("ubersmith_installer.cli.patch_apply.apply_patch", MagicMock())
    monkeypatch.setattr(
        "ubersmith_installer.cli.patch_apply.record_patch_metadata", MagicMock()
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["patch", "--state-file", str(state_file)],
        input="42\n",
    )

    assert result.exit_code == 0, result.output
    assert "Available Patches for Ubersmith 5.2.2" in result.output
    assert "Enter the patch ID to apply" in result.output


def test_patch_fails_when_selected_patch_id_not_available(tmp_path, monkeypatch):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()
    (ubersmith_home / "docker-compose.override.yml").write_text(
        "services:\n  web:\n    volumes:\n"
        "      - ./app/patches:/var/www/ubersmith_root/app/patches\n",
        encoding="utf-8",
    )
    state_file = tmp_path / "state.ini"
    state_mod.write_installer_state(
        state_mod.InstallerState(
            ubersmith_home=str(ubersmith_home),
            ubersmith_installed_version="5.2.2",
        ),
        path=state_file,
    )

    monkeypatch.setattr(
        "ubersmith_installer.cli.patch_apply.list_available_patches",
        MagicMock(return_value=[]),
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["patch", "--state-file", str(state_file), "--patch-id", "999"],
    )

    assert result.exit_code != 0
    assert "not among the available patches" in result.output


def test_patch_help_invokable():
    runner = CliRunner()
    result = runner.invoke(main, ["patch", "--help"])
    assert result.exit_code == 0
    assert "--patch-id" in result.output
    assert "--non-interactive" in result.output
    assert "--state-file" in result.output
