"""Upgrade/migration logic for the Ubersmith installer.

This module implements the "upgrade_only" tagged tasks from
``roles/ubersmith/tasks/main.yml`` -- i.e. the migrations that run *only*
when upgrading an existing install (invoked via
``ansible-playbook -t upgrade,upgrade_only upgrade_ubersmith.yml``), never
during a fresh install. It is deliberately narrow: everything tagged plain
``upgrade`` (pull_images, docker-compose.yml render, mysql cnf templates,
compose_up, etc.) is reused as-is from the Phase-1 modules
(``docker_ops.py``, ``templates.py``, ...) and is NOT reimplemented here.

Specifically this module covers:

    * "Update mysql to use caching_sha2_password" (~line 650)
    * "Alert admin to necessary license updates" (~line 131)
    * "Ensure sql_mode is only set to NO_ENGINE_SUBSTITUTION for 5.x
      upgrades" (~line 243)

Correction: an earlier version of this module claimed the sql_mode task
above was unnecessary because ``templates.render_mysql_cnf()`` is
"re-rendered unconditionally" on upgrade and already hardcodes the correct
value. That reasoning was wrong -- the upgrade command only calls
``render_mysql_cnf()`` when the *previously installed* version predates
5.2.0 (mirroring the separate "Create percona server configuration
overrides" task's own, different version guard). For any install already
at >= 5.2.0, ``conf/mysql/ubersmith.cnf`` is never touched at all otherwise
-- so the real Ansible task's defensive, unconditional (for major version
5) ``lineinfile`` fixup is genuinely needed as its own step, independent of
whether the file was just re-rendered, to repair a wrong/missing
``sql_mode`` line from a hand-edit or an older template on any existing
5.x-and-up install. See :func:`ensure_sql_mode_no_engine_substitution`.

CRITICAL: docker-compose.override.yml, the apache vhost config, rwhois.j2,
and ubersmith.ini.j2 are install-only templates that may contain customer
hand-edits. Upgrade never wholesale re-renders them -- nothing in this
module does so either.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import List, Mapping, Optional

from .docker_ops import SubprocessRunner
from .preflight import version_gte

#: Reminder message shown when upgrading from a version prior to 4.3.0.
#: Mirrors the exact wording of the "Alert admin to necessary license
#: updates" ansible.builtin.pause prompt.
LICENSE_UPDATE_REMINDER = (
    "When upgrading from versions prior to Ubersmith 4.3.0, a change is being "
    "made to the naming convention for the database host. Please contact "
    "support@ubersmith.com to ensure your license record is updated "
    "(CTRL+C to continue)"
)

#: Version below which the caching_sha2_password migration applies.
CACHING_SHA2_PASSWORD_MIN_VERSION = "5.2.0"

#: Version above which the license update reminder applies (strictly
#: greater than -- an installed_version of exactly 4.3.0 does not trigger
#: it, matching the Ansible `version_compare('4.3.0', '>')` check).
LICENSE_UPDATE_MIN_VERSION = "4.3.0"


def _version_lt(version: str, other: str) -> bool:
    """Return True if `version` < `other` (strict less-than)."""
    return not version_gte(version, other)


def _version_gt(version: str, other: str) -> bool:
    """Return True if `version` > `other` (strict greater-than)."""
    return version_gte(version, other) and not version_gte(other, version)


def _default_runner(
    cmd, cwd: Path, env: Mapping[str, str]
) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), cwd=str(cwd), env=dict(env), check=True)


def migrate_caching_sha2_password(
    ubersmith_home: Path,
    mysql_root_password: str,
    mysql_password: str,
    installed_version: str,
    is_local_database: bool,
    *,
    runner: Optional[SubprocessRunner] = None,
) -> bool:
    """Update the mysql `ubersmith` user to caching_sha2_password auth.

    Mirrors the "Update mysql to use caching_sha2_password" task: runs
    ``docker compose exec db sh -c 'mysql -u root -p$MYSQL_ROOT_PASSWORD -e
    "ALTER USER '\\''ubersmith'\\''@'\\''%'\\'' IDENTIFIED WITH
    '\\''caching_sha2_password'\\'' BY '\\''$MYSQL_PASSWORD'\\'';"'`` with
    ``chdir: ubersmith_home``.

    Only runs when both of the Ansible task's `when` conditions hold:
    ``ubersmith_installed_version is version_compare('5.2.0', '<')`` AND
    ``'DATABASE_HOST=db' in web_container_info.container.Config.Env`` (i.e.
    only for local databases, represented here by `is_local_database`).

    Returns True if the migration ran, False if it was skipped.
    """
    if not (
        _version_lt(installed_version, CACHING_SHA2_PASSWORD_MIN_VERSION)
        and is_local_database
    ):
        return False

    if runner is None:
        runner = _default_runner

    env = dict(os.environ)
    env["MYSQL_ROOT_PASSWORD"] = mysql_root_password
    env["MYSQL_PASSWORD"] = mysql_password

    shell_cmd = (
        "mysql -u root -p$MYSQL_ROOT_PASSWORD -e \"ALTER USER "
        "'ubersmith'@'%' IDENTIFIED WITH 'caching_sha2_password' BY "
        "'$MYSQL_PASSWORD';\""
    )
    cmd = ["docker", "compose", "exec", "db", "sh", "-c", shell_cmd]
    runner(cmd, cwd=Path(ubersmith_home), env=env)
    return True


def license_update_reminder(installed_version: str) -> Optional[str]:
    """Return the license-update reminder message if applicable, else None.

    Mirrors the "Alert admin to necessary license updates" task's `when`
    condition (minus `interactive`, which is a caller/UI concern):
    ``ubersmith_installed_version is version_compare('4.3.0', '>')``.

    Does not print or pause itself -- the caller (integration step)
    decides how to surface the message (print vs. pause) via prompts.py.
    """
    if _version_gt(installed_version, LICENSE_UPDATE_MIN_VERSION):
        return LICENSE_UPDATE_REMINDER
    return None


def ensure_sql_mode_no_engine_substitution(
    ubersmith_home: Path, ubersmith_major_version: str
) -> bool:
    """Mirror "Ensure sql_mode is only set to NO_ENGINE_SUBSTITUTION for 5.x
    upgrades" -- a defensive ``lineinfile``-equivalent fixup applied to the
    *existing* ``conf/mysql/ubersmith.cnf``, independent of whether that
    file was just re-rendered by ``templates.render_mysql_cnf()`` this run.

    Gated only on the target major version being "5" (matching the Ansible
    task's own ``when: ubersmith_major_version | int == 5`` -- there is no
    installed-version guard on this particular task, unlike the mysql cnf
    re-render). Only replaces an existing ``sql_mode =`` line; if the file
    doesn't have one at all, nothing is added (matching ``lineinfile``'s
    default ``regexp``-only, non-``insertafter`` behavior for this task).

    Returns whether the file was actually changed.
    """
    if str(ubersmith_major_version) != "5":
        return False

    cnf_path = Path(ubersmith_home) / "conf" / "mysql" / "ubersmith.cnf"
    try:
        text = cnf_path.read_text()
    except (FileNotFoundError, OSError):
        return False

    new_text, count = re.subn(
        r'^sql_mode\s*=.*$',
        'sql_mode = "NO_ENGINE_SUBSTITUTION"',
        text,
        flags=re.MULTILINE,
    )
    if count == 0 or new_text == text:
        return False

    cnf_path.write_text(new_text)
    return True


def run_migrations(
    ubersmith_home: Path,
    mysql_root_password: str,
    mysql_password: str,
    installed_version: str,
    is_local_database: bool,
    *,
    ubersmith_major_version: str = "5",
    runner: Optional[SubprocessRunner] = None,
) -> List[str]:
    """Run all applicable mutating migrations, returning the ones that ran.

    Orchestrates the "upgrade_only" migrations that actually change state
    (`migrate_caching_sha2_password`, `ensure_sql_mode_no_engine_substitution`).
    Does NOT include `license_update_reminder`, which is a non-mutating,
    purely informational concern the integration step calls directly.
    """
    ran: List[str] = []

    if migrate_caching_sha2_password(
        ubersmith_home,
        mysql_root_password,
        mysql_password,
        installed_version,
        is_local_database,
        runner=runner,
    ):
        ran.append("caching_sha2_password")

    if ensure_sql_mode_no_engine_substitution(ubersmith_home, ubersmith_major_version):
        ran.append("sql_mode_no_engine_substitution")

    return ran
