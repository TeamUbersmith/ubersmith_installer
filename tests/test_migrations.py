"""Tests for ubersmith_installer.migrations.

These exercise the upgrade-only migration logic with a mocked subprocess
runner -- no real Docker daemon is required.
"""

from pathlib import Path

import pytest

from ubersmith_installer import migrations


def test_caching_sha2_password_runs_for_old_local_database(tmp_path):
    calls = []

    def runner(cmd, cwd, env):
        calls.append((cmd, cwd, env))

    ran = migrations.migrate_caching_sha2_password(
        tmp_path,
        mysql_root_password="rootpw",
        mysql_password="ubersmithpw",
        installed_version="5.1.9",
        is_local_database=True,
        runner=runner,
    )

    assert ran is True
    assert len(calls) == 1
    cmd, cwd, env = calls[0]
    assert cmd == [
        "docker",
        "compose",
        "exec",
        "db",
        "sh",
        "-c",
        "mysql -u root -p$MYSQL_ROOT_PASSWORD -e \"ALTER USER "
        "'ubersmith'@'%' IDENTIFIED WITH 'caching_sha2_password' BY "
        "'$MYSQL_PASSWORD';\"",
    ]
    assert cwd == Path(tmp_path)
    assert env["MYSQL_ROOT_PASSWORD"] == "rootpw"
    assert env["MYSQL_PASSWORD"] == "ubersmithpw"


@pytest.mark.parametrize("installed_version", ["5.2.0", "5.2.1", "6.0.0"])
def test_caching_sha2_password_skipped_when_already_upgraded(
    tmp_path, installed_version
):
    calls = []

    def runner(cmd, cwd, env):
        calls.append(cmd)

    ran = migrations.migrate_caching_sha2_password(
        tmp_path,
        mysql_root_password="rootpw",
        mysql_password="ubersmithpw",
        installed_version=installed_version,
        is_local_database=True,
        runner=runner,
    )

    assert ran is False
    assert calls == []


def test_caching_sha2_password_skipped_for_remote_database(tmp_path):
    calls = []

    def runner(cmd, cwd, env):
        calls.append(cmd)

    ran = migrations.migrate_caching_sha2_password(
        tmp_path,
        mysql_root_password="rootpw",
        mysql_password="ubersmithpw",
        installed_version="5.0.0",
        is_local_database=False,
        runner=runner,
    )

    assert ran is False
    assert calls == []


@pytest.mark.parametrize(
    "installed_version,expect_message",
    [
        ("4.3.0", False),
        ("4.2.9", False),
        ("4.0.0", False),
        ("4.3.1", True),
        ("4.6.4", True),
        ("5.0.0", True),
    ],
)
def test_license_update_reminder(installed_version, expect_message):
    message = migrations.license_update_reminder(installed_version)

    if expect_message:
        assert message == migrations.LICENSE_UPDATE_REMINDER
        assert "support@ubersmith.com" in message
    else:
        assert message is None


def test_run_migrations_includes_caching_sha2_password_when_applicable(tmp_path):
    calls = []

    def runner(cmd, cwd, env):
        calls.append(cmd)

    ran = migrations.run_migrations(
        tmp_path,
        mysql_root_password="rootpw",
        mysql_password="ubersmithpw",
        installed_version="5.0.0",
        is_local_database=True,
        runner=runner,
    )

    assert ran == ["caching_sha2_password"]
    assert len(calls) == 1


@pytest.mark.parametrize(
    "installed_version,is_local_database",
    [
        ("5.2.0", True),
        ("5.3.0", True),
        ("5.0.0", False),
        ("4.0.0", False),
    ],
)
def test_run_migrations_empty_when_not_applicable(
    tmp_path, installed_version, is_local_database
):
    calls = []

    def runner(cmd, cwd, env):
        calls.append(cmd)

    ran = migrations.run_migrations(
        tmp_path,
        mysql_root_password="rootpw",
        mysql_password="ubersmithpw",
        installed_version=installed_version,
        is_local_database=is_local_database,
        runner=runner,
    )

    assert ran == []
    assert calls == []


def test_run_migrations_does_not_include_license_reminder(tmp_path):
    calls = []

    def runner(cmd, cwd, env):
        calls.append(cmd)

    ran = migrations.run_migrations(
        tmp_path,
        mysql_root_password="rootpw",
        mysql_password="ubersmithpw",
        installed_version="4.0.0",
        is_local_database=True,
        runner=runner,
    )

    assert "license_update_reminder" not in ran
