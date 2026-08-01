"""Command-line entry point for the Ubersmith installer.

This is a minimal, non-interactive spike wiring together
:mod:`ubersmith_installer.preflight`, :mod:`ubersmith_installer.templates`,
and :mod:`ubersmith_installer.state` into a single ``install`` command.

It deliberately does NOT bring anything up with Docker yet -- it runs the
preflight checks, renders the docker-compose/.env/ubersmith.ini config files
to an output directory, writes the initial installer state file, and prints
a "dry run" summary of what happened. Full parity with
``install_ubersmith.yml`` (actually starting containers, requesting
Let's Encrypt certs, etc.) is left to a later phase.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from . import preflight, state, templates

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


@click.group()
@click.version_option(package_name="ubersmith-installer")
def main() -> None:
    """Ubersmith installer CLI."""


@main.command()
@click.option(
    "--ubersmith-major-version",
    "ubersmith_major_version",
    default="5",
    show_default=True,
    help="Choose which version of Ubersmith to install (4 or 5).",
)
@click.option(
    "--ubersmith-home",
    "ubersmith_home",
    default="/usr/local/ubersmith",
    show_default=True,
    help="Choose an installation directory for Ubersmith.",
)
@click.option(
    "--virtual-host",
    "virtual_host",
    default="ubersmith.example.com",
    show_default=True,
    help=(
        "Enter the hostname(s) where you will be hosting Ubersmith; for "
        "multiple hostnames use a comma delimited list."
    ),
)
@click.option(
    "--admin-email",
    "admin_email",
    default="admin@example.org",
    show_default=True,
    help="Enter the email address of the Ubersmith administrator.",
)
@click.option(
    "--output-dir",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Directory to render docker-compose.yml/.env/ubersmith.ini into. "
        "Defaults to <ubersmith-home>/conf, but can be pointed elsewhere "
        "(e.g. /tmp/...) for testing without touching a real install."
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
    ubersmith_major_version: str,
    ubersmith_home: str,
    virtual_host: str,
    admin_email: str,
    output_dir: Path | None,
    state_file: Path,
    skip_preflight: bool,
) -> None:
    """Run a non-interactive, dry-run style Ubersmith install.

    Runs preflight checks, renders docker-compose.yml/.env/ubersmith.ini,
    and writes the initial installer state file. Does NOT call Docker to
    bring anything up -- that is left to a later phase.
    """
    if output_dir is None:
        output_dir = Path(ubersmith_home) / "conf"

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

    virtual_hosts = [h.strip() for h in virtual_host.split(",") if h.strip()]
    container_domain = virtual_hosts[0] if virtual_hosts else virtual_host

    context = {
        "registry": DEFAULT_REGISTRY,
        "ubersmith_version": release["ubersmith_release_version"],
        "containers_release_version": release["containers"]["release_version"],
        "container_domain": container_domain,
        "ubersmith_home": ubersmith_home,
        "ubersmith_major_version": ubersmith_major_version,
        "ubersmith_release": UBERSMITH_RELEASE,
        "mozilla_ciphers": DEFAULT_MOZILLA_CIPHERS,
        "pmm_version": DEFAULT_PMM_VERSION,
        "certbot_version": DEFAULT_CERTBOT_VERSION,
        "virtual_hosts": virtual_hosts,
        "notify_email": admin_email,
    }

    rendered = {
        "docker-compose.yml": templates.render_docker_compose(context),
        ".env": templates.render_dot_env(context),
        "ubersmith.ini": templates.render_ubersmith_ini(
            {**DEFAULT_INI_CONTEXT, **context}
        ),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    written_paths = []
    for filename, content in rendered.items():
        dest = output_dir / filename
        dest.write_text(content, encoding="utf-8")
        written_paths.append(dest)

    installer_state = state.InstallerState(
        ubersmith_home=ubersmith_home,
        virtual_host=virtual_host,
        admin_email=admin_email,
        ubersmith_installed_version=release["ubersmith_release_version"],
    )
    state.write_installer_state(installer_state, path=state_file)

    click.echo()
    click.secho("Dry run complete -- no Docker containers were started.", bold=True)
    click.echo(f"Rendered {len(written_paths)} config file(s) to {output_dir}:")
    for path in written_paths:
        click.echo(f"  - {path}")
    click.echo(f"Wrote installer state to {state_file}:")
    click.echo(f"  ubersmith_home = {ubersmith_home}")
    click.echo(f"  virtual_host = {virtual_host}")
    click.echo(f"  admin_email = {admin_email}")
    click.echo(f"  ubersmith_installed_version = {release['ubersmith_release_version']}")


if __name__ == "__main__":
    main()
