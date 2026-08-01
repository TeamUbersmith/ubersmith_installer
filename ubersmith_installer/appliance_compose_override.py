"""Legacy in-place fixups for the appliance's docker-compose.override.yml.

Mirrors two ``update_compose_override_template``-tagged tasks in
``roles/appliance/tasks/main.yml``:

    * "Update docker compose override file" (~line 138): replaces every
      stale top-level ``version: '2'`` line with ``version: '3'``
      (``ansible.builtin.replace`` -- replaces *every* match, not just the
      first).
    * "Ensure http virtual host configuration line exists" (~line 149):
      ensures the apache ``sites-enabled`` bind-mount volume line is
      present, inserting it right after the ssl key volume line if it's
      missing (``ansible.builtin.lineinfile`` with ``insertafter``).

This is the appliance-specific counterpart to
:mod:`ubersmith_installer.compose_override`, which implements the
analogous-but-distinct set of narrow fixups for the *ubersmith* role's own
``docker-compose.override.yml`` (php version paths, apache sites-enabled
path rename, stale version line removal). The two files/roles evolved
independently, so the fixups differ in both content and exact regex/anchor
text -- hence a separate module rather than sharing code.

``docker-compose.override.yml`` itself is rendered by the "Create docker
compose override file" task, tagged ``compose_file`` only (no
``upgrade``/``upgrade_only``) -- i.e. install-only -- and MUST NOT be
wholesale re-rendered on upgrade; these functions instead apply narrow,
idempotent text edits to the existing file, exactly like
``compose_override.py`` does for ubersmith's.

All functions degrade gracefully (return ``False``, raise nothing) when
``override_path`` does not exist.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Mirrors the `regexp`/`replace` used by the "Update docker compose
#: override file" task (ansible.builtin.replace, backup: true). Unlike
#: ubersmith's "remove version line" fixup (lineinfile, firstmatch, state:
#: absent), this is a plain global find/replace -- every occurrence is
#: rewritten, not just the first.
_VERSION_2_RE = re.compile(r"version: '2'")


def update_compose_version(override_path: Path) -> bool:
    """Replace every ``version: '2'`` with ``version: '3'`` in place.

    Mirrors the "Update docker compose override file" task. Returns whether
    the file was modified. No-ops (returns False) if `override_path`
    doesn't exist.
    """
    if not override_path.exists():
        return False

    text = override_path.read_text()
    new_text, count = _VERSION_2_RE.subn("version: '3'", text)
    if count == 0:
        return False

    override_path.write_text(new_text)
    return True


def ensure_http_vhost_line(
    override_path: Path, appliance_home: Path, app_virtual_host: str
) -> bool:
    """Ensure the apache ``sites-enabled`` bind-mount line is present.

    Mirrors the "Ensure http virtual host configuration line exists" task:
    an ``ansible.builtin.lineinfile`` that inserts::

        - "<appliance_home>/conf/httpd/sites-enabled:/etc/apache2/sites-enabled"

    right after::

        - "<appliance_home>/conf/ssl/<app_virtual_host>.key:/var/www/appliance_root/conf/ssl/appliance.key"

    if it's not already present anywhere in the file. Matches
    ``lineinfile``'s actual semantics: if the target line already exists
    anywhere, nothing changes; if the anchor line isn't found either, the
    new line is appended at the end of the file (lineinfile's documented
    fallback behavior when ``insertafter`` doesn't match anything).

    Returns whether the file was modified. No-ops (returns False) if
    `override_path` doesn't exist.
    """
    if not override_path.exists():
        return False

    target_line = (
        f'      - "{appliance_home}/conf/httpd/sites-enabled:/etc/apache2/sites-enabled"'
    )
    anchor_line = (
        f'      - "{appliance_home}/conf/ssl/{app_virtual_host}.key:'
        '/var/www/appliance_root/conf/ssl/appliance.key"'
    )

    text = override_path.read_text()
    lines = text.splitlines()

    if any(line.strip() == target_line.strip() for line in lines):
        return False

    inserted = False
    new_lines: list[str] = []
    for line in lines:
        new_lines.append(line)
        if line.strip() == anchor_line.strip():
            new_lines.append(target_line)
            inserted = True

    if not inserted:
        new_lines.append(target_line)

    new_text = "\n".join(new_lines) + ("\n" if text.endswith("\n") else "")
    override_path.write_text(new_text)
    return True


def apply_legacy_override_fixups(
    override_path: Path, appliance_home: Path, app_virtual_host: str
) -> bool:
    """Apply both appliance legacy override fixups, in Ansible task order.

    Runs `update_compose_version`, then `ensure_http_vhost_line`, matching
    the task order in roles/appliance/tasks/main.yml. Returns whether the
    file was modified by either. No-ops (returns False) if `override_path`
    doesn't exist.
    """
    if not override_path.exists():
        return False

    version_changed = update_compose_version(override_path)
    line_changed = ensure_http_vhost_line(override_path, appliance_home, app_virtual_host)

    return version_changed or line_changed
