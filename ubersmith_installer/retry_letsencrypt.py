"""Let's Encrypt certificate *retry* flow for the Ubersmith installer.

A faithful port of ``retry_letsencrypt.yml`` (repo root), which is invoked
after installation whenever an admin explicitly wants to (re)try obtaining
Let's Encrypt certificates for an already-running site. Unlike the
install-time flow in ``certbot.py``, this:

    * Runs certbot's ``certonly`` with the ``--webroot`` plugin (not
      ``--standalone``) against the already-serving site, using
      ``--webroot-path /var/www/ubersmith_root/app/www``, so there's no
      need to wait for port 80 to drain first.
    * Loops over *all* configured virtual hosts (from installer state),
      not just a newly-added one.
    * After the deploy hooks run, execs ``apachectl graceful`` in the web
      container ("Perform a graceful restart of apache in the web
      container") to pick up the new certificate without downtime.
    * Re-installs the same daily renewal cron task as install
      ("Create certbot container cron task") -- reusing
      ``certbot.install_renewal_cron_task`` rather than duplicating it.

There is no ``lets_encrypt_certificate`` gate here: this flow is only ever
invoked when the admin explicitly asks to retry, unlike the install-time
flow which is conditional on that answer.

The Docker client is injectable so this module can be unit tested without
a real Docker daemon.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import docker

from . import certbot
from .certbot import (
    DEPLOY_HOOK_ENTRYPOINT,
    _certbot_image,
    _letsencrypt_volumes,
    run_certbot_deploy_hook,
)

#: Webroot path inside the certbot container that the running site's
#: docroot is mounted at -- matches "Run certbot via a container".
WEBROOT_PATH = "/var/www/ubersmith_root/app/www"

#: Volume name mounted at WEBROOT_PATH, shared with the web container.
WEBROOT_VOLUME = "ubersmith_webroot"

#: Container the graceful apache reload is exec'd in.
WEB_CONTAINER_NAME = "ubersmith-web-1"

#: Command used for "Perform a graceful restart of apache in the web
#: container".
APACHE_GRACEFUL_COMMAND = ["/bin/bash", "-c", "/usr/local/apache2/bin/apachectl graceful"]


def _webroot_volumes(ubersmith_home: Path) -> dict:
    """Volume mounts for the "Run certbot via a container" (webroot) task:
    the same three as `certbot._letsencrypt_volumes` plus the shared
    webroot volume."""
    volumes = _letsencrypt_volumes(ubersmith_home)
    volumes[WEBROOT_VOLUME] = {"bind": "/var/www/ubersmith_root", "mode": "rw"}
    return volumes


def run_certbot_webroot_request(
    virtual_host: str,
    ubersmith_home: Path,
    notify_email: str,
    certbot_version: str,
    *,
    client: "docker.DockerClient",
    uid: int,
    gid: int,
) -> None:
    """Run the certbot container once for a single virtual host, webroot style.

    Mirrors one iteration of "Run certbot via a container" in
    ``retry_letsencrypt.yml``: ``certonly -vvv -n -d <virtual_host>
    --webroot --webroot-path /var/www/ubersmith_root/app/www --agree-tos
    -m <notify_email>``, auto-removing the container on exit. No port
    publishing is needed since the webroot plugin doesn't bind port 80
    itself -- the already-running site serves the ACME challenge files.
    """
    client.containers.run(
        image=_certbot_image(certbot_version),
        command=(
            f"certonly -vvv -n -d {virtual_host} --webroot "
            f"--webroot-path {WEBROOT_PATH} --agree-tos -m {notify_email}"
        ),
        name="certbot",
        user=f"{uid}:{gid}",
        remove=True,
        volumes=_webroot_volumes(ubersmith_home),
    )


def run_certbot_webroot_deploy_hook(
    virtual_host: str,
    ubersmith_home: Path,
    certbot_version: str,
    *,
    client: "docker.DockerClient",
    uid: int,
    gid: int,
) -> None:
    """Run the certbot container's deploy hook once for a single virtual host.

    Mirrors "Run deploy hooks" in ``retry_letsencrypt.yml``, which is
    identical in shape to certbot.py's "Manually run deploy hooks" task
    (same image, same entrypoint override, same ``RENEWED_DOMAINS`` env,
    same volumes) -- so this simply delegates to
    ``certbot.run_certbot_deploy_hook`` rather than duplicating it.
    """
    run_certbot_deploy_hook(
        virtual_host,
        ubersmith_home,
        certbot_version,
        client=client,
        uid=uid,
        gid=gid,
    )


def graceful_apache_reload(client: Optional["docker.DockerClient"] = None) -> None:
    """Gracefully reload Apache in the web container to pick up new certs.

    Mirrors "Perform a graceful restart of apache in the web container"
    (``community.docker.docker_container_exec``): execs
    ``/bin/bash -c "/usr/local/apache2/bin/apachectl graceful"`` in the
    ``ubersmith-web-1`` container.
    """
    if client is None:
        client = docker.from_env()

    container = client.containers.get(WEB_CONTAINER_NAME)
    container.exec_run(APACHE_GRACEFUL_COMMAND)


def retry_letsencrypt(
    virtual_hosts: List[str],
    ubersmith_home: Path,
    notify_email: str,
    certbot_version: str,
    *,
    client: Optional["docker.DockerClient"] = None,
    uid: Optional[int] = None,
    gid: Optional[int] = None,
) -> None:
    """Retry the Let's Encrypt certificate request for all `virtual_hosts`.

    Mirrors the full task list in ``retry_letsencrypt.yml``:

        1. Run certbot's ``certonly`` via a container (webroot method),
           once per host in `virtual_hosts` ("Run certbot via a
           container").
        2. Run certbot's deploy hooks via a container, once per host
           ("Run deploy hooks").
        3. Gracefully reload Apache in the web container ("Perform a
           graceful restart of apache in the web container").
        4. Re-install the daily renewal cron task ("Create certbot
           container cron task"), via ``certbot.install_renewal_cron_task``.

    Unlike ``certbot.request_letsencrypt_certificates``, there is no
    ``lets_encrypt_certificate`` gate: this is only ever invoked when the
    admin explicitly wants to retry.

    `client` is injectable so this can be tested without a real Docker
    daemon.
    """
    docker_client = client if client is not None else docker.from_env()
    request_uid = uid if uid is not None else os.getuid()
    request_gid = gid if gid is not None else os.getgid()

    for virtual_host in virtual_hosts:
        run_certbot_webroot_request(
            virtual_host,
            ubersmith_home,
            notify_email,
            certbot_version,
            client=docker_client,
            uid=request_uid,
            gid=request_gid,
        )

    for virtual_host in virtual_hosts:
        run_certbot_webroot_deploy_hook(
            virtual_host,
            ubersmith_home,
            certbot_version,
            client=docker_client,
            uid=request_uid,
            gid=request_gid,
        )

    graceful_apache_reload(client=docker_client)

    certbot.install_renewal_cron_task(ubersmith_home)
