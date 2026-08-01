"""Docker and Docker Compose operations for the Ubersmith installer.

This module is the container/filesystem lifecycle layer: pulling images,
creating the on-disk directory structure Ubersmith expects, copying static
helper files/rules into place, and driving ``docker compose`` to bring
containers up. It is a faithful port of the equivalent tasks in
``roles/ubersmith/tasks/main.yml`` -- specifically:

    * "Pull required images"
    * "Create ubersmith configuration directories"
    * "Copy ubersmith_restart" / "Copy ubersmith_start" / "Copy falco rules"
    * "Update and start ubersmith containers"
    * "Scale redis containers"
    * "Check for remote database" / local-database gating on
      ``DATABASE_HOST=db`` in the web container's env
    * "Make sure UID/GID 1001 owns the database files"
    * "Wait for containers to come online" / "Check database container status"
    * "Run updatedb.php" / "Display updatedb.php debug output"
    * "Remove setup"
    * "Docker image cleanup"

Image pulls/introspection go through the ``docker`` Python SDK (mirroring
``community.docker.docker_image``), while compose-level lifecycle commands
shell out to the ``docker compose`` CLI via ``subprocess`` -- exactly like
the Ansible role itself does (``ansible.builtin.command: docker compose ...``).

All functions accept an injectable client/runner so tests can exercise this
module without a real Docker daemon.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

import docker

#: Directory containing the static files shipped with this package, copied
#: byte-for-byte from roles/ubersmith/files/.
FILES_DIR = Path(__file__).parent / "files"

#: Mirrors the exact `with_items` list from the "Create ubersmith
#: configuration directories" task in roles/ubersmith/tasks/main.yml.
#: Paths are relative to ubersmith_home.
CONFIG_DIRECTORIES = [
    "logs/ubersmith",
    "conf/mail",
    "conf/mysql",
    "conf/mysql-components",
    "conf/ssl",
    "conf/httpd",
    "conf/httpd/sites-enabled",
    "conf/php",
    "conf/cron",
    "conf/rwhois",
    "conf/certbot",
    "conf/certbot/lib",
    "conf/certbot/etc",
    "conf/certbot/etc/renewal-hooks",
    "conf/certbot/etc/renewal-hooks/deploy",
    "conf/certbot/log",
    "conf/sso",
    "conf/falco",
    "app/custom",
    "app/custom/locale",
    "app/custom/plugins",
    "app/custom/include",
    "app/custom/include/service_modules",
    "app/custom/include/device_modules",
    "app/custom/include/order_modules",
    "app/custom/.well-known",
    "app/custom/.well-known/acme-challenge",
    "app/patches",
]

#: Mode used by the "Create ubersmith configuration directories" task.
CONFIG_DIRECTORY_MODE = 0o775

#: Mode used by the "Copy ubersmith_restart" / "Copy ubersmith_start" tasks.
HELPER_SCRIPT_MODE = 0o700

#: Mode used by the "Copy falco rules" task.
FALCO_RULES_MODE = 0o644

#: Mode used by the "Create mysql component configuration" task.
MYSQL_COMPONENT_FILES_MODE = 0o644

#: Filenames copied by the "Create mysql component configuration" task,
#: bind-mounted as individual files into the db container by
#: docker-compose.yml.j2 -- if these are missing, Docker silently creates an
#: empty directory at the mount path instead of a file, breaking MySQL
#: at-rest encryption / preventing the db container from starting.
MYSQL_COMPONENT_FILES = ["component_keyring_file.cnf", "mysqld.my"]

#: Exact service list from the "Update and start ubersmith containers" task:
#: docker compose up -d --quiet-pull --no-color web cron db php solr mail
#: rsyslog rwhois redis-data
COMPOSE_UP_SERVICES = [
    "web",
    "cron",
    "db",
    "php",
    "solr",
    "mail",
    "rsyslog",
    "rwhois",
    "redis-data",
]

#: Default number of redis replicas ("Scale redis containers" task).
DEFAULT_REDIS_SCALE = 3

#: Exact service list from the "Stop existing containers" task (upgrade_only,
#: ~line 666): docker compose rm -s -f web cron db php solr mail rsyslog
#: rwhois redis redis-data certbot
STOP_CONTAINERS_SERVICES = [
    "web",
    "cron",
    "db",
    "php",
    "solr",
    "mail",
    "rsyslog",
    "rwhois",
    "redis",
    "redis-data",
    "certbot",
]

#: Type of the subprocess runner callable injectable into compose_up/
#: scale_redis for testing. Matches the subset of subprocess.run's
#: signature that this module relies on.
SubprocessRunner = Callable[..., subprocess.CompletedProcess]


def _default_runner(
    cmd: Sequence[str], *, cwd: Path, env: Mapping[str, str]
) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), cwd=str(cwd), env=dict(env), check=True)


def pull_images(
    image_refs: Sequence[str], *, client: Optional["docker.DockerClient"] = None
) -> None:
    """Pull each of `image_refs` via the Docker SDK.

    Mirrors the "Pull required images" task, which loops over a list of
    fully-qualified image references and pulls each with
    ``community.docker.docker_image`` (``source: pull``).
    """
    if client is None:
        client = docker.from_env()

    for ref in image_refs:
        client.images.pull(ref)


def _chown(path: Path, uid: int, gid: int) -> None:
    """Thin wrapper around os.chown so ownership changes can be
    monkeypatched/skipped in tests without needing real privileges."""
    os.chown(path, uid, gid)


def create_config_directories(
    ubersmith_home: Path,
    owner_uid: int,
    owner_gid: int,
    *,
    chown: Optional[Callable[[Path, int, int], None]] = None,
) -> None:
    """Create the Ubersmith configuration directory tree.

    Mirrors the "Create ubersmith configuration directories" task: creates
    every directory in `CONFIG_DIRECTORIES` (relative to `ubersmith_home`)
    with mode 0775 and the given owner uid/gid.
    """
    ubersmith_home = Path(ubersmith_home)
    chown_fn = chown if chown is not None else _chown

    for relative_path in CONFIG_DIRECTORIES:
        directory = ubersmith_home / relative_path
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(CONFIG_DIRECTORY_MODE)
        try:
            chown_fn(directory, owner_uid, owner_gid)
        except (PermissionError, LookupError, OSError):
            # Best-effort: changing ownership requires privileges this
            # process may not have (e.g. non-root dev/test runs). The
            # directory is still created with the correct mode.
            pass


def copy_static_files(ubersmith_home: Path) -> None:
    """Copy the static helper scripts and falco rules into place.

    Mirrors the "Copy ubersmith_restart", "Copy ubersmith_start", and
    "Copy falco rules" tasks:

        * ubersmith_restart.sh -> <ubersmith_home>/ubersmith_restart.sh (0700)
        * ubersmith_start.sh   -> <ubersmith_home>/ubersmith_start.sh   (0700)
        * falco_rules.local.yaml -> <ubersmith_home>/conf/falco/falco_rules.local.yaml (0644)
    """
    ubersmith_home = Path(ubersmith_home)

    helper_scripts = ["ubersmith_restart.sh", "ubersmith_start.sh"]
    for filename in helper_scripts:
        src = FILES_DIR / filename
        dest = ubersmith_home / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        dest.chmod(HELPER_SCRIPT_MODE)

    falco_src = FILES_DIR / "falco_rules.local.yaml"
    falco_dest = ubersmith_home / "conf" / "falco" / "falco_rules.local.yaml"
    falco_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(falco_src, falco_dest)
    falco_dest.chmod(FALCO_RULES_MODE)


def copy_mysql_component_files(ubersmith_home: Path) -> None:
    """Copy the MySQL at-rest-encryption component config files into place.

    Mirrors the "Create mysql component configuration" task:
    component_keyring_file.cnf and mysqld.my -> <ubersmith_home>/conf/mysql-components/ (0644).

    docker-compose.yml.j2 bind-mounts these as individual files into the db
    container; if they're missing, Docker creates an empty directory at the
    mount path instead, which breaks MySQL at-rest encryption.
    """
    ubersmith_home = Path(ubersmith_home)
    dest_dir = ubersmith_home / "conf" / "mysql-components"
    dest_dir.mkdir(parents=True, exist_ok=True)

    for filename in MYSQL_COMPONENT_FILES:
        src = FILES_DIR / filename
        dest = dest_dir / filename
        shutil.copyfile(src, dest)
        dest.chmod(MYSQL_COMPONENT_FILES_MODE)


def compose_up(
    ubersmith_home: Path,
    extra_env: Optional[Mapping[str, str]] = None,
    *,
    runner: Optional[SubprocessRunner] = None,
    env: Optional[Mapping[str, str]] = None,
    services: Optional[Sequence[str]] = None,
    quiet_pull: bool = True,
) -> None:
    """Bring up Ubersmith containers via ``docker compose``.

    Mirrors the "Update and start ubersmith containers" task:
    ``docker compose up -d --quiet-pull --no-color web cron db php solr mail
    rsyslog rwhois redis-data``, run with ``chdir: ubersmith_home`` and (in
    that task) ``MAINTENANCE: "1"`` merged into the environment.

    ``services`` and ``quiet_pull`` let callers target a different subset of
    services / drop the ``--quiet-pull`` flag, matching the "Start web
    container with maintenance mode disabled" task later in the same file
    (``docker compose up -d --no-color web``, with ``MAINTENANCE: "0"``) --
    defaults reproduce the original full-service, quiet-pull invocation
    exactly.
    """
    if runner is None:
        runner = _default_runner

    base_env = dict(env) if env is not None else dict(os.environ)
    if extra_env:
        base_env.update(extra_env)

    service_list = list(services) if services is not None else list(COMPOSE_UP_SERVICES)

    cmd = ["docker", "compose", "up", "-d"]
    if quiet_pull:
        cmd.append("--quiet-pull")
    cmd.append("--no-color")
    cmd.extend(service_list)
    runner(cmd, cwd=Path(ubersmith_home), env=base_env)


def stop_containers(
    ubersmith_home: Path,
    *,
    runner: Optional[SubprocessRunner] = None,
    env: Optional[Mapping[str, str]] = None,
) -> None:
    """Stop and remove the existing Ubersmith containers via ``docker compose``.

    Mirrors the "Stop existing containers" (upgrade_only) task: ``docker
    compose rm -s -f web cron db php solr mail rsyslog rwhois redis
    redis-data certbot``, run with ``chdir: ubersmith_home``.
    """
    if runner is None:
        runner = _default_runner

    base_env = dict(env) if env is not None else dict(os.environ)

    cmd = ["docker", "compose", "rm", "-s", "-f", *STOP_CONTAINERS_SERVICES]
    runner(cmd, cwd=Path(ubersmith_home), env=base_env)


def scale_redis(
    ubersmith_home: Path,
    count: int = DEFAULT_REDIS_SCALE,
    *,
    runner: Optional[SubprocessRunner] = None,
    env: Optional[Mapping[str, str]] = None,
) -> None:
    """Scale the redis service via ``docker compose``.

    Mirrors the "Scale redis containers" task:
    ``docker compose up -d --scale redis=3 redis``, run with
    ``chdir: ubersmith_home``.
    """
    if runner is None:
        runner = _default_runner

    base_env = dict(env) if env is not None else dict(os.environ)

    cmd = ["docker", "compose", "up", "-d", "--scale", f"redis={count}", "redis"]
    runner(cmd, cwd=Path(ubersmith_home), env=base_env)


def backup_mysql_keyring(
    ubersmith_home: Path, *, client: Optional["docker.DockerClient"] = None
) -> None:
    """Back up the MySQL at-rest-encryption keyring volume.

    Mirrors the "Make a backup of the mysql keyring" task: runs a
    short-lived busybox container that tars the `ubersmith_database_keyring`
    volume into `<ubersmith_home>/backup/component_keyring_file.<epoch>.tar`
    (mode 0600). Must run after the containers (and thus the
    `ubersmith_database_keyring` volume) exist -- i.e. after `compose_up`.
    """
    if client is None:
        client = docker.from_env()

    backup_dir = Path(ubersmith_home) / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    epoch = int(time.time())
    command = (
        f"tar cvf /backup/component_keyring_file.{epoch}.tar /keyring; "
        "chmod 0600 /backup/*.tar"
    )

    client.containers.run(
        image="busybox",
        command=["/bin/sh", "-c", command],
        user="root",
        remove=True,
        volumes={
            "ubersmith_database_keyring": {"bind": "/keyring", "mode": "rw"},
            str(backup_dir): {"bind": "/backup", "mode": "rw"},
        },
    )


#: Default sleep callable injected into wait_for_containers_healthy so tests
#: can run instantly. Matches time.sleep's signature.
SleepFn = Callable[[float], None]


def get_web_container_env(client: Optional["docker.DockerClient"] = None) -> list:
    """Return the raw ``Config.Env`` list of the ubersmith-web-1 container.

    Mirrors the "Check for remote database" task:
    ``community.docker.docker_container_info`` on ``ubersmith-web-1``,
    registered as ``web_container_info`` -- later tasks key off
    ``web_container_info.container.Config.Env`` to decide whether the
    upgrade is dealing with a local (in-stack) or remote database.
    """
    if client is None:
        client = docker.from_env()

    container = client.containers.get("ubersmith-web-1")
    return list(container.attrs["Config"]["Env"])


def is_local_database(env: Sequence[str]) -> bool:
    """Return whether the web container's env indicates a local database.

    Mirrors the exact Ansible expression used throughout main.yml to gate
    local-database-only upgrade steps:
    ``"'DATABASE_HOST=db' in web_container_info.container.Config.Env"`` --
    a literal substring-in-list membership check, not a dict lookup (the
    variable is only ever set to the hostname "db" for the bundled/local
    database container; anything else, including a remote host, will not
    match this exact string).
    """
    return "DATABASE_HOST=db" in env


def chown_database_files(client: Optional["docker.DockerClient"] = None) -> None:
    """Chown the ubersmith_database volume to uid/gid 1001.

    Mirrors the "Make sure UID/GID 1001 owns the database files" task: runs
    a short-lived busybox container (root user, auto-remove) that runs
    ``chown -R 1001:1001 /mysql`` with the ``ubersmith_database`` volume
    bind-mounted at ``/mysql``. Only relevant when `is_local_database` is
    True -- the caller is responsible for that check, mirroring the task's
    ``when: "'DATABASE_HOST=db' in web_container_info.container.Config.Env"``.
    """
    if client is None:
        client = docker.from_env()

    client.containers.run(
        image="busybox",
        command="chown -R 1001:1001 /mysql",
        user="root",
        remove=True,
        volumes={"ubersmith_database": {"bind": "/mysql", "mode": "rw"}},
    )


def wait_for_containers_healthy(
    names: Sequence[str],
    *,
    client: Optional["docker.DockerClient"] = None,
    retries: int = 10,
    delay: int = 30,
    sleep: Optional[SleepFn] = None,
) -> None:
    """Poll each named container until Docker reports it healthy.

    Mirrors the "Wait for containers to come online" task:
    ``community.docker.docker_container_info`` on each of
    ``ubersmith-web-1``, ``ubersmith-php-1``, ``ubersmith-solr-1``, polled
    ``until: ... State.Health.Status == "healthy"`` with ``retries: 10`` /
    ``delay: 30``. Only relevant when the database is local -- the caller
    is responsible for gating on `is_local_database`.

    Raises TimeoutError if any container never becomes healthy within
    `retries` attempts.
    """
    if client is None:
        client = docker.from_env()
    if sleep is None:
        sleep = time.sleep

    for name in names:
        healthy = False
        for attempt in range(retries):
            container = client.containers.get(name)
            status = container.attrs.get("State", {}).get("Health", {}).get("Status")
            if status == "healthy":
                healthy = True
                break
            if attempt < retries - 1:
                sleep(delay)
        if not healthy:
            raise TimeoutError(
                f"Container '{name}' did not become healthy after "
                f"{retries} retries ({delay}s delay)"
            )


def check_database_container_healthy(
    *,
    client: Optional["docker.DockerClient"] = None,
    retries: int = 6,
    delay: int = 10,
    sleep: Optional[SleepFn] = None,
) -> None:
    """Poll ubersmith-db-1 until Docker reports it healthy.

    Mirrors the "Check database container status" task:
    ``community.docker.docker_container_info`` on ``ubersmith-db-1``,
    polled ``until: ... State.Health.Status == "healthy"`` with
    ``retries: 6`` / ``delay: 10``. Only relevant when the database is
    local -- the caller is responsible for gating on `is_local_database`.
    """
    wait_for_containers_healthy(
        ["ubersmith-db-1"],
        client=client,
        retries=retries,
        delay=delay,
        sleep=sleep,
    )


def run_updatedb(
    ubersmith_root: str, *, client: Optional["docker.DockerClient"] = None
) -> tuple:
    """Run updatedb.php inside the php container.

    Mirrors the "Run updatedb.php" task: ``community.docker.docker_container_exec``
    on ``ubersmith-php-1``, running
    ``/bin/bash -c 'php {{ ubersmith_root }}/app/www/setup/updatedb.php ubersmith --debug'``.
    Returns (stdout, stderr) so the caller can display them, mirroring the
    "Display updatedb.php debug output" task.
    """
    if client is None:
        client = docker.from_env()

    command = (
        f"/bin/bash -c 'php {ubersmith_root}/app/www/setup/updatedb.php "
        "ubersmith --debug'"
    )
    container = client.containers.get("ubersmith-php-1")
    exit_code, output = container.exec_run(command, demux=True)
    stdout, stderr = output if isinstance(output, tuple) else (output, None)
    stdout = stdout.decode() if isinstance(stdout, (bytes, bytearray)) else (stdout or "")
    stderr = stderr.decode() if isinstance(stderr, (bytes, bytearray)) else (stderr or "")
    return stdout, stderr


def remove_setup_dir(
    ubersmith_root: str, *, client: Optional["docker.DockerClient"] = None
) -> None:
    """Remove the setup/ directory from the web container.

    Mirrors the "Remove setup" task: ``community.docker.docker_container_exec``
    on ``ubersmith-web-1``, running
    ``/bin/bash -c 'rm -rf {{ ubersmith_root }}/app/www/setup'``. Allows
    Ubersmith to start (the app refuses to run while setup/ is present).
    """
    if client is None:
        client = docker.from_env()

    command = f"/bin/bash -c 'rm -rf {ubersmith_root}/app/www/setup'"
    container = client.containers.get("ubersmith-web-1")
    container.exec_run(command)


def prune_old_images(
    client: Optional["docker.DockerClient"] = None, until: str = "2160h"
) -> None:
    """Prune dangling/unused Docker images older than `until`.

    Mirrors the "Docker image cleanup" task: ``community.docker.docker_prune``
    with ``images: true`` and ``images_filters: {until: 2160h}``.
    """
    if client is None:
        client = docker.from_env()

    client.images.prune(filters={"until": until})
