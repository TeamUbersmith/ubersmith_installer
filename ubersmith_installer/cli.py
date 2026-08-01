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

import os
import subprocess
import sys
from pathlib import Path

import click

from . import (
    certbot,
    certs,
    compose_override,
    docker_ops,
    migrations,
    mta,
    patch_cleanup,
    preflight,
    prompts,
    redis_migration,
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
    # databases predating 5.2.0 -- run_migrations checks both internally).
    ran_migrations = migrations.run_migrations(
        ubersmith_home_path,
        mysql_root_password,
        mysql_ubersmith_password,
        old_installed_version,
        is_local_database,
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


if __name__ == "__main__":
    main()
