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


#: Mirrors configure.yml's vars_prompt block: (var name, prompt text,
#: default) in the exact order they are asked today. Note the prompt text
#: for ubersmith_home/virtual_host differs slightly from INSTALL_PROMPTS.
CONFIGURE_PROMPTS: tuple[tuple[str, str, str], ...] = (
    (
        "ubersmith_home",
        "Current path in use by Ubersmith / Appliance",
        "/usr/local/ubersmith",
    ),
    (
        "virtual_host",
        "Enter the address in use by Ubersmith / Appliance; for multiple "
        "hostnames use a comma delimited list",
        "ubersmith.example.com",
    ),
    (
        "admin_email",
        "Enter the email address of the Ubersmith administrator",
        "admin@example.org",
    ),
)

#: Mirrors add_new_brand.yml's single vars_prompt entry.
ADD_BRAND_PROMPTS: tuple[tuple[str, str, str], ...] = (
    (
        "new_virtual_host",
        "Enter the hostname(s) for the new brand; for multiple brands use a "
        "comma delimited list",
        "ubersmith.example.com",
    ),
)

#: Mirrors install_appliance.yml's vars_prompt block: (var name, prompt
#: text, default) in the exact order they are asked today. Note the prompt
#: text/defaults differ from INSTALL_PROMPTS even though the first var name
#: is the same (``ubersmith_major_version``) -- this one chooses the
#: Appliance release, not the full Ubersmith stack.
APPLIANCE_INSTALL_PROMPTS: tuple[tuple[str, str, str], ...] = (
    (
        "ubersmith_major_version",
        "Choose which version of Ubersmith's Appliance to install (4 or 5)",
        "5",
    ),
    (
        "appliance_home",
        "Choose an installation directory for Ubersmith's Appliance",
        "/usr/local/ubersmith",
    ),
    (
        "app_virtual_host",
        "Enter the domain name associated with your Ubersmith installation",
        "example.com",
    ),
)

#: Mirrors "Remind admin to make a backup before proceeding with an
#: upgrade" (roles/appliance/tasks/main.yml, tagged upgrade_only, ~line 3):
#: exact pause prompt text shown before an appliance upgrade proceeds. Note
#: this differs from ``PRE_UPGRADE_REMINDER`` (no release-notes link,
#: mentions "Ubersmith appliance database" specifically) -- not generic
#: enough to reuse ``show_pre_upgrade_reminder`` as-is.
APPLIANCE_PRE_UPGRADE_REMINDER = (
    "Please ensure you have made a backup of your Ubersmith appliance "
    "database before proceeding with the upgrade process. (CTRL+C to "
    "continue)"
)


def prompt_for_values(
    prompt_specs: tuple[tuple[str, str, str], ...], defaults: dict | None = None
) -> dict:
    """Generic version of :func:`prompt_for_install_values`: asks each
    ``(name, prompt_text, default)`` triple in `prompt_specs`, in order,
    honoring any ``defaults`` overrides the same way. Used by
    ``prompt_for_install_values``/``prompt_for_configure_values``/
    ``prompt_for_add_brand_values`` to avoid duplicating the prompt loop.
    """
    overrides = defaults or {}
    answers: dict = {}
    for name, prompt_text, default in prompt_specs:
        answers[name] = click.prompt(prompt_text, default=overrides.get(name, default))
    return answers


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
    return prompt_for_values(INSTALL_PROMPTS, defaults)


def prompt_for_configure_values(defaults: dict | None = None) -> dict:
    """Interactively prompt for the 3 ``configure.yml`` vars_prompt values.

    Asks the same 3 questions, in the same order, with the same prompt text
    and defaults as ``configure.yml``'s ``vars_prompt`` block -- note the
    prompt text differs slightly from ``prompt_for_install_values`` for
    ``ubersmith_home``/``virtual_host``.

    Returns a dict keyed by variable name: ``ubersmith_home``,
    ``virtual_host``, ``admin_email``.
    """
    return prompt_for_values(CONFIGURE_PROMPTS, defaults)


def prompt_for_add_brand_values(defaults: dict | None = None) -> dict:
    """Interactively prompt for the 1 ``add_new_brand.yml`` vars_prompt value.

    Returns a dict keyed by variable name: ``new_virtual_host``.
    """
    return prompt_for_values(ADD_BRAND_PROMPTS, defaults)


def prompt_for_appliance_install_values(defaults: dict | None = None) -> dict:
    """Interactively prompt for the 3 ``install_appliance.yml`` vars_prompt values.

    Asks the same 3 questions, in the same order, with the same prompt text
    and defaults as ``install_appliance.yml``'s ``vars_prompt`` block.

    Returns a dict keyed by variable name: ``ubersmith_major_version``,
    ``appliance_home``, ``app_virtual_host``.
    """
    return prompt_for_values(APPLIANCE_INSTALL_PROMPTS, defaults)


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


def show_appliance_pre_upgrade_reminder(interactive: bool) -> None:
    """Mirror "Remind admin to make a backup before proceeding with an
    upgrade" (roles/appliance/tasks/main.yml, ~line 3).

    Same interactive/non-interactive split as :func:`show_pre_upgrade_reminder`
    (blocking ``click.confirm`` when interactive, non-blocking informational
    ``click.echo`` otherwise), but with the appliance role's own distinct
    reminder text (see ``APPLIANCE_PRE_UPGRADE_REMINDER``).
    """
    if interactive:
        click.echo(APPLIANCE_PRE_UPGRADE_REMINDER)
        click.confirm("Continue with the upgrade?", default=True, abort=True)
        return
    click.echo(f"[info] {APPLIANCE_PRE_UPGRADE_REMINDER}")
