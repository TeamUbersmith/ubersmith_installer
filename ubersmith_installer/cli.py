"""Command-line entry point for the Ubersmith installer.

The ``install`` command wires together every module built so far --
:mod:`ubersmith_installer.preflight`, :mod:`ubersmith_installer.prompts`,
:mod:`ubersmith_installer.secrets`, :mod:`ubersmith_installer.certs`,
:mod:`ubersmith_installer.docker_ops`, :mod:`ubersmith_installer.templates`,
:mod:`ubersmith_installer.mta`, :mod:`ubersmith_installer.certbot`, and
:mod:`ubersmith_installer.state` -- into a real, working install, reaching
parity with ``install_ubersmith.yml`` + ``roles/common`` + the
fresh-install-scope tasks (every task NOT tagged ``upgrade_only``) in
``roles/ubersmith/tasks/main.yml``.

Out of scope for this command (left to later phases): the ``appliance``
role, an ``upgrade`` subcommand, and any other subcommands.
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
from pathlib import Path

import click

from . import (
    appliance_compose_override,
    appliance_ops,
    certbot,
    certs,
    compose_override,
    configure_state,
    docker_ops,
    migrations,
    mta,
    patch_apply,
    patch_cleanup,
    preflight,
    prompts,
    redis_migration,
    retry_letsencrypt as retry_letsencrypt_module,
    secrets,
    state,
    system_config,
    templates,
)

#: Mirrors roles/ubersmith/vars/main.yml -- the release metadata needed to
#: render docker-compose.yml.j2 for a given ubersmith_major_version.
UBERSMITH_RELEASE = {
    "4": {
        "mysql_version": 57,
        "php_version": 73,
        "backup_version": 2,
        "ubersmith_release_version": "4.6.4",
        "containers": {
            "release_version": "r4",
            "web_container_repo": "ubersmith",
            "haproxy_version": "1.7-alpine",
        },
    },
    "5": {
        "mysql_version": 84,
        "php_version": 84,
        "backup_version": 84,
        "ubersmith_release_version": "5.2.2",
        "containers": {
            "release_version": "r3",
            "web_container_repo": "ubersmith",
            "haproxy_version": "1.7-alpine",
        },
    },
}

#: Fallback used by roles/ubersmith/vars/main.yml when the live
#: ssl-config.mozilla.org lookup is unavailable.
DEFAULT_MOZILLA_CIPHERS = {
    "configurations": {
        "intermediate": {
            "ciphers": {
                "openssl": [
                    "ECDHE-RSA-AES128-GCM-SHA256",
                    "ECDHE-ECDSA-AES128-GCM-SHA256",
                    "ECDHE-ECDSA-AES256-GCM-SHA384",
                    "ECDHE-RSA-AES256-GCM-SHA384",
                ],
            },
        },
    },
}

DEFAULT_REGISTRY = "ghcr.io/teamubersmith"
DEFAULT_PMM_VERSION = 2
DEFAULT_CERTBOT_VERSION = "v3.2.0"

#: Mirrors roles/ubersmith/vars/main.yml's fixed (non-major-version-keyed)
#: values.
UBERSMITH_ROOT = "/var/www/ubersmith_root"
OVERRIDE_PHP_VERSION = "8.4"
FCGI_HOST = "php"

#: PHP/ubersmith.ini defaults, mirrors roles/ubersmith/vars/main.yml.
DEFAULT_INI_CONTEXT = {
    "php_gc_maxlifetime": 86400,
    "php_memory_limit": "512M",
    "php_default_socket_timeout": 6000,
    "php_max_input_time": 6000,
    "php_max_input_vars": 2000,
    "php_max_execution_time": 3600,
    "php_upload_max_filesize": "16M",
    "php_post_max_size": "50M",
}

#: Mirrors roles/ubersmith/vars/main.yml's `old_php_versions` -- the
#: docker-compose.override.yml PHP-path fixups applied on every upgrade.
OLD_PHP_VERSIONS = ["5.6", "7.1", "7.3", "8.2"]

#: Mirrors the "Create percona server configuration overrides" task's
#: `when: ubersmith_installed_version is version_compare('5.2.0', '<')`
#: guard -- conf/mysql/ubersmith.cnf is only re-rendered on upgrade when the
#: *previously installed* version predates 5.2.0 (which already has the
#: sql_mode fix and the current mysql config baked in via a fresh install or
#: a prior upgrade).
MYSQL_CNF_RERENDER_MAX_VERSION = "5.2.0"

#: Mirrors roles/appliance/vars/main.yml -- the release metadata needed to
#: render appliance-docker-compose.yml.j2 for a given ubersmith_major_version.
APPLIANCE_RELEASE = {
    "4": {
        "mysql_version": 57,
        "appliance_release_version": "4.6.3",
        "backup_version": 2,
        "containers": {
            "release_version": "r4",
            "appweb_container_repo": "appliance",
        },
    },
    "5": {
        "mysql_version": 80,
        "appliance_release_version": "5.1.4",
        "backup_version": 8,
        "containers": {
            "release_version": "r3",
            "appweb_container_repo": "appliance",
        },
    },
}

#: Mirrors roles/appliance/vars/main.yml's fixed `appliance_root` value.
APPLIANCE_ROOT = "/var/www/appliance_root"


def _image_refs(release: dict, certbot_version: str) -> list[str]:
    """Build the "Pull required images" image reference list.

    Mirrors the ``with_items`` list on the "Pull required images; this may
    take a few moments" task in roles/ubersmith/tasks/main.yml exactly.
    """
    registry = DEFAULT_REGISTRY
    version = release["ubersmith_release_version"]
    containers_release = release["containers"]["release_version"]
    tag = f"{version}-{containers_release}"
    return [
        f"{registry}/solr:{tag}",
        f"{registry}/ps{release['mysql_version']}:{tag}",
        f"{registry}/{release['containers']['web_container_repo']}:{tag}",
        f"{registry}/php{release['php_version']}:{tag}",
        f"{registry}/cron:{tag}",
        f"{registry}/mail:{tag}",
        f"{registry}/xinetd:{tag}",
        f"{registry}/redis7:{tag}",
        "busybox:latest",
        f"{registry}/rsyslog:{tag}",
        f"ghcr.io/teamubersmith/certbot:{certbot_version}",
        "falcosecurity/falco-no-driver:latest",
        "clamav/clamav:1.4_base",
    ]


def _appliance_image_refs(release: dict) -> list[str]:
    """Build the appliance "Pull required images" image reference list.

    Mirrors the ``with_items`` list on the "Pull required images; this may
    take a few moments" task in roles/appliance/tasks/main.yml exactly --
    note this list is much shorter than ubersmith's: just the db, appweb,
    and cron images (the xtrabackup/app_backup image referenced by
    appliance-docker-compose.yml.j2 is not explicitly pulled here, only
    picked up later by ``docker compose pull``/``docker compose up``).
    """
    registry = DEFAULT_REGISTRY
    version = release["appliance_release_version"]
    containers_release = release["containers"]["release_version"]
    tag = f"{version}-{containers_release}"
    return [
        f"{registry}/appliance_db_ps{release['mysql_version']}:{tag}",
        f"{registry}/{release['containers']['appweb_container_repo']}:{tag}",
        f"{registry}/appliance_cron:{tag}",
    ]


def _write_rendered(
    path: Path, content: str, mode: int, owner_uid: int, owner_gid: int
) -> None:
    """Write a rendered template to `path`, matching the mode/owner an
    ``ansible.builtin.template`` task would apply. Ownership changes are
    best-effort (mirrors docker_ops._chown), since the process running this
    may not have privileges to chown to another user in test/dev
    environments.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    try:
        os.chown(path, owner_uid, owner_gid)
    except (PermissionError, LookupError, OSError, AttributeError):
        pass


@click.group()
@click.version_option(package_name="ubersmith-installer")
def main() -> None:
    """Ubersmith installer CLI."""


