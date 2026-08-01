"""Jinja2-based rendering layer for the Ubersmith installer.

This module reproduces, using a plain ``jinja2.Environment`` (NOT Ansible's
own templating engine), what ``ansible.builtin.template`` does today for the
templates copied into ``ubersmith_installer/templates/``:

    * docker-compose.yml.j2
    * dot_env.j2
    * ubersmith.ini.j2
    * rwhois.j2
    * docker-compose.override.yml.j2
    * ubersmith.cnf.4.j2 / ubersmith.cnf.5.j2
    * ubersmith_extra.cnf.j2
    * instance_vhost.j2
    * postfix-deploy.sh.j2
    * ubersmith-deploy.sh.j2
    * ubersmith_certbot_renew.sh.j2

APPLIANCE role templates (``roles/appliance/templates/``) are also mirrored
here, under names prefixed with ``appliance-`` (or, for the one filename that
did not collide with an existing ubersmith-role template,
``appliance_vhost.j2`` unprefixed) to avoid clobbering the ubersmith-role
copies of the same base filename:

    * roles/appliance/templates/appliance_vhost.j2
      -> ubersmith_installer/templates/appliance_vhost.j2 (no collision)
    * roles/appliance/templates/docker-compose.yml.j2
      -> ubersmith_installer/templates/appliance-docker-compose.yml.j2
    * roles/appliance/templates/docker-compose.override.yml.j2
      -> ubersmith_installer/templates/appliance-docker-compose.override.yml.j2
    * roles/appliance/templates/ubersmith.cnf.4.j2
      -> ubersmith_installer/templates/appliance-ubersmith.cnf.4.j2
    * roles/appliance/templates/ubersmith.cnf.5.j2
      -> ubersmith_installer/templates/appliance-ubersmith.cnf.5.j2

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

import os
import platform
import warnings
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


def _parse_proc_meminfo() -> dict[str, int]:
    """Parse ``/proc/meminfo`` into a dict of ``{key: value_in_kb}``."""
    values: dict[str, int] = {}
    with open("/proc/meminfo", "r", encoding="ascii") as fh:
        for line in fh:
            key, _, rest = line.partition(":")
            rest = rest.strip()
            if rest.endswith("kB"):
                rest = rest[:-2].strip()
            try:
                values[key.strip()] = int(rest)
            except ValueError:
                continue
    return values


#: Conservative fallback used on non-Linux hosts (e.g. macOS dev machines)
#: where ``/proc/meminfo`` does not exist. This is only used for local
#: testing/development -- production installs target Linux.
_FALLBACK_MEMTOTAL_MB = 4096


def get_memtotal_mb() -> int:
    """Compute an ``ansible_memtotal_mb``-compatible value for the host.

    On Linux this reads ``MemTotal`` from ``/proc/meminfo`` (the same source
    Ansible's ``setup`` module uses). On non-Linux hosts (e.g. macOS, used
    for local development) it falls back to ``os.sysconf`` where available,
    and otherwise a conservative hardcoded default, emitting a warning since
    this value should not be relied upon in that case.
    """
    if platform.system() == "Linux":
        try:
            meminfo = _parse_proc_meminfo()
            return round(meminfo["MemTotal"] / 1024)
        except (OSError, KeyError):
            pass
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return round((pages * page_size) / (1024 * 1024))
    except (ValueError, OSError, AttributeError):
        warnings.warn(
            "Unable to determine total system memory on this platform; "
            f"falling back to a hardcoded default of {_FALLBACK_MEMTOTAL_MB}MB. "
            "This is only expected during local development/testing on "
            "non-Linux hosts.",
            stacklevel=2,
        )
        return _FALLBACK_MEMTOTAL_MB


def get_memfree_mb() -> int:
    """Compute an ``ansible_memfree_mb``-compatible value for the host.

    On Linux this reads ``MemAvailable`` (falling back to ``MemFree``) from
    ``/proc/meminfo``, mirroring the value Ansible's ``setup`` module
    reports. On non-Linux hosts it falls back to a conservative fraction of
    ``get_memtotal_mb()``, with a warning, for local testing purposes only.
    """
    if platform.system() == "Linux":
        try:
            meminfo = _parse_proc_meminfo()
            key = "MemAvailable" if "MemAvailable" in meminfo else "MemFree"
            return round(meminfo[key] / 1024)
        except (OSError, KeyError):
            pass
    warnings.warn(
        "Unable to determine free system memory on this platform; "
        "falling back to half of total memory. This is only expected "
        "during local development/testing on non-Linux hosts.",
        stacklevel=2,
    )
    return round(get_memtotal_mb() / 2)


def get_timezone_file() -> str:
    """Compute a ``timezone_file.stat.lnk_source``-compatible value.

    This is a Python re-implementation of the Ansible task that runs
    ``readlink -f /etc/localtime`` and registers the result as
    ``timezone_file`` -- ``os.path.realpath`` is the direct equivalent.
    """
    return os.path.realpath("/etc/localtime")


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


def render_rwhois(context: Mapping[str, Any]) -> str:
    """Render ``rwhois.j2`` with the given context.

    Variables referenced by this template: ``ubersmith_root``,
    ``main_virtual_host``.
    """
    return render("rwhois.j2", context)


def render_docker_compose_override(context: Mapping[str, Any]) -> str:
    """Render ``docker-compose.override.yml.j2`` with the given context.

    Variables referenced by this template: ``ubersmith_home``,
    ``mysql_root_password``, ``mysql_ubersmith_password``, ``php_version``,
    ``container_domain``, and the Ansible facts ``ansible_os_family``
    (auto-supplied, see module docstring) and
    ``timezone_file.stat.lnk_source`` -- the latter is supplied
    automatically via ``get_timezone_file()`` unless the caller already
    provided a ``timezone_file`` mapping in ``context`` (mostly useful for
    tests that want to force a particular value, e.g. on Darwin where the
    template does not reference it at all).
    """
    ctx = dict(context)
    ctx.setdefault("ansible_os_family", get_os_family())
    if "timezone_file" not in ctx:
        ctx["timezone_file"] = {"stat": {"lnk_source": get_timezone_file()}}
    return render("docker-compose.override.yml.j2", ctx)


def render_mysql_cnf(major_version: str, context: Mapping[str, Any]) -> str:
    """Render ``ubersmith.cnf.4.j2`` or ``ubersmith.cnf.5.j2``.

    ``major_version`` selects which of the two templates to render (mirrors
    Ansible's ``src: "ubersmith.cnf.{{ ubersmith_major_version }}.j2"``).

    ``ubersmith.cnf.4.j2`` references ``ansible_memfree_mb``;
    ``ubersmith.cnf.5.j2`` references ``ansible_memtotal_mb``. Whichever is
    needed is auto-supplied via ``get_memfree_mb()``/``get_memtotal_mb()``
    unless already present in ``context``.
    """
    ctx = dict(context)
    if str(major_version) == "4":
        ctx.setdefault("ansible_memfree_mb", get_memfree_mb())
        return render("ubersmith.cnf.4.j2", ctx)
    if str(major_version) == "5":
        ctx.setdefault("ansible_memtotal_mb", get_memtotal_mb())
        return render("ubersmith.cnf.5.j2", ctx)
    raise ValueError(f"Unsupported ubersmith major version: {major_version!r}")


def render_mysql_extra_cnf(context: Mapping[str, Any]) -> str:
    """Render ``ubersmith_extra.cnf.j2`` with the given context.

    This template currently has no variables, but ``context`` is accepted
    for interface consistency and forward-compatibility.
    """
    return render("ubersmith_extra.cnf.j2", context)


def render_instance_vhost(context: Mapping[str, Any]) -> str:
    """Render ``instance_vhost.j2`` with the given context.

    Variables referenced by this template: ``admin_email``,
    ``ubersmith_root``, ``item`` (the virtual host domain being rendered --
    Ansible loops this template once per entry in ``virtual_hosts`` via
    ``with_items``), ``fcgi_host``, and ``mozilla_ciphers`` (see
    ``render_docker_compose`` docstring for its shape).
    """
    return render("instance_vhost.j2", context)


def render_postfix_deploy_hook(context: Mapping[str, Any]) -> str:
    """Render ``postfix-deploy.sh.j2`` with the given context.

    This template currently has no variables, but ``context`` is accepted
    for interface consistency and forward-compatibility.
    """
    return render("postfix-deploy.sh.j2", context)


def render_ubersmith_deploy_hook(context: Mapping[str, Any]) -> str:
    """Render ``ubersmith-deploy.sh.j2`` with the given context.

    This template currently has no variables, but ``context`` is accepted
    for interface consistency and forward-compatibility.
    """
    return render("ubersmith-deploy.sh.j2", context)


def render_certbot_renew_script(context: Mapping[str, Any]) -> str:
    """Render ``ubersmith_certbot_renew.sh.j2`` with the given context.

    Variables referenced by this template: ``ubersmith_home``.
    """
    return render("ubersmith_certbot_renew.sh.j2", context)


# ---------------------------------------------------------------------------
# Appliance role templates (see module docstring for the naming/collision
# scheme used when copying these in from roles/appliance/templates/).
# ---------------------------------------------------------------------------


def render_appliance_docker_compose(context: Mapping[str, Any]) -> str:
    """Render ``appliance-docker-compose.yml.j2`` with the given context.

    Mirrors ``roles/appliance/templates/docker-compose.yml.j2``. Variables
    referenced: ``registry``, ``appliance_release`` (nested dict keyed by
    ``ubersmith_major_version``, with ``mysql_version``, ``backup_version``,
    and a nested ``containers`` dict with ``appweb_container_repo``),
    ``ubersmith_major_version``, ``appliance_version``,
    ``containers_release_version``, ``app_virtual_host``, ``appliance_home``,
    and the Ansible fact ``ansible_os_family`` (supplied automatically --
    see module docstring).
    """
    return render("appliance-docker-compose.yml.j2", context)


def render_appliance_docker_compose_override(context: Mapping[str, Any]) -> str:
    """Render ``appliance-docker-compose.override.yml.j2`` with the context.

    Mirrors ``roles/appliance/templates/docker-compose.override.yml.j2``.
    Variables referenced: ``mysql_appliance_password``,
    ``mysql_root_password``, ``appliance_home``, ``app_virtual_host``, and
    the Ansible facts ``ansible_os_family`` (auto-supplied, see module
    docstring) and ``timezone_file.stdout``.

    Note the appliance role's Ansible task registers ``timezone_file`` via
    ``ansible.builtin.command: readlink -f /etc/localtime`` (a ``command``
    module result, exposing ``.stdout``) rather than via ``ansible.builtin.
    stat`` (exposing ``.stat.lnk_source``) as the ubersmith role does -- so
    the shape of the auto-supplied ``timezone_file`` mapping differs from
    ``render_docker_compose_override`` above, even though both are computed
    from the same underlying ``get_timezone_file()`` helper.
    """
    ctx = dict(context)
    ctx.setdefault("ansible_os_family", get_os_family())
    if "timezone_file" not in ctx:
        ctx["timezone_file"] = {"stdout": get_timezone_file()}
    return render("appliance-docker-compose.override.yml.j2", ctx)


def render_appliance_vhost(context: Mapping[str, Any]) -> str:
    """Render ``appliance_vhost.j2`` with the given context.

    Mirrors ``roles/appliance/templates/appliance_vhost.j2``. Variables
    referenced: ``appliance_root``, ``app_virtual_host``.
    """
    return render("appliance_vhost.j2", context)


def render_appliance_mysql_cnf(context: Mapping[str, Any], major_version: str) -> str:
    """Render ``appliance-ubersmith.cnf.4.j2`` or ``...cnf.5.j2``.

    ``major_version`` selects which of the two templates to render (mirrors
    Ansible's ``src: "ubersmith.cnf.{{ ubersmith_major_version }}.j2"`` in
    the appliance role). Both templates are currently static (no
    variables), but ``context`` is accepted for interface consistency and
    forward-compatibility.
    """
    if str(major_version) == "4":
        return render("appliance-ubersmith.cnf.4.j2", context)
    if str(major_version) == "5":
        return render("appliance-ubersmith.cnf.5.j2", context)
    raise ValueError(f"Unsupported ubersmith major version: {major_version!r}")
