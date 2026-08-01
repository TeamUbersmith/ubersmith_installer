"""Legacy patch cleanup for the Ubersmith installer.

This module is a faithful port of the ``upgrade_only``-tagged tasks in
``roles/ubersmith/tasks/main.yml`` that remove artifacts left behind by the
legacy ``patch_ubersmith.sh`` mechanism:

    * "Determine if the installation has been patched"
    * "Move .patched file using the move module"
    * "Remove .patched file"
    * "Remove patches directory"

All four tasks are gated ``when: interactive`` in the Ansible source, and the
three cleanup tasks are additionally gated on ``patched.stat.exists``. Per the
comment above "Determine if the installation has been patched" ("When
upgrading, remove all patches"), this is a deliberate design choice: an old
``.patched`` file represents customer-applied patches, and the tool authors
chose not to silently discard that state during a fully unattended/scripted
upgrade -- only doing this cleanup when a human is actually present
(interactive mode). That gating semantics is preserved exactly here.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def cleanup_legacy_patches(
    ubersmith_home: Path, ubersmith_version: str, interactive: bool
) -> bool:
    """Remove legacy patch artifacts, mirroring the ``remove_patches`` tasks.

    Mirrors, in order:

        * "Determine if the installation has been patched" -- stat
          ``<ubersmith_home>/.patched``.
        * "Move .patched file using the move module" -- rename it to
          ``<ubersmith_home>/.patched-pre-<ubersmith_version>``.
        * "Remove .patched file" -- redundant/idempotent no-op after the
          rename above, intentionally not reimplemented here.
        * "Remove patches directory" -- remove
          ``<ubersmith_home>/app/patches``.

    All of these are gated ``when: interactive`` in the Ansible source, and
    the three cleanup tasks are further gated on ``patched.stat.exists`` --
    i.e. the patches directory is only removed if a ``.patched`` file was
    actually found, not unconditionally. If ``interactive`` is False, this
    function returns immediately without touching the filesystem at all.

    Returns True if any cleanup action was actually taken, False otherwise.
    """
    if not interactive:
        return False

    patched_file = ubersmith_home / ".patched"
    if not patched_file.exists():
        return False

    patched_file.rename(ubersmith_home / f".patched-pre-{ubersmith_version}")

    patches_dir = ubersmith_home / "app" / "patches"
    if patches_dir.exists():
        shutil.rmtree(patches_dir)

    return True
