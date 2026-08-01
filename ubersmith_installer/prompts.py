"""Interactive prompt layer matching the existing ``vars_prompt`` UX.

Mirrors the ``vars_prompt`` block in ``install_ubersmith.yml`` (lines 6-26):
five prompts, asked in order, each with the same prompt text and default as
today's Ansible-driven installer.
"""

from __future__ import annotations

import click

from ubersmith_installer.preflight import version_gte

#: Mirrors "Print administrator reminders" (roles/ubersmith/tasks/main.yml,
#: tagged release_notes_prompt/upgrade_only, ~line 35): exact pause prompt
#: text shown before an upgrade proceeds.
PRE_UPGRADE_REMINDER = (
    "Before upgrading, please read the release notes at\n"
    "https://ubersmith.com/release-notes/\n\n"
    "Please ensure you have made a backup of your Ubersmith database\n"
    "before proceeding with the upgrade process."
)

#: Mirrors "Give the administrator a chance to update
#: docker-compose.override.yml" (roles/ubersmith/tasks/main.yml, tagged
#: upgrade_only, ~line 684): exact pause prompt text, only shown when
#: upgrading from a version newer than 4.6.0.
COMPOSE_OVERRIDE_REMINDER = (
    "Make sure docker-compose.override.yml has been updated to include a "
    "ports directive for web, and timezone volume entries for all containers"
)

#: Ansible's version_compare('4.6.0', '>') gate: only versions strictly
#: newer than 4.6.0 get the docker-compose.override.yml reminder.
COMPOSE_OVERRIDE_REMINDER_MIN_VERSION = "4.6.0"

#: Mirrors install_ubersmith.yml's vars_prompt block: (var name, prompt
#: text, default) in the exact order they are asked today.
INSTALL_PROMPTS: tuple[tuple[str, str, str], ...] = (
    (
        "ubersmith_major_version",
        "Choose which version of Ubersmith to install (4 or 5)",
        "5",
    ),
    (
        "ubersmith_home",
        "Choose an installation directory for Ubersmith",
        "/usr/local/ubersmith",
    ),
    (
        "lets_encrypt_certificate",
        "Should the installer request a security certificate from Let's Encrypt?",
        "yes",
    ),
    (
        "virtual_host",
        "Enter the hostname(s) where you will be hosting Ubersmith; for "
        "multiple hostnames use a comma delimited list",
        "ubersmith.example.com",
    ),
    (
        "admin_email",
        "Enter the email address of the Ubersmith administrator",
        "admin@example.org",
    ),
)


def prompt_for_install_values(defaults: dict | None = None) -> dict:
    """Interactively prompt for the 5 ``install_ubersmith.yml`` vars_prompt values.

    Asks the same 5 questions, in the same order, with the same prompt text
    and defaults as ``install_ubersmith.yml``'s ``vars_prompt`` block. Pass
    ``defaults`` to override one or more defaults (e.g. to re-prompt with
    previously-entered values).

    Returns a dict keyed by variable name: ``ubersmith_major_version``,
    ``ubersmith_home``, ``lets_encrypt_certificate``, ``virtual_host``,
    ``admin_email``.
    """
    overrides = defaults or {}
    answers: dict = {}
    for name, prompt_text, default in INSTALL_PROMPTS:
        answers[name] = click.prompt(prompt_text, default=overrides.get(name, default))
    return answers


def is_lets_encrypt_requested(answer: str) -> bool:
    """Parse a ``lets_encrypt_certificate`` answer the same way Ansible does.

    Mirrors the exact Jinja expression used throughout
    ``roles/ubersmith/tasks/main.yml``::

        (lets_encrypt_certificate | default('') | trim | lower) in ['y', 'yes']

    Returns True only for "y"/"yes" (case-insensitive, after trimming
    whitespace); everything else (empty, "no", "n", garbage, None, ...)
    returns False.
    """
    normalized = (answer or "").strip().lower()
    return normalized in ("y", "yes")


def show_pre_upgrade_reminder(interactive: bool) -> None:
    """Mirror "Print administrator reminders" (main.yml, ~line 35).

    In Ansible this is an ``ansible.builtin.pause`` gated ``when:
    interactive``, tagged ``release_notes_prompt``/``upgrade_only``: it warns
    the admin to read the release notes and confirm a database backup
    exists before the upgrade proceeds.

    When ``interactive`` is True, this blocks on ``click.confirm`` (default
    True, so pressing enter continues) before returning -- matching the
    "CTRL+C to continue" spirit of the Ansible pause.

    When ``interactive`` is False, Ansible's ``when: interactive`` means
    this message is never shown at all. This implementation deliberately
    improves on that: it logs the reminder as non-blocking informational
    output via ``click.echo`` and returns immediately, rather than silently
    skipping it. This is an intentional UX improvement, not an accidental
    behavior change.
    """
    if interactive:
        click.echo(PRE_UPGRADE_REMINDER)
        click.confirm("Continue with the upgrade?", default=True, abort=True)
        return
    click.echo(f"[info] {PRE_UPGRADE_REMINDER}")


def show_compose_override_reminder(interactive: bool, installed_version: str) -> None:
    """Mirror "Give the administrator a chance to update docker-compose.override.yml" (main.yml, ~line 684).

    In Ansible this is an ``ansible.builtin.pause`` gated ``when: interactive
    and ubersmith_installed_version is version_compare('4.6.0', '>')``,
    tagged ``upgrade_only``: it reminds the admin that
    docker-compose.override.yml needs a ports directive for web and
    timezone volume entries for all containers, since upgrade never
    re-renders that file (it may contain hand-edits).

    If ``installed_version`` is not strictly greater than 4.6.0, this is a
    complete no-op regardless of ``interactive`` -- matching Ansible's
    ``version_compare('4.6.0', '>')`` gate.

    Otherwise, follows the same interactive/non-interactive split as
    :func:`show_pre_upgrade_reminder`: a blocking ``click.confirm`` (default
    True) when interactive, or a non-blocking informational ``click.echo``
    when not -- again a deliberate improvement over Ansible's silent skip.
    """
    is_newer = version_gte(installed_version, COMPOSE_OVERRIDE_REMINDER_MIN_VERSION) and not version_gte(
        COMPOSE_OVERRIDE_REMINDER_MIN_VERSION, installed_version
    )
    if not is_newer:
        return
    if interactive:
        click.echo(COMPOSE_OVERRIDE_REMINDER)
        click.confirm("Continue with the upgrade?", default=True, abort=True)
        return
    click.echo(f"[info] {COMPOSE_OVERRIDE_REMINDER}")
