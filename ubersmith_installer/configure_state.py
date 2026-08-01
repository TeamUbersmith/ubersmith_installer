"""Reconfigure the installer state file, mirroring ``configure.yml``.

``configure.yml`` lets an operator update the ``ubersmith_home``,
``virtual_host`` and ``admin_email`` values recorded in the installer state
ini file (``~/.ubersmith_installer.ini``) after the fact -- e.g. if the
virtual host or admin email changes post-install. It:

1. Confirms ``{{ ubersmith_home }}/docker-compose.yml`` exists, failing with
   ``"Provided path does not contain an existing Ubersmith installation!"``
   if not (see the ``Confirm specified path is correct`` task).
2. Writes ``ubersmith_home``, ``virtual_host`` and ``admin_email`` into the
   state ini file, along with mirrored ``appliance_home``/``app_virtual_host``
   copies of the same values, for appliance-side compatibility (see the
   ``Set up ini_file for future use`` task).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import state as state_module
from .state import PathLike

#: Message raised when the given path doesn't contain a Ubersmith install,
#: matching the ``ansible.builtin.fail`` message in ``configure.yml``.
NOT_INSTALLED_MSG = "Provided path does not contain an existing Ubersmith installation!"


def verify_existing_install(ubersmith_home: Path) -> bool:
    """Return whether ``ubersmith_home`` contains an existing installation.

    Mirrors the ``Check specified path`` task in ``configure.yml``, which
    stats ``{{ ubersmith_home }}/docker-compose.yml``.
    """
    return (Path(ubersmith_home) / "docker-compose.yml").exists()


def reconfigure(
    ubersmith_home: str,
    virtual_host: str,
    admin_email: str,
    *,
    state_file: Optional[PathLike] = None,
) -> None:
    """Update the installer state file with new configuration values.

    Mirrors ``configure.yml`` end to end: verifies ``ubersmith_home`` is an
    existing installation (raising :class:`ValueError` with the same message
    Ansible's ``fail`` task used if not), then writes ``ubersmith_home``,
    ``virtual_host`` and ``admin_email`` into the state ini file, along with
    mirrored ``appliance_home``/``app_virtual_host`` copies for appliance-side
    compatibility.
    """
    if not verify_existing_install(Path(ubersmith_home)):
        raise ValueError(NOT_INSTALLED_MSG)

    state_module.write_state(
        {
            "ubersmith_home": ubersmith_home,
            "virtual_host": virtual_host,
            "admin_email": admin_email,
            "appliance_home": ubersmith_home,
            "app_virtual_host": virtual_host,
        },
        path=state_file,
    )
