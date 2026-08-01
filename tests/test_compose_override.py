"""Tests for ubersmith_installer.compose_override.

These exercise the three narrow, in-place text edits applied to an
existing (customer-owned) docker-compose.override.yml during upgrade --
never a wholesale re-render of the file.
"""

from ubersmith_installer import compose_override

OLD_PHP_VERSIONS = ["5.6", "7.1", "7.3", "8.2"]
NEW_PHP_VERSION = "8.4"

FAKE_OVERRIDE = """version: '3'
services:
  php:
    volumes:
      - /etc/php/8.2/conf.d:/usr/local/etc/php/conf.d
      - /etc/php/7.1/legacy:/usr/local/etc/php/legacy
    ports:
      - "127.0.0.1:9999:9999"
  apache:
    volumes:
      - /etc/apache2/sites-enabled:/usr/local/apache2/conf/sites-enabled-custom
    environment:
      CUSTOM_ADMIN_FLAG: "true"
"""

FIXED_OVERRIDE = """services:
  php:
    volumes:
      - /etc/php/8.4/conf.d:/usr/local/etc/php/conf.d
      - /etc/php/8.4/legacy:/usr/local/etc/php/legacy
    ports:
      - "127.0.0.1:9999:9999"
  apache:
    volumes:
      - /usr/local/apache2/conf/sites-enabled:/usr/local/apache2/conf/sites-enabled-custom
    environment:
      CUSTOM_ADMIN_FLAG: "true"
"""


def _write_fake_override(tmp_path):
    override_path = tmp_path / "docker-compose.override.yml"
    override_path.write_text(FAKE_OVERRIDE)
    return override_path


def test_remove_version_line_removes_first_match_only(tmp_path):
    override_path = tmp_path / "docker-compose.override.yml"
    override_path.write_text("version: '3'\nversion: '2'\nother: stuff\n")

    changed = compose_override.remove_version_line(override_path)

    assert changed is True
    text = override_path.read_text()
    assert "version: '3'" not in text
    # firstmatch: true -- only the first occurrence is removed.
    assert "version: '2'" in text
    assert "other: stuff" in text


def test_remove_version_line_no_op_when_absent(tmp_path):
    override_path = tmp_path / "docker-compose.override.yml"
    override_path.write_text("other: stuff\n")

    changed = compose_override.remove_version_line(override_path)

    assert changed is False
    assert override_path.read_text() == "other: stuff\n"


def test_update_php_version_paths_replaces_all_old_versions(tmp_path):
    override_path = _write_fake_override(tmp_path)

    changed = compose_override.update_php_version_paths(
        override_path, OLD_PHP_VERSIONS, NEW_PHP_VERSION
    )

    assert changed is True
    text = override_path.read_text()
    assert "/etc/php/8.2" not in text
    assert "/etc/php/7.1" not in text
    assert "/etc/php/8.4/conf.d" in text
    assert "/etc/php/8.4/legacy" in text


def test_update_apache_vhost_path_replaces_old_path(tmp_path):
    override_path = _write_fake_override(tmp_path)

    changed = compose_override.update_apache_vhost_path(override_path)

    assert changed is True
    text = override_path.read_text()
    assert "/etc/apache2/sites-enabled" not in text
    assert (
        "/usr/local/apache2/conf/sites-enabled:/usr/local/apache2/conf/sites-enabled-custom"
        in text
    )


def test_apply_legacy_override_fixups_applies_all_three_and_preserves_custom_content(
    tmp_path,
):
    override_path = _write_fake_override(tmp_path)

    changed = compose_override.apply_legacy_override_fixups(
        override_path, OLD_PHP_VERSIONS, NEW_PHP_VERSION
    )

    assert changed is True
    text = override_path.read_text()
    assert text == FIXED_OVERRIDE

    # Unrelated, customer-added content survives byte-for-byte.
    assert '"127.0.0.1:9999:9999"' in text
    assert 'CUSTOM_ADMIN_FLAG: "true"' in text


def test_apply_legacy_override_fixups_is_idempotent(tmp_path):
    override_path = _write_fake_override(tmp_path)

    first_pass = compose_override.apply_legacy_override_fixups(
        override_path, OLD_PHP_VERSIONS, NEW_PHP_VERSION
    )
    text_after_first = override_path.read_text()

    second_pass = compose_override.apply_legacy_override_fixups(
        override_path, OLD_PHP_VERSIONS, NEW_PHP_VERSION
    )
    text_after_second = override_path.read_text()

    assert first_pass is True
    assert second_pass is False
    assert text_after_first == text_after_second == FIXED_OVERRIDE


def test_missing_file_is_a_no_op_for_all_functions(tmp_path):
    override_path = tmp_path / "does-not-exist.yml"

    assert compose_override.remove_version_line(override_path) is False
    assert (
        compose_override.update_php_version_paths(
            override_path, OLD_PHP_VERSIONS, NEW_PHP_VERSION
        )
        is False
    )
    assert compose_override.update_apache_vhost_path(override_path) is False
    assert (
        compose_override.apply_legacy_override_fixups(
            override_path, OLD_PHP_VERSIONS, NEW_PHP_VERSION
        )
        is False
    )
    assert not override_path.exists()