@main.command()
@click.option(
    "--ubersmith-major-version",
    "ubersmith_major_version",
    default=None,
    help="Choose which version of Ubersmith to install (4 or 5).",
)
@click.option(
    "--ubersmith-home",
    "ubersmith_home",
    default=None,
    help="Choose an installation directory for Ubersmith.",
)
@click.option(
    "--virtual-host",
    "virtual_host",
    default=None,
    help=(
        "Enter the hostname(s) where you will be hosting Ubersmith; for "
        "multiple hostnames use a comma delimited list."
    ),
)
@click.option(
    "--admin-email",
    "admin_email",
    default=None,
    help="Enter the email address of the Ubersmith administrator.",
)
@click.option(
    "--lets-encrypt-certificate",
    "lets_encrypt_certificate",
    default=None,
    help="Should the installer request a security certificate from Let's Encrypt? (yes/no)",
)
@click.option(
    "--non-interactive",
    is_flag=True,
    default=False,
    help=(
        "Never prompt. All 5 install values (--ubersmith-major-version, "
        "--ubersmith-home, --lets-encrypt-certificate, --virtual-host, "
        "--admin-email) must be supplied; otherwise the command aborts "
        "with an error instead of prompting."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help=(
        "Skip Docker/network/service side effects (image pulls, `docker "
        "compose up`, redis scaling, stopping/disabling MTAs, and "
        "requesting Let's Encrypt certificates). Directories, self-signed "
        "certs, rendered config files, and the state file are still "
        "written for real under --ubersmith-home. Intended for exercising "
        "this command in CI/tests without requiring root, a Docker daemon, "
        "or touching a real system."
    ),
)
@click.option(
    "--state-file",
    "state_file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=state.DEFAULT_STATE_PATH,
    show_default=True,
    help="Path to the installer state ini file to write.",
)
@click.option(
    "--skip-preflight",
    is_flag=True,
    default=False,
    help="Skip OS/Docker preflight checks (for testing in non-standard environments).",
)
def install(
    ubersmith_major_version: str | None,
    ubersmith_home: str | None,
    virtual_host: str | None,
    admin_email: str | None,
    lets_encrypt_certificate: str | None,
    non_interactive: bool,
    dry_run: bool,
    state_file: Path,
    skip_preflight: bool,
) -> None:
    """Install Ubersmith.

    Reaches parity with ``install_ubersmith.yml`` + ``roles/common`` + the
    fresh-install-scope tasks in ``roles/ubersmith/tasks/main.yml``: runs
    preflight checks, gathers the 5 install-time values (interactively by
    default, matching today's ``vars_prompt`` UX), generates/reads MySQL
    passwords and self-signed certs, creates the Ubersmith directory tree
    and static helper files, renders every config template to its real
    destination under ``--ubersmith-home``, stops/disables local MTAs,
    pulls images and brings up containers, optionally requests Let's
    Encrypt certificates, and writes the installer state file.

    Value-gathering UX: if all 5 required values are supplied as flags,
    no prompting happens at all. Otherwise, matching Ansible's
    ``vars_prompt`` behavior (which always prompts for all 5), the user is
    prompted for all 5 values -- but any values already supplied via flags
    are used to pre-seed the default shown at each prompt (rather than
    always falling back to the hardcoded install_ubersmith.yml defaults),
    so hitting enter keeps the flag's value. Pass --non-interactive to
    abort instead of prompting when values are missing (for unattended/CI
    use where a flag was simply forgotten).
    """
    required_names = [name for name, _, _ in prompts.INSTALL_PROMPTS]
    supplied = {
        "ubersmith_major_version": ubersmith_major_version,
        "ubersmith_home": ubersmith_home,
        "lets_encrypt_certificate": lets_encrypt_certificate,
        "virtual_host": virtual_host,
        "admin_email": admin_email,
    }
    provided = {name: value for name, value in supplied.items() if value is not None}

    if len(provided) == len(required_names):
        answers = provided
    elif non_interactive:
        missing = [name for name in required_names if name not in provided]
        flags = ", ".join(f"--{name.replace('_', '-')}" for name in missing)
        click.secho(
            f"--non-interactive was given but the following value(s) are missing: {flags}",
            fg="red",
            bold=True,
        )
        sys.exit(2)
    else:
        answers = prompts.prompt_for_install_values(defaults=provided)

    ubersmith_major_version = answers["ubersmith_major_version"]
    ubersmith_home = answers["ubersmith_home"]
    lets_encrypt_certificate = answers["lets_encrypt_certificate"]
    virtual_host = answers["virtual_host"]
    admin_email = answers["admin_email"]

    if not skip_preflight:
        click.echo("Running preflight checks...")
        result = preflight.run_preflight_checks(check_service_enabled=False)

        for warning in result.warnings:
            click.secho(f"  [WARN] {warning}", fg="yellow")

        if not result.ok:
            for error in result.errors:
                click.secho(f"  [FAIL] {error}", fg="red")
            click.secho("Preflight checks failed.", fg="red", bold=True)
            sys.exit(1)

        click.secho("Preflight checks passed.", fg="green")
    else:
        click.secho("Skipping preflight checks (--skip-preflight).", fg="yellow")

    release = UBERSMITH_RELEASE.get(ubersmith_major_version)
    if release is None:
        click.secho(
            f"Unsupported ubersmith_major_version: {ubersmith_major_version!r} "
            f"(expected one of {sorted(UBERSMITH_RELEASE)}).",
            fg="red",
            bold=True,
        )
        sys.exit(1)

    ubersmith_home_path = Path(ubersmith_home)
    virtual_hosts = [h.strip() for h in virtual_host.split(",") if h.strip()]
    main_virtual_host = virtual_hosts[0] if virtual_hosts else virtual_host

    owner_uid = os.getuid() if hasattr(os, "getuid") else 0
    owner_gid = os.getgid() if hasattr(os, "getgid") else 0

    # 3. MySQL passwords -- lookup('password', ...) equivalent, keyed off
    # the user's home directory + main_virtual_host (roles/ubersmith/vars).
    password_paths = secrets.ubersmith_password_paths(Path.home(), main_virtual_host)
    mysql_root_password = secrets.get_or_create_password(password_paths["root_db_pass"])
    mysql_ubersmith_password = secrets.get_or_create_password(
        password_paths["ubersmith_db_pass"]
    )

    # 4. Self-signed certs for every virtual host (always generated,
    # regardless of the Let's Encrypt answer -- matching today's behavior).
    ssl_dir = ubersmith_home_path / "conf" / "ssl"
    for host in virtual_hosts:
        certs.generate_selfsigned_cert(host, ssl_dir)

    # 5. Config directories + static helper files/rules.
    docker_ops.create_config_directories(ubersmith_home_path, owner_uid, owner_gid)
    docker_ops.copy_static_files(ubersmith_home_path)
    docker_ops.copy_mysql_component_files(ubersmith_home_path)

    # 6. Render every template to its real destination.
    common_context = {
        "registry": DEFAULT_REGISTRY,
        "ubersmith_version": release["ubersmith_release_version"],
        "containers_release_version": release["containers"]["release_version"],
        "container_domain": main_virtual_host,
        "ubersmith_home": str(ubersmith_home_path),
        "ubersmith_major_version": ubersmith_major_version,
        "ubersmith_release": UBERSMITH_RELEASE,
        "mozilla_ciphers": DEFAULT_MOZILLA_CIPHERS,
        "pmm_version": DEFAULT_PMM_VERSION,
        "certbot_version": DEFAULT_CERTBOT_VERSION,
        "virtual_hosts": virtual_hosts,
        "notify_email": admin_email,
    }

    _write_rendered(
        ubersmith_home_path / "docker-compose.yml",
        templates.render_docker_compose(common_context),
        0o600,
        owner_uid,
        owner_gid,
    )

    dot_env_path = ubersmith_home_path / ".env"
    if not dot_env_path.exists():
        # Mirrors the "Create docker compose env file" task's `force: false`
        # -- only written the first time, never overwritten on re-runs.
        _write_rendered(
            dot_env_path,
            templates.render_dot_env(common_context),
            0o600,
            owner_uid,
            owner_gid,
        )

    _write_rendered(
        ubersmith_home_path / "docker-compose.override.yml",
        templates.render_docker_compose_override(
            {
                **common_context,
                "mysql_root_password": mysql_root_password,
                "mysql_ubersmith_password": mysql_ubersmith_password,
                "php_version": OVERRIDE_PHP_VERSION,
            }
        ),
        0o600,
        owner_uid,
        owner_gid,
    )

    _write_rendered(
        ubersmith_home_path / "conf" / "php" / "ubersmith.ini",
        templates.render_ubersmith_ini({**DEFAULT_INI_CONTEXT, **common_context}),
        0o644,
        owner_uid,
        owner_gid,
    )

    _write_rendered(
        ubersmith_home_path / "conf" / "rwhois" / "rwhois",
        templates.render_rwhois(
            {"ubersmith_root": UBERSMITH_ROOT, "main_virtual_host": main_virtual_host}
        ),
        0o644,
        owner_uid,
        owner_gid,
    )

    # Fresh installs always render the percona server config override --
    # the Ansible task's `when: ubersmith_installed_version is
    # version_compare('5.2.0', '<')` guard is satisfied by the default
    # ubersmith_installed_version (4.0.0) used when there's no prior state.
    _write_rendered(
        ubersmith_home_path / "conf" / "mysql" / "ubersmith.cnf",
        templates.render_mysql_cnf(ubersmith_major_version, {}),
        0o644,
        owner_uid,
        owner_gid,
    )
    _write_rendered(
        ubersmith_home_path / "conf" / "mysql" / "ubersmith_extra.cnf",
        templates.render_mysql_extra_cnf({}),
        0o644,
        owner_uid,
        owner_gid,
    )

    for host in virtual_hosts:
        _write_rendered(
            ubersmith_home_path / "conf" / "httpd" / "sites-enabled" / f"{host}.conf",
            templates.render_instance_vhost(
                {
                    "admin_email": admin_email,
                    "ubersmith_root": UBERSMITH_ROOT,
                    "item": host,
                    "fcgi_host": FCGI_HOST,
                    "mozilla_ciphers": DEFAULT_MOZILLA_CIPHERS,
                }
            ),
            0o640,
            owner_uid,
            owner_gid,
        )

    renewal_hooks_dir = (
        ubersmith_home_path / "conf" / "certbot" / "etc" / "renewal-hooks" / "deploy"
    )
    _write_rendered(
        renewal_hooks_dir / "ubersmith-deploy.sh",
        templates.render_ubersmith_deploy_hook({}),
        0o755,
        owner_uid,
        owner_gid,
    )
    _write_rendered(
        renewal_hooks_dir / "postfix-deploy.sh",
        templates.render_postfix_deploy_hook({}),
        0o755,
        owner_uid,
        owner_gid,
    )

    _write_rendered(
        ubersmith_home_path / "ubersmith_certbot_renew.sh",
        templates.render_certbot_renew_script({"ubersmith_home": str(ubersmith_home_path)}),
        0o700,
        owner_uid,
        owner_gid,
    )

    # 7. Stop/disable local MTAs (Ubersmith provides its own mail service).
    if not dry_run:
        mta.stop_and_disable_mtas(templates.get_os_family(), runner=subprocess.run)
    else:
        click.secho("Skipping MTA stop/disable (--dry-run).", fg="yellow")

    # 7b. Set systemd journal retention policy and restart journald to pick
    # it up (best-effort: no-op with a warning on non-systemd hosts or
    # without root, same as the Ansible task's practical behavior).
    if not dry_run:
        system_config.set_journald_retention()
        system_config.restart_systemd_journald()
    else:
        click.secho(
            "Skipping systemd journal retention policy (--dry-run).", fg="yellow"
        )

    # 8. Pull images, bring up containers, scale redis.
    if not dry_run:
        click.echo("Pulling images (this may take a few moments)...")
        docker_ops.pull_images(_image_refs(release, DEFAULT_CERTBOT_VERSION))

        click.echo("Starting Ubersmith containers...")
        docker_ops.compose_up(ubersmith_home_path)
        docker_ops.scale_redis(ubersmith_home_path)
        docker_ops.backup_mysql_keyring(ubersmith_home_path)
    else:
        click.secho(
            "Skipping image pull / docker compose up / redis scaling / "
            "mysql keyring backup (--dry-run).",
            fg="yellow",
        )

    # 9. Let's Encrypt certificates, if requested (self-signed certs from
    # step 4 remain in place either way).
    requested_lets_encrypt = prompts.is_lets_encrypt_requested(lets_encrypt_certificate)
    if not dry_run:
        if requested_lets_encrypt:
            click.echo("Requesting Let's Encrypt certificate(s)...")
        certbot.request_letsencrypt_certificates(
            virtual_hosts,
            ubersmith_home_path,
            admin_email,
            DEFAULT_CERTBOT_VERSION,
            lets_encrypt_certificate,
            uid=owner_uid,
            gid=owner_gid,
        )
    elif requested_lets_encrypt:
        click.secho(
            "Skipping Let's Encrypt certificate request (--dry-run); "
            "self-signed certificates remain in place.",
            fg="yellow",
        )

    # 10. Write the final install state.
    installer_state = state.InstallerState(
        ubersmith_home=str(ubersmith_home_path),
        virtual_host=virtual_host,
        admin_email=admin_email,
        lets_encrypt_certificate=lets_encrypt_certificate,
        ubersmith_installed_version=release["ubersmith_release_version"],
    )
    state.write_installer_state(installer_state, path=state_file)

    # 11. Success summary.
    click.echo()
    click.secho("Ubersmith install complete.", fg="green", bold=True)
    click.echo(f"  ubersmith_home              = {ubersmith_home_path}")
    click.echo(f"  virtual_host(s)             = {', '.join(virtual_hosts)}")
    click.echo(f"  admin_email                 = {admin_email}")
    click.echo(f"  ubersmith_installed_version = {release['ubersmith_release_version']}")
    click.echo(
        "  lets_encrypt_certificate    = "
        f"{lets_encrypt_certificate} ({'requested' if requested_lets_encrypt else 'self-signed only'})"
    )
    click.echo(f"  installer state written to  = {state_file}")
    if dry_run:
        click.secho(
            "  (--dry-run: Docker/MTA/Let's Encrypt side effects were skipped)",
            fg="yellow",
        )


@main.command()
@click.option(
    "--non-interactive",
    is_flag=True,
    default=False,
    help=(
        "Never block on the pre-upgrade / license / docker-compose.override.yml "
        "reminder prompts -- they are logged as informational messages instead. "
        "Also skips the legacy .patched/patches cleanup, which only ever runs "
        "interactively (matching patch_ubersmith's own behavior)."
    ),
)
@click.option(
    "--state-file",
    "state_file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=state.DEFAULT_STATE_PATH,
    show_default=True,
    help="Path to the installer state ini file to read/update.",
)
@click.option(
    "--skip-preflight",
    is_flag=True,
    default=False,
    help="Skip OS/Docker preflight checks (for testing in non-standard environments).",
)
def upgrade(
    non_interactive: bool,
    state_file: Path,
    skip_preflight: bool,
) -> None:
    """Upgrade an existing Ubersmith install.

    Reaches parity with ``upgrade_ubersmith.yml`` -t ``upgrade,upgrade_only``
    -- i.e. every task in ``roles/ubersmith/tasks/main.yml`` tagged
    ``upgrade`` or ``upgrade_only``. Unlike ``install``, this command always
    targets the current major version 5 release (``upgrade_ubersmith.yml``
    hardcodes ``ubersmith_major_version: "5"`` regardless of what was
    previously installed) and reads its configuration from the installer
    state file written by a prior ``install`` run, rather than prompting for
    it.

    CRITICAL: docker-compose.override.yml, the apache virtual host config,
    rwhois.j2, and ubersmith.ini.j2 are install-only templates -- they carry
    no "upgrade"/"upgrade_only" tag in the Ansible source and are NEVER
    wholesale re-rendered here. Only docker-compose.override.yml gets a
    handful of narrow, in-place text fixups (see
    :mod:`ubersmith_installer.compose_override`).
    """
    installer_state = state.read_state(path=state_file)
    required_fields = ("ubersmith_home", "virtual_host", "admin_email")
    missing_fields = [
        name for name in required_fields if getattr(installer_state, name) is None
    ]
    if missing_fields:
        click.secho(
            "Installer configuration is not present (missing: "
            f"{', '.join(missing_fields)} in {state_file}). Run `install` first.",
            fg="red",
            bold=True,
        )
        sys.exit(1)

    if not skip_preflight:
        click.echo("Running preflight checks...")
        result = preflight.run_preflight_checks(check_service_enabled=False)

        for warning in result.warnings:
            click.secho(f"  [WARN] {warning}", fg="yellow")

        if not result.ok:
            for error in result.errors:
                click.secho(f"  [FAIL] {error}", fg="red")
            click.secho("Preflight checks failed.", fg="red", bold=True)
            sys.exit(1)

        click.secho("Preflight checks passed.", fg="green")
    else:
        click.secho("Skipping preflight checks (--skip-preflight).", fg="yellow")

    # upgrade_ubersmith.yml hardcodes ubersmith_major_version: "5" -- upgrade
    # always targets the current major-5 release, regardless of what major
    # version was previously installed.
    ubersmith_major_version = "5"
    release = UBERSMITH_RELEASE[ubersmith_major_version]

    ubersmith_home_path = Path(installer_state.ubersmith_home)
    virtual_hosts = [
        h.strip() for h in installer_state.virtual_host.split(",") if h.strip()
    ]
    main_virtual_host = virtual_hosts[0] if virtual_hosts else installer_state.virtual_host
    admin_email = installer_state.admin_email
    # Mirrors the default used when no prior state exists (see `install`);
    # in practice an upgrade target always has this set by a prior install.
    old_installed_version = installer_state.ubersmith_installed_version or "4.0.0"

    interactive = not non_interactive
    owner_uid = os.getuid() if hasattr(os, "getuid") else 0
    owner_gid = os.getgid() if hasattr(os, "getgid") else 0

    # "Check ubersmith containers before proceeding with upgrade" -- a
    # failsafe ensuring the bare-minimum containers are running before doing
    # anything else, in case a prior upgrade attempt failed partway through.
    # The Ansible task runs bare `docker compose up -d web db php` (no
    # --quiet-pull); passing --no-color here is a harmless, cosmetic-only
    # deviation (output coloring only), not a functional one.
    docker_ops.compose_up(
        ubersmith_home_path, services=["web", "db", "php"], quiet_pull=False
    )

    # MySQL passwords must already exist from the original install --
    # get_or_create_password reads the existing files rather than
    # regenerating them.
    password_paths = secrets.ubersmith_password_paths(Path.home(), main_virtual_host)
    mysql_root_password = secrets.get_or_create_password(password_paths["root_db_pass"])
    mysql_ubersmith_password = secrets.get_or_create_password(
        password_paths["ubersmith_db_pass"]
    )

    # "Print administrator reminders" (release_notes_prompt/upgrade_only).
    prompts.show_pre_upgrade_reminder(interactive)

    # Remove legacy patch_ubersmith.sh artifacts (interactive-only).
    patch_cleanup.cleanup_legacy_patches(
        ubersmith_home_path, release["ubersmith_release_version"], interactive
    )

    # "Check for remote database" -- determines local-vs-remote database
    # topology, gating several later upgrade_only steps.
    web_container_env = docker_ops.get_web_container_env()
    is_local_database = docker_ops.is_local_database(web_container_env)

    # Phase 1 of the redis volume migration (must run before the existing
    # containers are stopped, further down). A True return means phase 3
    # (copy_redis_dump_in + chown_redis_dump) must run again later, once the
    # new redis-data container exists.
    redis_migration_needed = redis_migration.migrate_redis_volume(ubersmith_home_path)

    # "Update mysql to use caching_sha2_password" (only meaningful for local
    # databases predating 5.2.0 -- run_migrations checks both internally)
    # and the defensive sql_mode fixup (applies to whatever's on disk right
    # now, whether that's the pre-existing file or one about to be
    # re-rendered below -- either way the end result is the same).
    ran_migrations = migrations.run_migrations(
        ubersmith_home_path,
        mysql_root_password,
        mysql_ubersmith_password,
        old_installed_version,
        is_local_database,
        ubersmith_major_version=ubersmith_major_version,
    )
    for name in ran_migrations:
        click.echo(f"Ran migration: {name}")

    # "Alert admin to necessary license updates" -- gated `when: interactive`
    # in the Ansible source just like the other two reminders above/below,
    # so it gets the same blocking-confirm-vs-informational-echo treatment.
    license_message = migrations.license_update_reminder(old_installed_version)
    if license_message is not None:
        if interactive:
            click.echo(license_message)
            click.confirm("Continue with the upgrade?", default=True, abort=True)
        else:
            click.echo(f"[info] {license_message}")

    # "Create ubersmith configuration directories" is tagged plain `upgrade`
    # (not `upgrade_only`), so Ansible re-runs it on every upgrade too --
    # idempotent mkdir/chmod/chown, guards against directories that drifted
    # or were never created (e.g. conf/sso, app/custom/*) since the original
    # install.
    docker_ops.create_config_directories(ubersmith_home_path, owner_uid, owner_gid)

    # Re-render every template that IS re-rendered on upgrade (plain
    # `upgrade` tag, no `upgrade_only`/install-only gate) -- reusing Phase
    # 1's exact render functions.
    common_context = {
        "registry": DEFAULT_REGISTRY,
        "ubersmith_version": release["ubersmith_release_version"],
        "containers_release_version": release["containers"]["release_version"],
        "container_domain": main_virtual_host,
        "ubersmith_home": str(ubersmith_home_path),
        "ubersmith_major_version": ubersmith_major_version,
        "ubersmith_release": UBERSMITH_RELEASE,
        "mozilla_ciphers": DEFAULT_MOZILLA_CIPHERS,
        "pmm_version": DEFAULT_PMM_VERSION,
        "certbot_version": DEFAULT_CERTBOT_VERSION,
        "virtual_hosts": virtual_hosts,
        "notify_email": admin_email,
    }

    _write_rendered(
        ubersmith_home_path / "docker-compose.yml",
        templates.render_docker_compose(common_context),
        0o600,
        owner_uid,
        owner_gid,
    )

    # ".env" is deliberately NOT re-rendered here. The Ansible "Create
    # docker compose env file" task uses `force: false` (only written the
    # first time), and on any real upgrade target it already exists from the
    # original install -- so re-running it is always a no-op in the Ansible
    # source too, not something this codebase is skipping/simplifying.

    # "Create percona server configuration overrides" only fires when the
    # *previously installed* version predates 5.2.0.
    if not preflight.version_gte(old_installed_version, MYSQL_CNF_RERENDER_MAX_VERSION):
        _write_rendered(
            ubersmith_home_path / "conf" / "mysql" / "ubersmith.cnf",
            templates.render_mysql_cnf(ubersmith_major_version, {}),
            0o644,
            owner_uid,
            owner_gid,
        )

    _write_rendered(
        ubersmith_home_path / "conf" / "mysql" / "ubersmith_extra.cnf",
        templates.render_mysql_extra_cnf({}),
        0o644,
        owner_uid,
        owner_gid,
    )

    docker_ops.copy_mysql_component_files(ubersmith_home_path)

    renewal_hooks_dir = (
        ubersmith_home_path / "conf" / "certbot" / "etc" / "renewal-hooks" / "deploy"
    )
    _write_rendered(
        renewal_hooks_dir / "ubersmith-deploy.sh",
        templates.render_ubersmith_deploy_hook({}),
        0o755,
        owner_uid,
        owner_gid,
    )
    _write_rendered(
        renewal_hooks_dir / "postfix-deploy.sh",
        templates.render_postfix_deploy_hook({}),
        0o755,
        owner_uid,
        owner_gid,
    )

    _write_rendered(
        ubersmith_home_path / "ubersmith_certbot_renew.sh",
        templates.render_certbot_renew_script({"ubersmith_home": str(ubersmith_home_path)}),
        0o700,
        owner_uid,
        owner_gid,
    )

    # "Create certbot container cron task" -- only re-installed if Let's
    # Encrypt is still requested per the existing installer state.
    requested_lets_encrypt = prompts.is_lets_encrypt_requested(
        installer_state.lets_encrypt_certificate
    )
    if requested_lets_encrypt:
        certbot.install_renewal_cron_task(ubersmith_home_path)

    # copy_static_files() unconditionally overwrites ubersmith_start.sh from
    # the currently-shipped version, which already hardcodes `--scale
    # redis=3` -- this makes the Ansible source's separate "Ensure redis
    # line exists" lineinfile fixup redundant given this codebase's
    # full-file-copy approach (the fixup is a no-op in the Ansible source
    # too, for the same reason: it always runs immediately after an
    # equivalent full copy of ubersmith_start.sh).
    docker_ops.copy_static_files(ubersmith_home_path)
    system_config.set_journald_retention()
    system_config.restart_systemd_journald()

    # Narrow, in-place fixups to the EXISTING docker-compose.override.yml --
    # never wholesale re-rendered (see CRITICAL note in this command's
    # docstring).
    compose_override.apply_legacy_override_fixups(
        ubersmith_home_path / "docker-compose.override.yml",
        OLD_PHP_VERSIONS,
        OVERRIDE_PHP_VERSION,
    )

    # "Give the administrator a chance to update docker-compose.override.yml"
    # (only fires when installed_version > 4.6.0, per the function's own gate).
    prompts.show_compose_override_reminder(interactive, old_installed_version)

    if is_local_database:
        docker_ops.chown_database_files()

    click.echo("Stopping existing containers...")
    docker_ops.stop_containers(ubersmith_home_path)

    redis_migration.remove_webroot_volume_if_present()

    click.echo("Pulling images (this may take a few moments)...")
    docker_ops.pull_images(_image_refs(release, DEFAULT_CERTBOT_VERSION))

    click.echo("Starting Ubersmith containers (maintenance mode enabled)...")
    docker_ops.compose_up(ubersmith_home_path, extra_env={"MAINTENANCE": "1"})
    docker_ops.scale_redis(ubersmith_home_path)

    # Phase 3 of the redis volume migration -- only after the new
    # redis-data container exists.
    if redis_migration_needed:
        redis_migration.copy_redis_dump_in(ubersmith_home_path)
        redis_migration.chown_redis_dump()

    if is_local_database:
        click.echo("Waiting for containers to come online...")
        docker_ops.wait_for_containers_healthy(
            ["ubersmith-web-1", "ubersmith-php-1", "ubersmith-solr-1"]
        )
        docker_ops.check_database_container_healthy()

    click.echo("Running updatedb.php...")
    stdout, stderr = docker_ops.run_updatedb(UBERSMITH_ROOT)
    click.echo(stderr)
    click.echo(stdout)

    docker_ops.remove_setup_dir(UBERSMITH_ROOT)

    click.echo("Starting web container (maintenance mode disabled)...")
    docker_ops.compose_up(
        ubersmith_home_path,
        extra_env={"MAINTENANCE": "0"},
        services=["web"],
        quiet_pull=False,
    )

    docker_ops.backup_mysql_keyring(ubersmith_home_path)
    docker_ops.prune_old_images()

    updated_state = state.InstallerState(
        ubersmith_installed_version=release["ubersmith_release_version"],
        lets_encrypt_certificate=installer_state.lets_encrypt_certificate,
    )
    state.write_installer_state(updated_state, path=state_file)

    click.echo()
    click.secho("Ubersmith upgrade complete.", fg="green", bold=True)
    click.echo(f"  ubersmith_home              = {ubersmith_home_path}")
    click.echo(f"  virtual_host(s)             = {', '.join(virtual_hosts)}")
    click.echo(f"  admin_email                 = {admin_email}")
    click.echo(f"  upgraded from version       = {old_installed_version}")
    click.echo(f"  ubersmith_installed_version = {release['ubersmith_release_version']}")
    click.echo(f"  database topology           = {'local' if is_local_database else 'remote'}")
    click.echo(f"  installer state written to  = {state_file}")


@main.command(name="install-appliance")
@click.option(
    "--ubersmith-major-version",
    "ubersmith_major_version",
    default=None,
    help="Choose which version of Ubersmith's Appliance to install (4 or 5).",
)
@click.option(
    "--appliance-home",
    "appliance_home",
    default=None,
    help="Choose an installation directory for Ubersmith's Appliance.",
)
@click.option(
    "--app-virtual-host",
    "app_virtual_host",
    default=None,
    help="Enter the domain name associated with your Ubersmith installation.",
)
@click.option(
    "--non-interactive",
    is_flag=True,
    default=False,
    help=(
        "Never prompt. All 3 install values (--ubersmith-major-version, "
        "--appliance-home, --app-virtual-host) must be supplied; otherwise "
        "the command aborts with an error instead of prompting."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help=(
        "Skip Docker/network side effects (image pulls, `docker compose "
        "pull`/`up`, waiting for containers healthy, and configuring the "
        "uberapp/xml-rpc user password). Directories, self-signed certs, "
        "rendered config files, and the state file are still written for "
        "real under --appliance-home. Intended for exercising this command "
        "in CI/tests without requiring root, a Docker daemon, or touching a "
        "real system."
    ),
)
@click.option(
    "--state-file",
    "state_file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=state.DEFAULT_STATE_PATH,
    show_default=True,
    help="Path to the installer state ini file to write.",
)
@click.option(
    "--skip-preflight",
    is_flag=True,
    default=False,
    help="Skip OS/Docker preflight checks (for testing in non-standard environments).",
)
def install_appliance(
    ubersmith_major_version: str | None,
    appliance_home: str | None,
    app_virtual_host: str | None,
    non_interactive: bool,
    dry_run: bool,
    state_file: Path,
    skip_preflight: bool,
) -> None:
    """Install the Ubersmith Appliance.

    Reaches parity with ``install_appliance.yml`` + ``roles/common`` + the
    fresh-install-scope tasks (every task NOT tagged ``upgrade_only``) in
    ``roles/appliance/tasks/main.yml``: runs preflight checks, gathers the 3
    install-time values, generates/reads the appliance's MySQL passwords and
    a self-signed cert, creates the appliance directory tree and static
    helper files, renders every appliance config template to its real
    destination under ``--appliance-home``, pulls images and brings up
    containers, configures the appliance's uberapp/xml-rpc user password,
    and writes the installer state file.
    """
    required_names = [name for name, _, _ in prompts.APPLIANCE_INSTALL_PROMPTS]
    supplied = {
        "ubersmith_major_version": ubersmith_major_version,
        "appliance_home": appliance_home,
        "app_virtual_host": app_virtual_host,
    }
    provided = {name: value for name, value in supplied.items() if value is not None}

    if len(provided) == len(required_names):
        answers = provided
    elif non_interactive:
        missing = [name for name in required_names if name not in provided]
        flags = ", ".join(f"--{name.replace('_', '-')}" for name in missing)
        click.secho(
            f"--non-interactive was given but the following value(s) are missing: {flags}",
            fg="red",
            bold=True,
        )
        sys.exit(2)
    else:
        answers = prompts.prompt_for_appliance_install_values(defaults=provided)

    ubersmith_major_version = answers["ubersmith_major_version"]
    appliance_home = answers["appliance_home"]
    app_virtual_host = answers["app_virtual_host"]

    if not skip_preflight:
        click.echo("Running preflight checks...")
        result = preflight.run_preflight_checks(check_service_enabled=False)

        for warning in result.warnings:
            click.secho(f"  [WARN] {warning}", fg="yellow")

        if not result.ok:
            for error in result.errors:
                click.secho(f"  [FAIL] {error}", fg="red")
            click.secho("Preflight checks failed.", fg="red", bold=True)
            sys.exit(1)

        click.secho("Preflight checks passed.", fg="green")
    else:
        click.secho("Skipping preflight checks (--skip-preflight).", fg="yellow")

    release = APPLIANCE_RELEASE.get(ubersmith_major_version)
    if release is None:
        click.secho(
            f"Unsupported ubersmith_major_version: {ubersmith_major_version!r} "
            f"(expected one of {sorted(APPLIANCE_RELEASE)}).",
            fg="red",
            bold=True,
        )
        sys.exit(1)

    appliance_home_path = Path(appliance_home)
    owner_uid = os.getuid() if hasattr(os, "getuid") else 0
    owner_gid = os.getgid() if hasattr(os, "getgid") else 0

    # MySQL/xml-rpc passwords -- lookup('password', ...) equivalent (see
    # roles/appliance/vars/main.yml). `appliance_user_pass`
    # (mysql_appliance_user_password) is deliberately never generated here:
    # it's defined in the Ansible source but never actually referenced by
    # any task or template in the role (Ansible vars are lazily evaluated,
    # so its lookup() is never triggered in practice) -- generating it would
    # create a password file the real installer never creates.
    password_paths = secrets.appliance_password_paths(Path.home(), app_virtual_host)
    mysql_root_password = secrets.get_or_create_password(password_paths["root_db_pass"])
    mysql_appliance_password = secrets.get_or_create_password(
        password_paths["appliance_db_pass"]
    )
    uberapp_user_password = secrets.get_or_create_password(
        password_paths["appliance_xmlrpc_pass"]
    )

    # Self-signed certificate for the appliance's virtual host.
    ssl_dir = appliance_home_path / "conf" / "ssl"
    certs.generate_selfsigned_cert(app_virtual_host, ssl_dir)

    # Config directories + static helper files.
    appliance_ops.create_config_directories(appliance_home_path, owner_uid, owner_gid)
    appliance_ops.copy_static_files(appliance_home_path)

    # Render every appliance template to its real destination.
    common_context = {
        "registry": DEFAULT_REGISTRY,
        "appliance_release": APPLIANCE_RELEASE,
        "ubersmith_major_version": ubersmith_major_version,
        "appliance_version": release["appliance_release_version"],
        "containers_release_version": release["containers"]["release_version"],
        "app_virtual_host": app_virtual_host,
        "appliance_home": str(appliance_home_path),
    }

    # "Create ubersmith apache virtual host configuration file" -- force:
    # false, so only written the first time.
    vhost_path = appliance_home_path / "conf" / "httpd" / "sites-enabled" / "appliance.conf"
    if not vhost_path.exists():
        _write_rendered(
            vhost_path,
            templates.render_appliance_vhost(
                {"appliance_root": APPLIANCE_ROOT, "app_virtual_host": app_virtual_host}
            ),
            0o640,
            owner_uid,
            owner_gid,
        )

    # Fresh installs always render the percona server config override --
    # this task carries no upgrade/upgrade_only tag (mysql_config only), so
    # it is install-only (see CRITICAL note on `upgrade_appliance`).
    _write_rendered(
        appliance_home_path / "conf" / "mysql" / "ubersmith.cnf",
        templates.render_appliance_mysql_cnf({}, ubersmith_major_version),
        0o644,
        owner_uid,
        owner_gid,
    )

    _write_rendered(
        appliance_home_path / "docker-compose.yml",
        templates.render_appliance_docker_compose(common_context),
        0o600,
        owner_uid,
        owner_gid,
    )

    # "Create docker compose override file" also carries no upgrade/
    # upgrade_only tag (compose_file only) -- install-only, same as above.
    override_path = appliance_home_path / "docker-compose.override.yml"
    _write_rendered(
        override_path,
        templates.render_appliance_docker_compose_override(
            {
                **common_context,
                "mysql_appliance_password": mysql_appliance_password,
                "mysql_root_password": mysql_root_password,
            }
        ),
        0o600,
        owner_uid,
        owner_gid,
    )

    # The two narrow legacy override fixups are tagged plain `upgrade` (not
    # `upgrade_only`), so Ansible runs them right after rendering the file
    # on a fresh install too -- always a no-op here since the freshly
    # rendered file already has the current content.
    appliance_compose_override.apply_legacy_override_fixups(
        override_path, appliance_home_path, app_virtual_host
    )

    if not dry_run:
        click.echo("Pulling images (this may take a few moments)...")
        docker_ops.pull_images(_appliance_image_refs(release))
        appliance_ops.compose_pull(appliance_home_path)

        click.echo("Starting appliance containers...")
        appliance_ops.compose_up(appliance_home_path)
        appliance_ops.wait_for_containers_healthy()
    else:
        click.secho(
            "Skipping image pull / docker compose pull / up / wait "
            "(--dry-run).",
            fg="yellow",
        )

    # Deliberate fix, not a faithful port: the Ansible "Database upgrade
    # successful, set value in ini file for future use" task is tagged
    # plain `upgrade` (not `upgrade_only`), so it *also* runs during a
    # fresh install and unconditionally writes app_mysql_version=8.0 --
    # regardless of which major version was actually installed (even a
    # fresh major-4 install, which runs mysql 5.7, gets this wrong value).
    # Since upgrade_appliance's own `lookup('ini', ..., default=5.6')` only
    # falls back to "5.6" when the key is ABSENT, this bug means the mysql
    # 5.6->5.7 step-up migration can never fire for any appliance that went
    # through a normal install-then-upgrade lifecycle via the real Ansible
    # tool -- it always sees "8.0" already recorded from install. Fixed
    # here by recording the version that's actually installed (derived from
    # this major version's `mysql_version`, e.g. 57 -> "5.7", 80 -> "8.0")
    # instead of a hardcoded "8.0".
    installed_mysql_version = f"{release['mysql_version'] // 10}.{release['mysql_version'] % 10}"
    state.write_state({"app_mysql_version": installed_mysql_version}, path=state_file)

    if not dry_run:
        appliance_ops.configure_uberapp_user_password(
            mysql_appliance_password, uberapp_user_password
        )
    else:
        click.secho(
            "Skipping uberapp user password configuration (--dry-run).",
            fg="yellow",
        )

    # "Output appliance xml-rpc username and password".
    click.echo()
    click.secho("*** PLEASE NOTE ***", fg="yellow", bold=True)
    click.echo("The appliance user has been configured with the following credentials:")
    click.echo("username: ubersmith")
    click.echo(f"password: {uberapp_user_password}")
    click.echo("Please use these values to configure the appliance entry in Ubersmith.")

    installer_state = state.InstallerState(
        ubersmith_home=str(appliance_home_path),
        virtual_host=app_virtual_host,
        appliance_home=str(appliance_home_path),
        app_virtual_host=app_virtual_host,
        appliance_installed_version=release["appliance_release_version"],
        app_mysql_version=installed_mysql_version,
    )
    state.write_installer_state(installer_state, path=state_file)

    click.echo()
    click.secho("Ubersmith Appliance install complete.", fg="green", bold=True)
    click.echo(f"  appliance_home              = {appliance_home_path}")
    click.echo(f"  app_virtual_host            = {app_virtual_host}")
    click.echo(f"  appliance_installed_version = {release['appliance_release_version']}")
    click.echo(f"  installer state written to  = {state_file}")
    if dry_run:
        click.secho(
            "  (--dry-run: Docker side effects were skipped)",
            fg="yellow",
        )


@main.command(name="upgrade-appliance")
@click.option(
    "--non-interactive",
    is_flag=True,
    default=False,
    help=(
        "Never block on the pre-upgrade reminder prompt -- it is logged as "
        "an informational message instead."
    ),
)
@click.option(
    "--state-file",
    "state_file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=state.DEFAULT_STATE_PATH,
    show_default=True,
    help="Path to the installer state ini file to read/update.",
)
@click.option(
    "--skip-preflight",
    is_flag=True,
    default=False,
    help="Skip OS/Docker preflight checks (for testing in non-standard environments).",
)
def upgrade_appliance(
    non_interactive: bool,
    state_file: Path,
    skip_preflight: bool,
) -> None:
    """Upgrade an existing Ubersmith Appliance install.

    Reaches parity with ``upgrade_appliance.yml`` -t ``upgrade,upgrade_only``
    -- i.e. every task in ``roles/appliance/tasks/main.yml`` tagged
    ``upgrade`` or ``upgrade_only``. Like ``upgrade_appliance.yml`` itself,
    this always targets the current major version 5 release regardless of
    what was previously installed, and reads its configuration from the
    installer state file written by a prior ``install-appliance`` run.

    CRITICAL: the percona server config override (``conf/mysql/ubersmith.cnf``,
    tagged ``mysql_config`` only) and ``docker-compose.override.yml`` (tagged
    ``compose_file`` only) are install-only templates -- they carry no
    "upgrade"/"upgrade_only" tag in the Ansible source and are NEVER
    wholesale re-rendered here. Only docker-compose.override.yml gets two
    narrow, in-place text fixups (see
    :mod:`ubersmith_installer.appliance_compose_override`). Likewise,
    "Configure uberapp user password" and "Output appliance xml-rpc username
    and password" are tagged ``password`` only (no upgrade/upgrade_only tag)
    -- the xml-rpc user password is configured once at install time and is
    never reset or re-displayed by an upgrade.
    """
    installer_state = state.read_state(path=state_file)
    required_fields = ("appliance_home", "app_virtual_host")
    missing_fields = [
        name for name in required_fields if getattr(installer_state, name) is None
    ]
    if missing_fields:
        click.secho(
            "Installer configuration is not present (missing: "
            f"{', '.join(missing_fields)} in {state_file}). Run "
            "`install-appliance` first.",
            fg="red",
            bold=True,
        )
        sys.exit(1)

    if not skip_preflight:
        click.echo("Running preflight checks...")
        result = preflight.run_preflight_checks(check_service_enabled=False)

        for warning in result.warnings:
            click.secho(f"  [WARN] {warning}", fg="yellow")

        if not result.ok:
            for error in result.errors:
                click.secho(f"  [FAIL] {error}", fg="red")
            click.secho("Preflight checks failed.", fg="red", bold=True)
            sys.exit(1)

        click.secho("Preflight checks passed.", fg="green")
    else:
        click.secho("Skipping preflight checks (--skip-preflight).", fg="yellow")

    # upgrade_appliance.yml hardcodes ubersmith_major_version: "5".
    ubersmith_major_version = "5"
    release = APPLIANCE_RELEASE[ubersmith_major_version]

    appliance_home_path = Path(installer_state.appliance_home)
    app_virtual_host = installer_state.app_virtual_host
    # Mirrors upgrade_appliance.yml's `lookup('ini', ... default=5.6)`.
    app_mysql_version = installer_state.app_mysql_version or "5.6"

    interactive = not non_interactive
    owner_uid = os.getuid() if hasattr(os, "getuid") else 0
    owner_gid = os.getgid() if hasattr(os, "getgid") else 0

    # "Remind admin to make a backup before proceeding with an upgrade"
    # (upgrade_only).
    prompts.show_appliance_pre_upgrade_reminder(interactive)

    click.echo("Pulling images (this may take a few moments)...")
    docker_ops.pull_images(_appliance_image_refs(release))

    # "Check ubersmith containers before proceeding with upgrade" -- a
    # failsafe ensuring the bare-minimum containers are running before doing
    # anything else, in case a prior upgrade attempt failed partway through.
    appliance_ops.compose_up(
        appliance_home_path, services=["app_web", "app_db", "app_cron"]
    )

    # MySQL/xml-rpc passwords must already exist from the original install.
    password_paths = secrets.appliance_password_paths(Path.home(), app_virtual_host)
    mysql_root_password = secrets.get_or_create_password(password_paths["root_db_pass"])
    mysql_appliance_password = secrets.get_or_create_password(
        password_paths["appliance_db_pass"]
    )

    # "Create appliance configuration directories" is tagged plain `upgrade`,
    # so it's re-run on every upgrade too -- idempotent mkdir/chmod/chown.
    appliance_ops.create_config_directories(appliance_home_path, owner_uid, owner_gid)

    common_context = {
        "registry": DEFAULT_REGISTRY,
        "appliance_release": APPLIANCE_RELEASE,
        "ubersmith_major_version": ubersmith_major_version,
        "appliance_version": release["appliance_release_version"],
        "containers_release_version": release["containers"]["release_version"],
        "app_virtual_host": app_virtual_host,
        "appliance_home": str(appliance_home_path),
    }

    _write_rendered(
        appliance_home_path / "docker-compose.yml",
        templates.render_appliance_docker_compose(common_context),
        0o600,
        owner_uid,
        owner_gid,
    )

    # "Create ubersmith apache virtual host configuration file" -- force:
    # false, so only written if it doesn't already exist.
    vhost_path = appliance_home_path / "conf" / "httpd" / "sites-enabled" / "appliance.conf"
    if not vhost_path.exists():
        _write_rendered(
            vhost_path,
            templates.render_appliance_vhost(
                {"appliance_root": APPLIANCE_ROOT, "app_virtual_host": app_virtual_host}
            ),
            0o640,
            owner_uid,
            owner_gid,
        )

    # conf/mysql/ubersmith.cnf and docker-compose.override.yml are NEVER
    # wholesale re-rendered here -- see CRITICAL note in this command's
    # docstring. Only narrow, in-place fixups to the EXISTING override file.
    override_path = appliance_home_path / "docker-compose.override.yml"
    appliance_compose_override.apply_legacy_override_fixups(
        override_path, appliance_home_path, app_virtual_host
    )

    # copy_static_files() unconditionally re-copies backup_rrds.sh too (an
    # install-only, untagged task in the Ansible source) -- harmless since
    # it's always the same shipped static file, matching the same
    # simplification `upgrade`'s copy_static_files() call makes for
    # ubersmith_start.sh (see that command's comment).
    appliance_ops.copy_static_files(appliance_home_path)

    # "Check for remote database".
    app_web_env = appliance_ops.get_app_web_container_env()
    is_local_database = appliance_ops.is_local_database(app_web_env)
    click.echo(
        f"Database topology: {'local' if is_local_database else 'remote'} "
        f"(ini-tracked app_mysql_version={app_mysql_version!r}, not used for "
        "the mysql 5.7 step-up decision -- see below)"
    )

    # "Create self signed certificates" -- tagged plain `upgrade`; the
    # Ansible task has a `creates:` guard this codebase doesn't replicate
    # (regenerates unconditionally), matching the same simplification
    # already accepted for `install`/`install-appliance`.
    ssl_dir = appliance_home_path / "conf" / "ssl"
    certs.generate_selfsigned_cert(app_virtual_host, ssl_dir)

    # Determine, from the CURRENTLY RUNNING app_db container's own image
    # reference, whether the mysql 5.7 step-up is needed -- deliberately not
    # trusted from the ini-tracked app_mysql_version, since (a) the Ansible
    # source has a latent bug where a fresh install always records "8.0"
    # regardless of what was actually installed, and (b) even a "fresh"
    # ps57 (mysql 5.7) image has been observed in practice to initialize
    # data using a pre-5.7.9 redo log format that mysql 8.0 cannot read, so
    # the real running state -- not a version label -- is what matters.
    # Must happen before the container is stopped/replaced below.
    running_app_db_image = appliance_ops.get_running_app_db_image()
    needs_mysql_57_stepup = is_local_database and appliance_ops.is_pre_mysql_8_image(
        running_app_db_image
    )
    click.echo(
        f"Running app_db image: {running_app_db_image!r} -- mysql 5.7 "
        f"step-up needed: {needs_mysql_57_stepup}"
    )

    click.echo("Pulling images via docker compose...")
    appliance_ops.compose_pull(appliance_home_path)

    click.echo("Stopping existing containers...")
    appliance_ops.stop_containers(appliance_home_path)

    volumes = appliance_ops.get_existing_volumes(appliance_home_path)
    appliance_ops.remove_webroot_volume_if_present(appliance_home_path, volumes)

    if is_local_database:
        appliance_ops.chown_database_files()

    # step_up_mysql_57's own gate checks its `app_mysql_version` argument
    # for the literal string "5.6" -- feed it a value reflecting the
    # image-based decision above (`needs_mysql_57_stepup`), not the
    # ini-tracked `app_mysql_version` (see the comment above for why that's
    # unreliable).
    stepped_up = appliance_ops.step_up_mysql_57(
        DEFAULT_REGISTRY,
        release["appliance_release_version"],
        release["containers"]["release_version"],
        "5.6" if needs_mysql_57_stepup else "8.0",
        is_local_database,
    )
    click.echo(f"mysql 5.7 step-up: {'ran' if stepped_up else 'skipped'}")

    click.echo("Starting appliance containers...")
    appliance_ops.compose_up(appliance_home_path)

    click.echo("Waiting for containers to come online...")
    appliance_ops.wait_for_containers_healthy()

    # "Database upgrade successful, set value in ini file for future use".
    state.write_state({"app_mysql_version": "8.0"}, path=state_file)

    click.echo("Running upgrade.php...")
    appliance_ops.run_upgrade_php(appliance_home_path)

    appliance_ops.prune_old_images()

    updated_state = state.InstallerState(
        appliance_installed_version=release["appliance_release_version"],
    )
    state.write_installer_state(updated_state, path=state_file)

    click.echo()
    click.secho("Ubersmith Appliance upgrade complete.", fg="green", bold=True)
    click.echo(f"  appliance_home              = {appliance_home_path}")
    click.echo(f"  app_virtual_host            = {app_virtual_host}")
    click.echo(f"  appliance_installed_version = {release['appliance_release_version']}")
    click.echo(f"  database topology           = {'local' if is_local_database else 'remote'}")
    click.echo(f"  installer state written to  = {state_file}")


@main.command()
@click.option(
    "--ubersmith-home",
    "ubersmith_home",
    default=None,
    help="Current path in use by Ubersmith / Appliance.",
)
@click.option(
    "--virtual-host",
    "virtual_host",
    default=None,
    help=(
        "Enter the address in use by Ubersmith / Appliance; for multiple "
        "hostnames use a comma delimited list."
    ),
)
@click.option(
    "--admin-email",
    "admin_email",
    default=None,
    help="Enter the email address of the Ubersmith administrator.",
)
@click.option(
    "--non-interactive",
    is_flag=True,
    default=False,
    help=(
        "Never prompt. All 3 values (--ubersmith-home, --virtual-host, "
        "--admin-email) must be supplied; otherwise the command aborts with "
        "an error instead of prompting."
    ),
)
@click.option(
    "--state-file",
    "state_file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=state.DEFAULT_STATE_PATH,
    show_default=True,
    help="Path to the installer state ini file to write.",
)
def configure(
    ubersmith_home: str | None,
    virtual_host: str | None,
    admin_email: str | None,
    non_interactive: bool,
    state_file: Path,
) -> None:
    """Reconfigure an existing Ubersmith install's stored settings.

    Reaches parity with ``configure.yml``: gathers ``ubersmith_home``,
    ``virtual_host``, and ``admin_email`` (interactively by default,
    matching today's ``vars_prompt`` UX -- note the prompt text differs
    slightly from ``install``'s for ``ubersmith_home``/``virtual_host``),
    confirms ``ubersmith_home`` contains an existing installation, and
    writes the values (plus mirrored ``appliance_home``/``app_virtual_host``
    copies) into the installer state file.
    """
    required_names = [name for name, _, _ in prompts.CONFIGURE_PROMPTS]
    supplied = {
        "ubersmith_home": ubersmith_home,
        "virtual_host": virtual_host,
        "admin_email": admin_email,
    }
    provided = {name: value for name, value in supplied.items() if value is not None}

    if len(provided) == len(required_names):
        answers = provided
    elif non_interactive:
        missing = [name for name in required_names if name not in provided]
        flags = ", ".join(f"--{name.replace('_', '-')}" for name in missing)
        click.secho(
            f"--non-interactive was given but the following value(s) are missing: {flags}",
            fg="red",
            bold=True,
        )
        sys.exit(2)
    else:
        answers = prompts.prompt_for_configure_values(defaults=provided)

    ubersmith_home = answers["ubersmith_home"]
    virtual_host = answers["virtual_host"]
    admin_email = answers["admin_email"]

    try:
        configure_state.reconfigure(
            ubersmith_home, virtual_host, admin_email, state_file=state_file
        )
    except ValueError as exc:
        click.secho(str(exc), fg="red", bold=True)
        sys.exit(1)

    click.echo()
    click.secho("Ubersmith configuration updated.", fg="green", bold=True)
    click.echo(f"  ubersmith_home              = {ubersmith_home}")
    click.echo(f"  virtual_host                = {virtual_host}")
    click.echo(f"  admin_email                 = {admin_email}")
    click.echo(f"  installer state written to  = {state_file}")


@main.command(name="retry-letsencrypt")
@click.option(
    "--non-interactive",
    is_flag=True,
    default=False,
    help=(
        "No-op today -- this command has no interactive prompts of its own "
        "(retry_letsencrypt.yml has none either). Accepted for consistency "
        "with the other subcommands' flag conventions."
    ),
)
@click.option(
    "--state-file",
    "state_file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=state.DEFAULT_STATE_PATH,
    show_default=True,
    help="Path to the installer state ini file to read.",
)
@click.option(
    "--skip-preflight",
    is_flag=True,
    default=False,
    help="Skip OS/Docker preflight checks (for testing in non-standard environments).",
)
def retry_letsencrypt(
    non_interactive: bool,
    state_file: Path,
    skip_preflight: bool,
) -> None:
    """Retry the Let's Encrypt certificate request for an existing install.

    Reaches parity with ``retry_letsencrypt.yml``: reads the existing
    installer state (failing clearly, same pattern as ``upgrade``, if it's
    missing), then requests certificates via the webroot method for every
    configured virtual host, runs the deploy hooks, gracefully reloads
    Apache, and re-installs the daily renewal cron task.
    """
    installer_state = state.read_state(path=state_file)
    required_fields = (
        "ubersmith_home",
        "virtual_host",
        "admin_email",
        "ubersmith_installed_version",
    )
    missing_fields = [
        name for name in required_fields if getattr(installer_state, name) is None
    ]
    if missing_fields:
        click.secho(
            "Installer configuration is not present (missing: "
            f"{', '.join(missing_fields)} in {state_file}). Run `install` first.",
            fg="red",
            bold=True,
        )
        sys.exit(1)

    if not skip_preflight:
        click.echo("Running preflight checks...")
        result = preflight.run_preflight_checks(check_service_enabled=False)

        for warning in result.warnings:
            click.secho(f"  [WARN] {warning}", fg="yellow")

        if not result.ok:
            for error in result.errors:
                click.secho(f"  [FAIL] {error}", fg="red")
            click.secho("Preflight checks failed.", fg="red", bold=True)
            sys.exit(1)

        click.secho("Preflight checks passed.", fg="green")
    else:
        click.secho("Skipping preflight checks (--skip-preflight).", fg="yellow")

    ubersmith_home_path = Path(installer_state.ubersmith_home)
    virtual_hosts = [
        h.strip() for h in installer_state.virtual_host.split(",") if h.strip()
    ]

    click.echo("Retrying Let's Encrypt certificate request...")
    retry_letsencrypt_module.retry_letsencrypt(
        virtual_hosts,
        ubersmith_home_path,
        installer_state.admin_email,
        DEFAULT_CERTBOT_VERSION,
    )

    click.echo()
    click.secho("Let's Encrypt certificate retry complete.", fg="green", bold=True)
    click.echo(f"  virtual_host(s) = {', '.join(virtual_hosts)}")


@main.command(name="add-brand")
@click.option(
    "--new-virtual-host",
    "new_virtual_host",
    default=None,
    help=(
        "Enter the hostname(s) for the new brand; for multiple brands use a "
        "comma delimited list."
    ),
)
@click.option(
    "--non-interactive",
    is_flag=True,
    default=False,
    help=(
        "Never prompt. --new-virtual-host must be supplied; otherwise the "
        "command aborts with an error instead of prompting."
    ),
)
@click.option(
    "--state-file",
    "state_file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=state.DEFAULT_STATE_PATH,
    show_default=True,
    help="Path to the installer state ini file to read/update.",
)
@click.option(
    "--skip-preflight",
    is_flag=True,
    default=False,
    help="Skip OS/Docker preflight checks (for testing in non-standard environments).",
)
def add_brand(
    new_virtual_host: str | None,
    non_interactive: bool,
    state_file: Path,
    skip_preflight: bool,
) -> None:
    """Add a new brand (additional virtual host(s)) to an existing install.

    Reaches parity with the two-step sequence ``add_new_brand.sh`` runs
    back-to-back (``add_new_brand.yml -t new_brand`` then
    ``retry_letsencrypt.yml``): prompts for (or accepts via flag) the new
    hostname(s), reads the existing installer state, computes the combined
    ``virtual_host`` list (existing + new), generates a self-signed cert and
    apache vhost config for the NEW host(s) only (existing hosts already
    have theirs from a prior install/add-brand run -- an intentional
    simplification vs. faithfully replaying the tagged Ansible tasks, which
    would harmlessly but pointlessly re-generate them for every host on
    every run), updates the installer state's ``virtual_host`` to the
    combined list, then retries the Let's Encrypt certificate request for
    the FULL combined host list.
    """
    if new_virtual_host is None:
        if non_interactive:
            click.secho(
                "--non-interactive was given but the following value(s) are "
                "missing: --new-virtual-host",
                fg="red",
                bold=True,
            )
            sys.exit(2)
        answers = prompts.prompt_for_add_brand_values()
        new_virtual_host = answers["new_virtual_host"]

    installer_state = state.read_state(path=state_file)
    required_fields = ("ubersmith_home", "virtual_host", "admin_email")
    missing_fields = [
        name for name in required_fields if getattr(installer_state, name) is None
    ]
    if missing_fields:
        click.secho(
            "Installer configuration is not present (missing: "
            f"{', '.join(missing_fields)} in {state_file}). Run `install` first.",
            fg="red",
            bold=True,
        )
        sys.exit(1)

    if not skip_preflight:
        click.echo("Running preflight checks...")
        result = preflight.run_preflight_checks(check_service_enabled=False)

        for warning in result.warnings:
            click.secho(f"  [WARN] {warning}", fg="yellow")

        if not result.ok:
            for error in result.errors:
                click.secho(f"  [FAIL] {error}", fg="red")
            click.secho("Preflight checks failed.", fg="red", bold=True)
            sys.exit(1)

        click.secho("Preflight checks passed.", fg="green")
    else:
        click.secho("Skipping preflight checks (--skip-preflight).", fg="yellow")

    ubersmith_home_path = Path(installer_state.ubersmith_home)
    admin_email = installer_state.admin_email
    existing_hosts = [
        h.strip() for h in installer_state.virtual_host.split(",") if h.strip()
    ]
    new_hosts = [h.strip() for h in new_virtual_host.split(",") if h.strip()]
    combined_hosts = existing_hosts + new_hosts

    owner_uid = os.getuid() if hasattr(os, "getuid") else 0
    owner_gid = os.getgid() if hasattr(os, "getgid") else 0

    ssl_dir = ubersmith_home_path / "conf" / "ssl"
    for host in new_hosts:
        certs.generate_selfsigned_cert(host, ssl_dir)

    for host in new_hosts:
        _write_rendered(
            ubersmith_home_path / "conf" / "httpd" / "sites-enabled" / f"{host}.conf",
            templates.render_instance_vhost(
                {
                    "admin_email": admin_email,
                    "ubersmith_root": UBERSMITH_ROOT,
                    "item": host,
                    "fcgi_host": FCGI_HOST,
                    "mozilla_ciphers": DEFAULT_MOZILLA_CIPHERS,
                }
            ),
            0o640,
            owner_uid,
            owner_gid,
        )

    combined_virtual_host = ",".join(combined_hosts)
    state.write_state(
        {
            "virtual_host": combined_virtual_host,
            "app_virtual_host": combined_virtual_host,
        },
        path=state_file,
    )

    click.echo("Retrying Let's Encrypt certificate request...")
    retry_letsencrypt_module.retry_letsencrypt(
        combined_hosts,
        ubersmith_home_path,
        admin_email,
        DEFAULT_CERTBOT_VERSION,
    )

    click.echo()
    click.secho("Brand added.", fg="green", bold=True)
    click.echo(f"  new virtual_host(s)         = {', '.join(new_hosts)}")
    click.echo(f"  virtual_host(s)             = {combined_virtual_host}")
    click.echo(f"  installer state written to  = {state_file}")


@main.command()
@click.option(
    "--patch-id",
    "patch_id",
    default=None,
    help=(
        "Apply this patch ID directly instead of prompting from the "
        "available-patches list (required with --non-interactive, since a "
        "patch ID cannot be chosen interactively without a human present)."
    ),
)
@click.option(
    "--non-interactive",
    is_flag=True,
    default=False,
    help=(
        "Never prompt for a patch ID -- --patch-id must be supplied; "
        "otherwise the command aborts with an error instead of prompting."
    ),
)
@click.option(
    "--state-file",
    "state_file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=state.DEFAULT_STATE_PATH,
    show_default=True,
    help="Path to the installer state ini file to read/update.",
)
def patch(
    patch_id: str | None,
    non_interactive: bool,
    state_file: Path,
) -> None:
    """Fetch and apply an official Ubersmith patch release.

    Reaches parity with ``patch_ubersmith.yml`` as actually invoked by
    ``patch_ubersmith.sh`` (with ``--skip-tags remove_patches``): reads the
    existing installer state, confirms patches are supported for this
    install (the ``docker-compose.override.yml`` patches volume mount must
    be present), warns (without deleting anything) if a prior patch marker
    is present, lists the patch releases available for the currently
    installed Ubersmith version, prompts the admin to choose one (or uses
    ``--patch-id`` for non-interactive use), downloads and unpacks the
    chosen release asset, applies it (restarting the web container, fixing
    ownership, and copying the patch files into place), and records the
    applied patch's metadata in ``.patched``.
    """
    installer_state = state.read_state(path=state_file)
    required_fields = ("ubersmith_home", "ubersmith_installed_version")
    missing_fields = [
        name for name in required_fields if getattr(installer_state, name) is None
    ]
    if missing_fields:
        click.secho(
            "Installer configuration is not present (missing: "
            f"{', '.join(missing_fields)} in {state_file}). Run `install` first.",
            fg="red",
            bold=True,
        )
        sys.exit(1)

    ubersmith_home_path = Path(installer_state.ubersmith_home)
    ubersmith_version = installer_state.ubersmith_installed_version

    if not patch_apply.check_patches_supported(ubersmith_home_path):
        click.secho(
            "Ubersmith is not currently configured to accept patches. "
            "Please contact support@ubersmith.com.",
            fg="red",
            bold=True,
        )
        sys.exit(1)

    patch_apply.warn_if_already_patched(ubersmith_home_path, not non_interactive)

    click.echo("Determining available patches...")
    patches = patch_apply.list_available_patches(ubersmith_version)

    if patch_id is None:
        if non_interactive:
            click.secho(
                "--non-interactive was given but --patch-id is missing (a "
                "patch ID cannot be chosen non-interactively).",
                fg="red",
                bold=True,
            )
            sys.exit(2)
        patch_id = patch_apply.prompt_for_patch_id(patches, ubersmith_version)

    selected = next(
        (p for p in patches if str(p["id"]) == str(patch_id)), None
    )
    if selected is None:
        click.secho(
            f"Patch ID {patch_id!r} is not among the available patches for "
            f"Ubersmith {ubersmith_version}.",
            fg="red",
            bold=True,
        )
        sys.exit(1)

    click.echo(f"Downloading and unpacking patch {patch_id}...")
    patch_apply.download_and_unpack_patch(
        patch_id, selected["asset_url"], ubersmith_home_path
    )

    click.echo("Applying patch...")
    patch_apply.apply_patch(ubersmith_home_path, patch_id)

    patch_apply.record_patch_metadata(
        ubersmith_home_path,
        patch_id,
        installer=getpass.getuser(),
        github_page=selected["html_url"],
    )

    click.echo()
    click.secho("Ubersmith patch applied.", fg="green", bold=True)
    click.echo(f"  patch_id = {patch_id}")
    click.echo(f"  name     = {selected['name']}")


if __name__ == "__main__":
    main()
