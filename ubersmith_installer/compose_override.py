"""Legacy in-place fixups for docker-compose.override.yml.

This module mirrors exactly three "upgrade_only" tagged tasks in
``roles/ubersmith/tasks/main.yml``:

    * "Remove version line from docker-compose.override.yml if present"
    * "Update docker compose override file for php version"
    * "Update docker compose override file for apache virtual hosts"

``docker-compose.override.yml`` itself is rendered from
``docker-compose.override.yml.j2`` by the "Create docker compose override
file" task, which carries no "upgrade" or "upgrade_only" tag at all -- it
therefore only ever runs during a fresh install. The file is explicitly
customer-owned, site-specific state ("This file contains site specific
changes and will not be modified by future upgrades" per the task's
comment) and MUST NOT be wholesale re-rendered during an upgrade.

What upgrade *does* do to this file is apply three narrow, targeted
in-place text edits to the existing content, to carry forward
compatibility with old versions of the file that predate later changes to
the base template (a stale compose "version:" line, old PHP-version host
paths, an old apache sites-enabled host path). Those three edits are what
this module implements, using plain text/regex manipulation on the file's
existing content -- read, modify in memory, write back -- leaving every
other line completely untouched.

All functions degrade gracefully (return ``False``, raise nothing) when
``override_path`` does not exist. On any real upgrade target the override
file always exists (it was created at install time), but tests/CI
environments should not crash if it's missing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

#: Mirrors the `regexp` used by the "Remove version line from
#: docker-compose.override.yml if present" task (ansible.builtin.lineinfile,
#: state: absent, firstmatch: true).
_VERSION_LINE_RE = re.compile(r"^version: '[23]'\s*$\n?", re.MULTILINE)

#: Mirrors the `regexp`/`replace` used by the "Update docker compose
#: override file for apache virtual hosts" task (ansible.builtin.replace).
_APACHE_SITES_ENABLED_OLD = "/etc/apache2/sites-enabled"
_APACHE_SITES_ENABLED_NEW = "/usr/local/apache2/conf/sites-enabled"


def remove_version_line(override_path: Path) -> bool:
    """Remove the first stale top-level "version: '2'"/"version: '3'" line.

    Mirrors the "Remove version line from docker-compose.override.yml if
    present" task: an `ansible.builtin.lineinfile` with
    `regexp: "^version: '([23])'"`, `state: absent`, `firstmatch: true` --
    i.e. only the *first* matching line is removed, not every occurrence.

    Returns whether the file was modified. No-ops (returns False) if
    `override_path` doesn't exist.
    """
    if not override_path.exists():
        return False

    text = override_path.read_text()
    new_text, count = _VERSION_LINE_RE.subn("", text, count=1)
    if count == 0:
        return False

    override_path.write_text(new_text)
    return True


def update_php_version_paths(
    override_path: Path, old_versions: Sequence[str], new_version: str
) -> bool:
    """Replace old `/etc/php/<old_version>` paths with the current version.

    Mirrors the "Update docker compose override file for php version" task
    (`ansible.builtin.replace`, looping over `old_php_versions`), replacing
    every occurrence of `/etc/php/{{ item }}` with `/etc/php/{{ php_version }}`
    for each old version in turn.

    Returns whether the file was modified. No-ops (returns False) if
    `override_path` doesn't exist.
    """
    if not override_path.exists():
        return False

    text = override_path.read_text()
    original = text
    for old_version in old_versions:
        pattern = re.compile(re.escape(f"/etc/php/{old_version}"))
        text = pattern.sub(f"/etc/php/{new_version}", text)

    if text == original:
        return False

    override_path.write_text(text)
    return True


def update_apache_vhost_path(override_path: Path) -> bool:
    """Replace old apache sites-enabled paths with the new container path.

    Mirrors the "Update docker compose override file for apache virtual
    hosts" task (`ansible.builtin.replace`), replacing every occurrence of
    `/etc/apache2/sites-enabled` with `/usr/local/apache2/conf/sites-enabled`.

    Returns whether the file was modified. No-ops (returns False) if
    `override_path` doesn't exist.
    """
    if not override_path.exists():
        return False

    text = override_path.read_text()
    new_text = text.replace(_APACHE_SITES_ENABLED_OLD, _APACHE_SITES_ENABLED_NEW)
    if new_text == text:
        return False

    override_path.write_text(new_text)
    return True


def apply_legacy_override_fixups(
    override_path: Path, old_php_versions: Sequence[str], php_version: str
) -> bool:
    """Apply all three legacy override fixups, in the same order as Ansible.

    Runs `remove_version_line`, then `update_php_version_paths`, then
    `update_apache_vhost_path`, matching the task order in
    roles/ubersmith/tasks/main.yml. Returns whether the file was modified
    by any of the three. No-ops (returns False) if `override_path` doesn't
    exist.
    """
    if not override_path.exists():
        return False

    version_changed = remove_version_line(override_path)
    php_changed = update_php_version_paths(override_path, old_php_versions, php_version)
    apache_changed = update_apache_vhost_path(override_path)

    return version_changed or php_changed or apache_changed
