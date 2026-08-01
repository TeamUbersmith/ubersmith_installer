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
    docker_ops,
    mta,
    preflight,
    prompts,
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


if __name__ == "__main__":
    main()
