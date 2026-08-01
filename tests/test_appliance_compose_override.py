"""Tests for ubersmith_installer.appliance_compose_override.

These exercise the two narrow, in-place text edits applied to an existing
(customer-owned) appliance docker-compose.override.yml during upgrade --
never a wholesale re-render of the file.
"""

from ubersmith_installer import appliance_compose_override

APPLIANCE_HOME = "/usr/local/ubersmith"
APP_VIRTUAL_HOST = "appliance.example.com"


def _anchor_line() -> str:
    return (
        f'      - "{APPLIANCE_HOME}/conf/ssl/{APP_VIRTUAL_HOST}.key:'
        '/var/www/appliance_root/conf/ssl/appliance.key"'
    )


def _target_line() -> str:
    return f'      - "{APPLIANCE_HOME}/conf/httpd/sites-enabled:/etc/apache2/sites-enabled"'


def test_update_compose_version_replaces_all_occurrences(tmp_path):
    path = tmp_path / "docker-compose.override.yml"
    path.write_text("version: '2'\nservices:\n  app_web: {}\n# version: '2' in a comment too\n")

    changed = appliance_compose_override.update_compose_version(path)

    assert changed is True
    text = path.read_text()
    assert "version: '2'" not in text
    assert text.count("version: '3'") == 2


def test_update_compose_version_noop_when_absent(tmp_path):
    path = tmp_path / "docker-compose.override.yml"
    path.write_text("services:\n  app_web: {}\n")

    changed = appliance_compose_override.update_compose_version(path)

    assert changed is False
    assert path.read_text() == "services:\n  app_web: {}\n"


def test_update_compose_version_noop_when_file_missing(tmp_path):
    path = tmp_path / "docker-compose.override.yml"
    assert appliance_compose_override.update_compose_version(path) is False


def test_ensure_http_vhost_line_inserts_after_anchor(tmp_path):
    path = tmp_path / "docker-compose.override.yml"
    path.write_text(
        "services:\n"
        "  app_web:\n"
        "    volumes:\n"
        f"{_anchor_line()}\n"
        '      - "other:/mount"\n'
    )

    changed = appliance_compose_override.ensure_http_vhost_line(
        path, APPLIANCE_HOME, APP_VIRTUAL_HOST
    )

    assert changed is True
    lines = path.read_text().splitlines()
    anchor_idx = lines.index(_anchor_line())
    assert lines[anchor_idx + 1] == _target_line()


def test_ensure_http_vhost_line_noop_when_already_present(tmp_path):
    original = (
        "services:\n"
        "  app_web:\n"
        "    volumes:\n"
        f"{_anchor_line()}\n"
        f"{_target_line()}\n"
    )
    path = tmp_path / "docker-compose.override.yml"
    path.write_text(original)

    changed = appliance_compose_override.ensure_http_vhost_line(
        path, APPLIANCE_HOME, APP_VIRTUAL_HOST
    )

    assert changed is False
    assert path.read_text() == original


def test_ensure_http_vhost_line_appends_at_end_when_anchor_missing(tmp_path):
    path = tmp_path / "docker-compose.override.yml"
    path.write_text("services:\n  app_web:\n    volumes:\n      - \"other:/mount\"\n")

    changed = appliance_compose_override.ensure_http_vhost_line(
        path, APPLIANCE_HOME, APP_VIRTUAL_HOST
    )

    assert changed is True
    lines = path.read_text().splitlines()
    assert lines[-1] == _target_line()


def test_ensure_http_vhost_line_noop_when_file_missing(tmp_path):
    path = tmp_path / "docker-compose.override.yml"
    assert (
        appliance_compose_override.ensure_http_vhost_line(path, APPLIANCE_HOME, APP_VIRTUAL_HOST)
        is False
    )


def test_apply_legacy_override_fixups_runs_both_in_order(tmp_path):
    path = tmp_path / "docker-compose.override.yml"
    path.write_text(
        "version: '2'\n"
        "services:\n"
        "  app_web:\n"
        "    volumes:\n"
        f"{_anchor_line()}\n"
    )

    changed = appliance_compose_override.apply_legacy_override_fixups(
        path, APPLIANCE_HOME, APP_VIRTUAL_HOST
    )

    assert changed is True
    text = path.read_text()
    assert "version: '3'" in text
    assert _target_line() in text


def test_apply_legacy_override_fixups_noop_when_file_missing(tmp_path):
    path = tmp_path / "docker-compose.override.yml"
    assert (
        appliance_compose_override.apply_legacy_override_fixups(
            path, APPLIANCE_HOME, APP_VIRTUAL_HOST
        )
        is False
    )
