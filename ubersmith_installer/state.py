"""Ini-file based state management for the Ubersmith installer.

This module is a byte-compatible reader/writer for the state file the
installer has historically maintained at ``~/.ubersmith_installer.ini`` via
Ansible's ``community.general.ini_file`` module and ``lookup('ini', ...)``.

The file is a plain ini file with a single section, ``ubersmith_installer``,
containing keys written and read across ``roles/ubersmith/tasks/main.yml``,
``roles/appliance/tasks/main.yml``, ``configure.yml``, ``upgrade_ubersmith.yml``,
``retry_letsencrypt.yml`` and ``patch_ubersmith.yml``. Known keys:

- ubersmith_home
- virtual_host
- admin_email
- ubersmith_installed_version
- lets_encrypt_certificate
- appliance_home
- app_virtual_host
- app_mysql_version
- appliance_installed_version

Because older or partial ini files may be missing newer keys (and newer
installer versions may add keys that older readers don't know about), reads
default missing keys to ``None`` and writes only ever update the specific
options requested, preserving any other sections/options already present in
the file (matching the non-destructive, per-option semantics of
``community.general.ini_file``).
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional, Union

SECTION = "ubersmith_installer"

DEFAULT_STATE_PATH = Path.home() / ".ubersmith_installer.ini"

PathLike = Union[str, os.PathLike]


@dataclass
class InstallerState:
    """Typed view of the keys ever stored in the installer state ini file.

    All fields default to ``None`` so that reading an ini file that predates
    a given key (or a fresh/missing file) doesn't error -- callers should
    treat ``None`` as "unknown"/"not yet configured".
    """

    ubersmith_home: Optional[str] = None
    virtual_host: Optional[str] = None
    admin_email: Optional[str] = None
    ubersmith_installed_version: Optional[str] = None
    lets_encrypt_certificate: Optional[str] = None
    appliance_home: Optional[str] = None
    app_virtual_host: Optional[str] = None
    app_mysql_version: Optional[str] = None
    appliance_installed_version: Optional[str] = None


def _known_fields() -> tuple[str, ...]:
    return tuple(f.name for f in fields(InstallerState))


def _resolve_path(path: Optional[PathLike]) -> Path:
    if path is None:
        return DEFAULT_STATE_PATH
    return Path(path)


def _new_parser() -> configparser.ConfigParser:
    # Disable interpolation: values in this file (e.g. email addresses
    # containing '%') should be treated literally, matching Ansible's
    # ini_file behavior which does no interpolation.
    return configparser.ConfigParser(interpolation=None)


def read_state(path: Optional[PathLike] = None) -> InstallerState:
    """Read the installer state ini file into an :class:`InstallerState`.

    If the file doesn't exist, or the ``ubersmith_installer`` section is
    absent, an ``InstallerState`` with all fields set to ``None`` is
    returned rather than raising.
    """
    ini_path = _resolve_path(path)

    parser = _new_parser()
    if ini_path.exists():
        parser.read(ini_path, encoding="utf-8")

    if not parser.has_section(SECTION):
        return InstallerState()

    values = {}
    for key in _known_fields():
        if parser.has_option(SECTION, key):
            values[key] = parser.get(SECTION, key)
    return InstallerState(**values)


def read_raw(path: Optional[PathLike] = None) -> configparser.ConfigParser:
    """Read the full ini file (all sections/options) as a ConfigParser.

    Useful when callers need to inspect or preserve sections/options this
    module doesn't know about.
    """
    ini_path = _resolve_path(path)
    parser = _new_parser()
    if ini_path.exists():
        parser.read(ini_path, encoding="utf-8")
    return parser


def write_state(
    values: dict,
    path: Optional[PathLike] = None,
    section: str = SECTION,
) -> None:
    """Update ``values`` (option -> value) in the ini file at ``path``.

    This performs a read-modify-write: any existing sections/options in the
    file that aren't mentioned in ``values`` are left untouched, matching the
    behavior of Ansible's ``community.general.ini_file`` module, which
    updates individual options without clobbering the rest of the file.

    ``value`` entries of ``None`` are skipped (not written), so callers can
    pass a partially-populated dict (or an :class:`InstallerState`'s
    ``__dict__``) without accidentally erasing unset fields.

    The parent directory is created if necessary, and the file itself is
    created if it doesn't yet exist (fresh install case).
    """
    ini_path = _resolve_path(path)
    ini_path.parent.mkdir(parents=True, exist_ok=True)

    parser = _new_parser()
    if ini_path.exists():
        parser.read(ini_path, encoding="utf-8")

    if not parser.has_section(section):
        parser.add_section(section)

    for key, value in values.items():
        if value is None:
            continue
        parser.set(section, key, str(value))

    with open(ini_path, "w", encoding="utf-8") as fh:
        parser.write(fh)


def write_installer_state(
    state: InstallerState,
    path: Optional[PathLike] = None,
) -> None:
    """Convenience wrapper to write all non-``None`` fields of ``state``."""
    write_state(
        {name: getattr(state, name) for name in _known_fields()},
        path=path,
    )
