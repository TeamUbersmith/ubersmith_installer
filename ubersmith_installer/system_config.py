"""Host-level system configuration changes.

Mirrors two tasks in ``roles/common``/``roles/ubersmith/tasks/main.yml`` that
mutate host system config rather than anything inside a container:

    * "Set systemd journal retention policy"
    * "Restart service cron on centos, in all cases, also issue daemon-reload
      to pick up config changes" (systemd-journald restart)

Both are best-effort: on a non-systemd host (or one without permission to
edit /etc/systemd/journald.conf) they log a warning rather than raising,
matching this package's general preflight/best-effort conventions.
"""

from __future__ import annotations

import re
import subprocess
import warnings
from pathlib import Path
from typing import Callable, Optional, Sequence

JOURNALD_CONF_PATH = Path("/etc/systemd/journald.conf")
MAX_RETENTION_SEC_LINE = "MaxRetentionSec=1year"

SubprocessRunner = Callable[[Sequence[str]], "subprocess.CompletedProcess"]


def _default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), check=True, capture_output=True)


def set_journald_retention(
    path: Path = JOURNALD_CONF_PATH,
) -> None:
    """Ensure ``MaxRetentionSec=1year`` is set in journald.conf.

    Mirrors the "Set systemd journal retention policy" lineinfile task: only
    replaces a commented-out ``#MaxRetentionSec=`` line (or appends the
    setting if journald.conf doesn't already reference it at all), rather
    than touching an administrator's own explicit value.
    """
    try:
        text = path.read_text()
    except (FileNotFoundError, PermissionError, OSError) as exc:
        warnings.warn(
            f"Could not read {path} to set journal retention policy: {exc}. "
            "Skipping -- this is expected on non-systemd hosts."
        )
        return

    if re.search(r"^\s*MaxRetentionSec\s*=", text, re.MULTILINE):
        # An explicit (non-commented) value already exists -- leave it alone,
        # matching the Ansible task's regexp which only targets "#MaxRetentionSec=".
        return

    if re.search(r"^#MaxRetentionSec=", text, re.MULTILINE):
        new_text = re.sub(
            r"^#MaxRetentionSec=.*$",
            MAX_RETENTION_SEC_LINE,
            text,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        separator = "" if text.endswith("\n") or not text else "\n"
        new_text = f"{text}{separator}{MAX_RETENTION_SEC_LINE}\n"

    try:
        path.write_text(new_text)
    except (PermissionError, OSError) as exc:
        warnings.warn(
            f"Could not write {path} to set journal retention policy: {exc}. "
            "Skipping -- this is expected without root privileges."
        )


def restart_systemd_journald(*, runner: Optional[SubprocessRunner] = None) -> None:
    """Restart systemd-journald so a journald.conf edit takes effect.

    Mirrors the "...also issue daemon-reload to pick up config changes" task.
    Best-effort: does nothing (with a warning) on non-systemd hosts.
    """
    if runner is None:
        runner = _default_runner

    try:
        runner(["systemctl", "restart", "systemd-journald"])
    except (FileNotFoundError, subprocess.CalledProcessError, OSError) as exc:
        warnings.warn(
            f"Could not restart systemd-journald: {exc}. Skipping -- this is "
            "expected on non-systemd hosts."
        )
