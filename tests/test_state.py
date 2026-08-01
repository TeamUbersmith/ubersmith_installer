"""Tests for ubersmith_installer.state."""

import configparser

from ubersmith_installer import state


def test_read_missing_file_returns_defaults(tmp_path):
    ini_path = tmp_path / "does_not_exist.ini"

    result = state.read_state(ini_path)

    assert result == state.InstallerState()
    assert result.ubersmith_home is None
    assert result.virtual_host is None
    assert result.admin_email is None
    assert result.ubersmith_installed_version is None
    assert result.lets_encrypt_certificate is None
    assert result.appliance_home is None
    assert result.app_virtual_host is None
    assert result.app_mysql_version is None
    assert result.appliance_installed_version is None


def test_round_trip_read(tmp_path):
    ini_path = tmp_path / "state.ini"
    ini_path.write_text(
        "[ubersmith_installer]\n"
        "ubersmith_home = /var/www/ubersmith_root\n"
        "virtual_host = ubersmith.example.com\n"
        "admin_email = admin@example.com\n"
        "ubersmith_installed_version = 5.2.2\n"
        "lets_encrypt_certificate = yes\n"
        "appliance_home = /var/www/appliance_root\n"
        "app_virtual_host = appliance.example.com\n"
        "app_mysql_version = 8.0\n"
        "appliance_installed_version = 5.2.2\n",
        encoding="utf-8",
    )

    result = state.read_state(ini_path)

    assert result.ubersmith_home == "/var/www/ubersmith_root"
    assert result.virtual_host == "ubersmith.example.com"
    assert result.admin_email == "admin@example.com"
    assert result.ubersmith_installed_version == "5.2.2"
    assert result.lets_encrypt_certificate == "yes"
    assert result.appliance_home == "/var/www/appliance_root"
    assert result.app_virtual_host == "appliance.example.com"
    assert result.app_mysql_version == "8.0"
    assert result.appliance_installed_version == "5.2.2"


def test_read_partial_file_defaults_missing_keys(tmp_path):
    ini_path = tmp_path / "state.ini"
    ini_path.write_text(
        "[ubersmith_installer]\nubersmith_home = /var/www/ubersmith_root\n",
        encoding="utf-8",
    )

    result = state.read_state(ini_path)

    assert result.ubersmith_home == "/var/www/ubersmith_root"
    assert result.virtual_host is None
    assert result.admin_email is None


def test_write_creates_missing_file(tmp_path):
    ini_path = tmp_path / "new" / "state.ini"

    state.write_state(
        {"ubersmith_home": "/var/www/ubersmith_root", "virtual_host": "ubersmith.example.com"},
        path=ini_path,
    )

    assert ini_path.exists()
    result = state.read_state(ini_path)
    assert result.ubersmith_home == "/var/www/ubersmith_root"
    assert result.virtual_host == "ubersmith.example.com"


def test_write_then_read_round_trip(tmp_path):
    ini_path = tmp_path / "state.ini"

    original = state.InstallerState(
        ubersmith_home="/var/www/ubersmith_root",
        virtual_host="ubersmith.example.com",
        admin_email="admin@example.com",
        ubersmith_installed_version="5.2.2",
        lets_encrypt_certificate="yes",
    )
    state.write_installer_state(original, path=ini_path)

    result = state.read_state(ini_path)

    assert result.ubersmith_home == original.ubersmith_home
    assert result.virtual_host == original.virtual_host
    assert result.admin_email == original.admin_email
    assert result.ubersmith_installed_version == original.ubersmith_installed_version
    assert result.lets_encrypt_certificate == original.lets_encrypt_certificate
    # Fields never set should remain unwritten/None.
    assert result.appliance_home is None


def test_write_preserves_unknown_keys_and_sections(tmp_path):
    ini_path = tmp_path / "state.ini"
    ini_path.write_text(
        "[ubersmith_installer]\n"
        "ubersmith_home = /var/www/ubersmith_root\n"
        "some_future_key = keep-me\n"
        "\n"
        "[other_section]\n"
        "unrelated_option = do-not-touch\n",
        encoding="utf-8",
    )

    state.write_state({"virtual_host": "ubersmith.example.com"}, path=ini_path)

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ini_path, encoding="utf-8")

    # The newly written option is present.
    assert parser.get("ubersmith_installer", "virtual_host") == "ubersmith.example.com"
    # Pre-existing options in the known section are preserved.
    assert parser.get("ubersmith_installer", "ubersmith_home") == "/var/www/ubersmith_root"
    assert parser.get("ubersmith_installer", "some_future_key") == "keep-me"
    # An entirely unrelated section is preserved untouched.
    assert parser.get("other_section", "unrelated_option") == "do-not-touch"


def test_write_none_values_are_not_written(tmp_path):
    ini_path = tmp_path / "state.ini"
    ini_path.write_text(
        "[ubersmith_installer]\nadmin_email = admin@example.com\n",
        encoding="utf-8",
    )

    state.write_state({"admin_email": None, "virtual_host": "ubersmith.example.com"}, path=ini_path)

    result = state.read_state(ini_path)
    assert result.admin_email == "admin@example.com"
    assert result.virtual_host == "ubersmith.example.com"


def test_read_raw_exposes_unknown_sections(tmp_path):
    ini_path = tmp_path / "state.ini"
    ini_path.write_text(
        "[ubersmith_installer]\nubersmith_home = /var/www/ubersmith_root\n"
        "\n[other_section]\nfoo = bar\n",
        encoding="utf-8",
    )

    parser = state.read_raw(ini_path)

    assert parser.get("ubersmith_installer", "ubersmith_home") == "/var/www/ubersmith_root"
    assert parser.get("other_section", "foo") == "bar"


def test_read_raw_missing_file_returns_empty_parser(tmp_path):
    ini_path = tmp_path / "does_not_exist.ini"

    parser = state.read_raw(ini_path)

    assert parser.sections() == []
