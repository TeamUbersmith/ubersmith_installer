"""Let's Encrypt certificate request flow for the Ubersmith installer.

A faithful port of the certbot-tagged tasks in
``roles/ubersmith/tasks/main.yml``:

    * "Wait for port 80 to become available" (three occurrences: before the
      cert request, between the cert request and the deploy hooks, and after
      the deploy hooks)
    * "Run certbot via a container" (loops over ``virtual_hosts``, runs
      ``certonly -n -d <host> --standalone --agree-tos -m <notify_email>``
      in the ``ghcr.io/teamubersmith/certbot:<certbot_version>`` image)
    * "Manually run deploy hooks" (loops over ``virtual_hosts`` again, runs
      the same image with a different entrypoint --
      ``/etc/letsencrypt/renewal-hooks/deploy/ubersmith-deploy.sh`` -- and
      ``RENEWED_DOMAINS=<host>`` in the environment)

All three of the above are gated in the Ansible role on::

    (lets_encrypt_certificate | default('') | trim | lower) in ['y', 'yes']

which is checked here via ``prompts.is_lets_encrypt_requested`` rather than
being reimplemented.

The "Create certbot renewal shell script" task (a template) is out of scope
for this module -- see ``templates.render_certbot_renew_script()``. The
"Create certbot container cron task" IS implemented here
(``install_renewal_cron_task``), since it's gated by the same
``lets_encrypt_certificate`` check as everything else in this module.

Both the port-wait and the Docker client are injectable so this module can
be unit tested without a real port 80 or a real Docker daemon.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Callable, List, Mapping, Optional

import docker

from .prompts import is_lets_encrypt_requested

#: Marker comment used to identify the managed crontab entry, mirroring how
#: Ansible's `cron` module tags entries it manages with `name:`.
RENEWAL_CRON_MARKER = "#Ansible: check for le renewal"

#: Type of the crontab-reader/writer callables injectable into
#: install_renewal_cron_task for testing.
CrontabReader = Callable[[], str]
CrontabWriter = Callable[[str], None]

#: Mirrors "Run certbot via a container" / "Manually run deploy hooks":
#: image: ghcr.io/teamubersmith/certbot:{{ certbot_version }}
CERTBOT_IMAGE_TEMPLATE = "ghcr.io/teamubersmith/certbot:{version}"

#: Entrypoint used by the "Manually run deploy hooks" task.
DEPLOY_HOOK_ENTRYPOINT = "/etc/letsencrypt/renewal-hooks/deploy/ubersmith-deploy.sh"

#: Default timeout (seconds) for the port-80-availability wait, matching the
#: intent of Ansible's wait_for module (which defaults to a 300s timeout,
#: but a much shorter window is plenty for a "drained" check in practice).
DEFAULT_WAIT_TIMEOUT = 60.0

#: Default poll interval (seconds) between port-availability checks.
DEFAULT_POLL_INTERVAL = 1.0

#: Type of the connect callable injectable into wait_for_port_available for
#: testing. Should raise OSError (or a subclass) if the port is free, and
#: return normally if something is listening/accepting on it.
PortConnector = Callable[[str, int, float], None]


def _default_connect(host: str, port: int, timeout: float) -> None:
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.close()


def wait_for_port_available(
    host: str = "0.0.0.0",
    port: int = 80,
    *,
    timeout: float = DEFAULT_WAIT_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    connect: Optional[PortConnector] = None,
) -> None:
    """Wait for `port` on `host` to become available (i.e. free).

    Mirrors the three "Wait for port 80 to become available" tasks, which
    use Ansible's ``wait_for`` module with ``state: drained`` -- waiting
    until nothing is listening/accepting connections on the port so
    certbot's standalone plugin can bind it exclusively. This is not a
    byte-for-byte port of wait_for's drain semantics (which also waits for
    already-established connections to close), just a functionally
    equivalent TCP connect retry loop: poll until a connection attempt is
    refused (port free), or raise TimeoutError once `timeout` elapses.
    """
    connect_fn = connect if connect is not None else _default_connect

    deadline = time.monotonic() + timeout
    while True:
        try:
            connect_fn(host, port, min(poll_interval, timeout))
        except OSError:
            return

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"port {port} on {host} did not become available within {timeout}s"
            )
        time.sleep(poll_interval)


def _certbot_image(certbot_version: str) -> str:
    return CERTBOT_IMAGE_TEMPLATE.format(version=certbot_version)


def _letsencrypt_volumes(ubersmith_home: Path) -> dict:
    """Volume mounts shared by both the cert-request and deploy-hook runs."""
    ubersmith_home = Path(ubersmith_home)
    return {
        str(ubersmith_home / "conf/certbot/etc"): {"bind": "/etc/letsencrypt", "mode": "rw"},
        str(ubersmith_home / "conf/certbot/lib"): {"bind": "/var/lib/letsencrypt", "mode": "rw"},
        str(ubersmith_home / "conf/certbot/log"): {"bind": "/var/log/letsencrypt", "mode": "rw"},
    }


def _deploy_hook_volumes(ubersmith_home: Path) -> dict:
    """Volume mounts for the "Manually run deploy hooks" task: the same
    three as `_letsencrypt_volumes` plus conf/ssl -> /opt/certbot/deploy."""
    volumes = _letsencrypt_volumes(ubersmith_home)
    volumes[str(Path(ubersmith_home) / "conf/ssl")] = {
        "bind": "/opt/certbot/deploy",
        "mode": "rw",
    }
    return volumes


def run_certbot_request(
    virtual_host: str,
    ubersmith_home: Path,
    notify_email: str,
    certbot_version: str,
    *,
    client: "docker.DockerClient",
    uid: int,
    gid: int,
) -> None:
    """Run the certbot container once for a single virtual host.

    Mirrors one iteration of the "Run certbot via a container" task:
    ``certonly -n -d <virtual_host> --standalone --agree-tos -m
    <notify_email>``, publishing port 80, auto-removing the container on
    exit.
    """
    client.containers.run(
        image=_certbot_image(certbot_version),
        command=(
            f"certonly -n -d {virtual_host} --standalone --agree-tos "
            f"-m {notify_email}"
        ),
        name="certbot",
        user=f"{uid}:{gid}",
        ports={"80/tcp": 80},
        remove=True,
        volumes=_letsencrypt_volumes(ubersmith_home),
    )


def run_certbot_deploy_hook(
    virtual_host: str,
    ubersmith_home: Path,
    certbot_version: str,
    *,
    client: "docker.DockerClient",
    uid: int,
    gid: int,
) -> None:
    """Run the certbot container's deploy hook once for a single virtual host.

    Mirrors one iteration of the "Manually run deploy hooks" task: same
    image, entrypoint overridden to
    ``/etc/letsencrypt/renewal-hooks/deploy/ubersmith-deploy.sh``, with
    ``RENEWED_DOMAINS=<virtual_host>`` in the environment.
    """
    client.containers.run(
        image=_certbot_image(certbot_version),
        entrypoint=DEPLOY_HOOK_ENTRYPOINT,
        name="certbot",
        user=f"{uid}:{gid}",
        remove=True,
        environment={"RENEWED_DOMAINS": virtual_host},
        volumes=_deploy_hook_volumes(ubersmith_home),
    )


def _default_crontab_read() -> str:
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    # A non-existent crontab exits non-zero (typically with "no crontab for
    # <user>" on stderr) -- that's not an error, just an empty crontab.
    return proc.stdout if proc.returncode == 0 else ""


def _default_crontab_write(content: str) -> None:
    subprocess.run(["crontab", "-"], input=content, text=True, check=True)


def install_renewal_cron_task(
    ubersmith_home: Path,
    *,
    reader: Optional[CrontabReader] = None,
    writer: Optional[CrontabWriter] = None,
) -> None:
    """Install a daily crontab entry to renew Let's Encrypt certificates.

    Mirrors the "Create certbot container cron task" task: a daily cron job
    running ``<ubersmith_home>/ubersmith_certbot_renew.sh`` for the invoking
    user, named "check for le renewal". Idempotent: replaces any previously
    installed entry (identified by `RENEWAL_CRON_MARKER`) rather than
    duplicating it, matching Ansible's ``cron`` module semantics for a named
    entry.
    """
    read_fn = reader if reader is not None else _default_crontab_read
    write_fn = writer if writer is not None else _default_crontab_write

    job = f"{Path(ubersmith_home) / 'ubersmith_certbot_renew.sh'}"
    entry = f"{RENEWAL_CRON_MARKER}\n@daily {job}\n"

    existing_lines = read_fn().splitlines(keepends=True)
    kept_lines = []
    skip_next = False
    for line in existing_lines:
        if skip_next:
            skip_next = False
            continue
        if line.rstrip("\n") == RENEWAL_CRON_MARKER:
            skip_next = True
            continue
        kept_lines.append(line)

    new_crontab = "".join(kept_lines)
    if new_crontab and not new_crontab.endswith("\n"):
        new_crontab += "\n"
    new_crontab += entry

    write_fn(new_crontab)


def request_letsencrypt_certificates(
    virtual_hosts: List[str],
    ubersmith_home: Path,
    notify_email: str,
    certbot_version: str,
    lets_encrypt_certificate: str,
    *,
    client: Optional["docker.DockerClient"] = None,
    wait_for_port: Optional[Callable[..., None]] = None,
    uid: Optional[int] = None,
    gid: Optional[int] = None,
    wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
    install_cron: bool = True,
    cron_reader: Optional[CrontabReader] = None,
    cron_writer: Optional[CrontabWriter] = None,
) -> None:
    """Request (and deploy) Let's Encrypt certificates for `virtual_hosts`.

    Mirrors the full certbot request flow in
    ``roles/ubersmith/tasks/main.yml``:

        1. Wait for port 80 to become available.
        2. Run certbot's ``certonly`` via a container, once per host in
           `virtual_hosts` ("Run certbot via a container").
        3. Wait for port 80 to become available again.
        4. Run certbot's deploy hooks via a container, once per host in
           `virtual_hosts` ("Manually run deploy hooks").
        5. Wait for port 80 to become available a third time.
        6. Install the daily renewal cron task ("Create certbot container
           cron task"), unless `install_cron` is False.

    All of the above is gated on `lets_encrypt_certificate` the same way
    the Ansible tasks are gated on
    ``(lets_encrypt_certificate | default('') | trim | lower) in ['y',
    'yes']`` -- checked via `prompts.is_lets_encrypt_requested` rather than
    reimplemented here. If Let's Encrypt was not requested, this function
    is a no-op (no port wait, no Docker client used).

    `client` and `wait_for_port` are injectable so this can be tested
    without a real Docker daemon or a real port 80.
    """
    if not is_lets_encrypt_requested(lets_encrypt_certificate):
        return

    docker_client = client if client is not None else docker.from_env()
    wait_fn = wait_for_port if wait_for_port is not None else wait_for_port_available
    request_uid = uid if uid is not None else os.getuid()
    request_gid = gid if gid is not None else os.getgid()

    wait_fn(host="0.0.0.0", port=80, timeout=wait_timeout)

    for virtual_host in virtual_hosts:
        run_certbot_request(
            virtual_host,
            ubersmith_home,
            notify_email,
            certbot_version,
            client=docker_client,
            uid=request_uid,
            gid=request_gid,
        )

    wait_fn(host="0.0.0.0", port=80, timeout=wait_timeout)

    for virtual_host in virtual_hosts:
        run_certbot_deploy_hook(
            virtual_host,
            ubersmith_home,
            certbot_version,
            client=docker_client,
            uid=request_uid,
            gid=request_gid,
        )

    wait_fn(host="0.0.0.0", port=80, timeout=wait_timeout)

    if install_cron:
        install_renewal_cron_task(ubersmith_home, reader=cron_reader, writer=cron_writer)
