"""Interactive prompt layer matching the existing ``vars_prompt`` UX.

Mirrors the ``vars_prompt`` block in ``install_ubersmith.yml`` (lines 6-26):
five prompts, asked in order, each with the same prompt text and default as
today's Ansible-driven installer.
"""

from __future__ import annotations

import click

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
