"""Tests for ubersmith_installer.configure_state."""

import pytest

from ubersmith_installer import configure_state, state


def _make_install(tmp_path):
    ubersmith_home = tmp_path / "ubersmith_root"
    ubersmith_home.mkdir()
    (ubersmith_home / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    return ubersmith_home


def test_verify_existing_install_true_when_compose_file_present(tmp_path):
    ubersmith_home = _make_install(tmp_path)

    assert configure_state.verify_existing_install(ubersmith_home) is True


def test_verify_existing_install_false_when_missing(tmp_path):
    ubersmith_home = tmp_path / "not_an_install"
    ubersmith_home.mkdir()

    assert configure_state.verify_existing_install(ubersmith_home) is False


def test_verify_existing_install_false_when_dir_missing(tmp_path):
    ubersmith_home = tmp_path / "does_not_exist_at_all"

    assert configure_state.verify_existing_install(ubersmith_home) is False


def test_reconfigure_raises_when_no_existing_install(tmp_path):
    ubersmith_home = tmp_path / "not_an_install"
    ubersmith_home.mkdir()
    ini_path = tmp_path / "state.ini"

    with pytest.raises(ValueError, match=configure_state.NOT_INSTALLED_MSG):
        configure_state.reconfigure(
            str(ubersmith_home),
            "ubersmith.example.com",
            "admin@example.com",
            state_file=ini_path,
        )

    assert not ini_path.exists()


def test_reconfigure_writes_all_five_keys(tmp_path):
    ubersmith_home = _make_install(tmp_path)
    ini_path = tmp_path / "state.ini"

    configure_state.reconfigure(
        str(ubersmith_home),
        "ubersmith.example.com",
        "admin@example.com",
        state_file=ini_path,
    )

    result = state.read_state(ini_path)
    assert result.ubersmith_home == str(ubersmith_home)
    assert result.virtual_host == "ubersmith.example.com"
    assert result.admin_email == "admin@example.com"
    assert result.appliance_home == str(ubersmith_home)
    assert result.app_virtual_host == "ubersmith.example.com"


def test_reconfigure_preserves_other_existing_keys(tmp_path):
    ubersmith_home = _make_install(tmp_path)
    ini_path = tmp_path / "state.ini"
    state.write_state(
        {
            "ubersmith_installed_version": "5.2.2",
            "lets_encrypt_certificate": "yes",
            "app_mysql_version": "8.0",
            "appliance_installed_version": "5.2.2",
        },
        path=ini_path,
    )

    configure_state.reconfigure(
        str(ubersmith_home),
        "new.example.com",
        "newadmin@example.com",
        state_file=ini_path,
    )

    result = state.read_state(ini_path)
    assert result.ubersmith_home == str(ubersmith_home)
    assert result.virtual_host == "new.example.com"
    assert result.admin_email == "newadmin@example.com"
    assert result.appliance_home == str(ubersmith_home)
    assert result.app_virtual_host == "new.example.com"
    # pre-existing unrelated keys are preserved
    assert result.ubersmith_installed_version == "5.2.2"
    assert result.lets_encrypt_certificate == "yes"
    assert result.app_mysql_version == "8.0"
    assert result.appliance_installed_version == "5.2.2"


def test_reconfigure_uses_default_state_file_when_not_given(tmp_path, monkeypatch):
    ubersmith_home = _make_install(tmp_path)
    default_ini = tmp_path / "default_state.ini"
    monkeypatch.setattr(configure_state.state_module, "DEFAULT_STATE_PATH", default_ini)

    configure_state.reconfigure(
        str(ubersmith_home),
        "ubersmith.example.com",
        "admin@example.com",
    )

    assert default_ini.exists()
    result = state.read_state(default_ini)
    assert result.ubersmith_home == str(ubersmith_home)
