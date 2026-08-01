"""Stop and disable mail transfer agents for the Ubersmith installer.

Mirrors the "Stop and disable mail transfer agents" task in
roles/ubersmith/tasks/main.yml, which stops/disables postfix, sendmail,
and exim4 via the Ansible `service` module with `failed_when: false`
(i.e. a missing/uninstalled service is expected and not an error), and
is skipped entirely when `ansible_os_family` is Darwin or Windows.
"""

from __future__ import annotations

import logging
import subprocess

# Mirrors the `with_items` list in the Ansible task.
MAIL_TRANSFER_AGENTS = ["postfix", "sendmail", "exim4"]

logger = logging.getLogger(__name__)


def _run_systemctl(args: list, runner=subprocess.run) -> None:
    """Run a systemctl command, swallowing any failure.

    A missing service (not installed) is the common, expected case here --
    mirroring the Ansible task's `failed_when: false` -- so failures are
    logged and otherwise ignored rather than raised.
    """
    try:
        runner(args, capture_output=True, text=True, check=False)
    except Exception as exc:  # noqa: BLE001 - any subprocess/OS error
        logger.debug("Command %s failed: %s", args, exc)


def stop_and_disable_mtas(os_family: str, runner=subprocess.run) -> None:
    """Stop and disable postfix/sendmail/exim4, matching the Ansible task.

    Parameters
    ----------
    os_family:
        Mirrors `ansible_os_family` (e.g. "Debian", "RedHat", "Darwin",
        "Windows"). Skipped entirely on Darwin/Windows, matching the
        Ansible task's `when:` condition.
    runner:
        Injection point for the subprocess call, primarily used for
        testing.
    """
    if os_family in ("Darwin", "Windows"):
        return

    for name in MAIL_TRANSFER_AGENTS:
        _run_systemctl(["systemctl", "stop", name], runner=runner)
        _run_systemctl(["systemctl", "disable", name], runner=runner)
