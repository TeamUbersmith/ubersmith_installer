"""Jinja2-based rendering layer for the Ubersmith installer.

This module reproduces, using a plain ``jinja2.Environment`` (NOT Ansible's
own templating engine), what ``ansible.builtin.template`` does today for the
three templates copied into ``ubersmith_installer/templates/``:

    * docker-compose.yml.j2
    * dot_env.j2
    * ubersmith.ini.j2

Design notes
------------
``roles/ubersmith/templates/docker-compose.yml.j2`` was audited (grepping for
``ansible_`` and every ``{%``/``{{`` block) and the *only* Ansible-specific
thing it touches is the ``ansible_os_family`` fact, used in
``{% if ansible_os_family != 'Darwin' %}`` guards around ``logging:`` stanzas.
No Ansible-only filters or tests (e.g. ``version_compare``) are used anywhere
in the file -- ``join`` is a stock Jinja2/Ansible-builtin filter available in
plain Jinja2 too.

Variable-naming choice: rather than renaming ``ansible_os_family`` inside the
copied template (which would create a diff against the Ansible original and
risk drifting the two copies apart), we keep the variable named
``ansible_os_family`` in the render context. Callers of the public
``render_*`` functions do NOT need to know or supply this -- it is computed
automatically from ``platform``/``distro`` (see ``get_os_family``) and merged
into the context unless the caller already provided an explicit value (which
is mostly useful for tests that want to force a particular branch).
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any, Mapping

import distro
from jinja2 import Environment, FileSystemLoader, StrictUndefined

#: Directory containing the .j2 template files shipped with this package.
TEMPLATES_DIR = Path(__file__).parent / "templates"

#: Shared Jinja2 environment. ``StrictUndefined`` makes any reference to a
#: variable that isn't in the supplied context raise ``jinja2.UndefinedError``
#: at render time (mirroring the way Ansible templating fails loudly on
#: undefined variables), instead of silently rendering as an empty string.
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)

# Debian-family and RedHat-family etc. groupings mirror the "os_family"
# facts Ansible's setup module derives from /etc/os-release on Linux.
_DEBIAN_FAMILY = {"debian", "ubuntu", "raspbian", "linuxmint", "pop"}
_REDHAT_FAMILY = {
    "rhel",
    "centos",
    "fedora",
    "rocky",
    "almalinux",
    "amzn",
    "amazon",
    "oracle",
    "ol",
}
_SUSE_FAMILY = {"opensuse", "sles", "suse", "opensuse-leap", "opensuse-tumbleweed"}
_ARCH_FAMILY = {"arch", "archlinux", "manjaro"}


def get_os_family() -> str:
    """Compute an ``ansible_os_family``-compatible value for the host.

    This is a Python re-implementation of the relevant subset of what
    Ansible's ``setup`` module derives from ``platform``/``/etc/os-release``:
    it does not call into Ansible at all.
    """
    system = platform.system()
    if system == "Darwin":
        return "Darwin"
    if system == "Windows":
        return "Windows"
    if system == "Linux":
        os_id = (distro.id() or "").lower()
        like = (distro.like() or "").lower()
        if os_id in _DEBIAN_FAMILY or "debian" in like:
            return "Debian"
        if os_id in _REDHAT_FAMILY or "rhel" in like or "fedora" in like:
            return "RedHat"
        if os_id in _SUSE_FAMILY or "suse" in like:
            return "Suse"
        if os_id in _ARCH_FAMILY or "arch" in like:
            return "Archlinux"
        if os_id == "alpine":
            return "Alpine"
        # Reasonable default for unrecognized Linux distros.
        return "Debian"
    return system or "Unknown"


def render(template_name: str, context: Mapping[str, Any]) -> str:
    """Render any template in ``ubersmith_installer/templates/`` by filename.

    ``context`` is a plain dict of the variables the template needs (e.g.
    ``ubersmith_home``, ``virtual_host``, ``admin_email``,
    ``ubersmith_version``, ``containers_release_version``, ``registry``,
    ...). If ``ansible_os_family`` is not present in ``context`` it is
    computed automatically via ``get_os_family()``.
    """
    ctx = dict(context)
    ctx.setdefault("ansible_os_family", get_os_family())
    template = _env.get_template(template_name)
    return template.render(**ctx)


def render_docker_compose(context: Mapping[str, Any]) -> str:
    """Render ``docker-compose.yml.j2`` with the given context.

    Variables referenced by this template: ``registry``, ``ubersmith_version``,
    ``containers_release_version``, ``container_domain``, ``ubersmith_home``,
    ``ubersmith_release`` (nested dict keyed by ``ubersmith_major_version``,
    with ``mysql_version``, ``php_version``, ``backup_version``, and a nested
    ``containers`` dict with ``web_container_repo`` and ``haproxy_version``),
    ``ubersmith_major_version``, ``mozilla_ciphers`` (nested dict:
    ``mozilla_ciphers.configurations.intermediate.ciphers.openssl`` is a list
    of cipher-suite strings joined with ``:``), ``pmm_version``,
    ``certbot_version``, ``virtual_hosts`` and ``notify_email`` (only
    referenced inside commented-out ``# command: ...`` lines, but Jinja
    evaluates ``{{ }}`` expressions regardless of surrounding YAML comments,
    so they must still be supplied), and the Ansible fact
    ``ansible_os_family`` (supplied automatically -- see module docstring).
    """
    return render("docker-compose.yml.j2", context)


def render_dot_env(context: Mapping[str, Any]) -> str:
    """Render ``dot_env.j2`` with the given context.

    This template currently has no variables (it is the static line
    ``MAINTENANCE=0``), but ``context`` is accepted for interface
    consistency and forward-compatibility.
    """
    return render("dot_env.j2", context)


def render_ubersmith_ini(context: Mapping[str, Any]) -> str:
    """Render ``ubersmith.ini.j2`` with the given context.

    Variables referenced by this template: ``php_gc_maxlifetime``,
    ``php_memory_limit``, ``php_default_socket_timeout``,
    ``php_max_input_time``, ``php_max_input_vars``, ``php_max_execution_time``,
    ``php_upload_max_filesize``, ``php_post_max_size``.
    """
    return render("ubersmith.ini.j2", context)
