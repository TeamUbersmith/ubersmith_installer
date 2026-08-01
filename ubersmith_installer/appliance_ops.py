"""Docker and Docker Compose operations for the Ubersmith *appliance* installer.

This is the appliance-specific counterpart to :mod:`ubersmith_installer.
docker_ops` -- the container/filesystem lifecycle layer for the standalone
appliance product (as opposed to the full Ubersmith stack). It is a faithful
port of the equivalent tasks in ``roles/appliance/tasks/main.yml`` --
specifically:

    * "Create appliance configuration directories"
    * "Copy backup script" / "Copy appliance_restart" / "Copy appliance_start"
      / "Copy appliance_upgrade"
    * "Check for remote database" / local-database gating on
      ``DATABASE_HOST=app_db`` in the ``app_web`` container's env
    * "Run docker compose pull"
    * "Stop existing containers"
    * "Get existing docker volumes" / "Remove ubersmith appliance webroot
      volume if present"
    * "Make sure UID/GID 1001 owns the database files"
    * "Step the database up to mysql 5.7, if necessary" / "Wait for mysql 5.7
      container to come online" / "Run mysql_upgrade for mysql 5.7" /
      "Remove mysql 5.7 container"
    * "Run docker compose up -d"
    * "Wait for containers to come online"
    * "Docker image cleanup"

Where an appliance task is functionally identical to its ubersmith
counterpart (same docker SDK call, same shape, just different container/
volume names -- e.g. image pulls, wait-for-healthy polling, image pruning),
this module reuses the corresponding function from ``docker_ops`` directly
rather than reimplementing it. Only genuinely appliance-specific behavior
(different directory list, different static files, the ``-p ubersmith``
project-scoped compose invocations with no explicit service list, and the
mysql 5.6->5.7 step-up dance) gets a new function here.

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

from ubersmith_installer import docker_ops

#: Directory containing the static files shipped with this package. Shared
#: with docker_ops -- both the ubersmith and appliance static files live
#: side by side under ubersmith_installer/files/.
FILES_DIR = docker_ops.FILES_DIR

#: Mirrors the exact `with_items` list from the "Create appliance
#: configuration directories" task in roles/appliance/tasks/main.yml.
#: Paths are relative to appliance_home.
CONFIG_DIRECTORIES = [
    "conf/cron",
    "conf/httpd",
    "conf/httpd/sites-enabled",
    "conf/mysql",
    "conf/php",
    "conf/ssl",
    "logs",
    "logs/appliance",
]

#: Mode used by the "Create appliance configuration directories" task.
CONFIG_DIRECTORY_MODE = 0o775

#: Mode used by the "Copy backup script" / "Copy appliance_restart" /
#: "Copy appliance_start" / "Copy appliance_upgrade" tasks.
HELPER_SCRIPT_MODE = 0o700

#: "Copy backup script" has no `force: false`, so it is overwritten on every
#: run (unlike the other three helper scripts below).
ALWAYS_OVERWRITE_HELPER_SCRIPTS = ["backup_rrds.sh"]

#: "Copy appliance_restart" / "Copy appliance_start" / "Copy
#: appliance_upgrade" all set `force: false`, meaning Ansible leaves the
#: destination alone if it already exists.
IF_ABSENT_HELPER_SCRIPTS = [
    "appliance_restart.sh",
    "appliance_start.sh",
    "appliance_upgrade.sh",
]

#: Container name checked by the "Check for remote database" task
#: (appliance-specific -- ubersmith's equivalent checks "ubersmith-web-1").
APP_WEB_CONTAINER_NAME = "ubersmith-app_web-1"

#: Volume containing the appliance database, chowned by "Make sure UID/GID
#: 1001 owns the database files" and mounted into the mysql 5.7 step-up
#: container.
APP_DATABASE_VOLUME = "ubersmith_app_database"

#: Volume removed by "Remove ubersmith appliance webroot volume if present".
APP_WEBROOT_VOLUME = "ubersmith_app_webroot"

#: Name of the temporary container used by the "Step the database up to
#: mysql 5.7" / "Wait for mysql 5.7 container to come online" / "Run
#: mysql_upgrade for mysql 5.7" / "Remove mysql 5.7 container" tasks.
MYSQL_57_STEPUP_CONTAINER = "ubersmith-app_db_57"

#: Container names polled by the "Wait for containers to come online" task.
WAIT_FOR_CONTAINERS = [
    "ubersmith-app_web-1",
    "ubersmith-app_db-1",
    "ubersmith-app_cron-1",
]

#: Type of the subprocess runner callable injectable into the compose
#: functions for testing. Matches the subset of subprocess.run's signature
#: this module relies on.
SubprocessRunner = Callable[..., subprocess.CompletedProcess]

#: Sleep callable injectable into step_up_mysql_57's wait loop, matching
#: time.sleep's signature (see docker_ops.SleepFn).
SleepFn = docker_ops.SleepFn


def _default_runner(
    cmd: Sequence[str], *, cwd: Path, env: Mapping[str, str]
) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), cwd=str(cwd), env=dict(env), check=True)


def _chown(path: Path, uid: int, gid: int) -> None:
    """Thin wrapper around os.chown so ownership changes can be
    monkeypatched/skipped in tests without needing real privileges."""
    os.chown(path, uid, gid)


def create_config_directories(
    appliance_home: Path,
    owner_uid: int,
    owner_gid: int,
    *,
    chown: Optional[Callable[[Path, int, int], None]] = None,
) -> None:
    """Create the appliance configuration directory tree.

    Mirrors the "Create appliance configuration directories" task: creates
    every directory in `CONFIG_DIRECTORIES` (relative to `appliance_home`)
    with mode 0775 and the given owner uid/gid.
    """
    appliance_home = Path(appliance_home)
    chown_fn = chown if chown is not None else _chown

    for relative_path in CONFIG_DIRECTORIES:
        directory = appliance_home / relative_path
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(CONFIG_DIRECTORY_MODE)
        try:
            chown_fn(directory, owner_uid, owner_gid)
        except (PermissionError, LookupError, OSError):
            # Best-effort: changing ownership requires privileges this
            # process may not have (e.g. non-root dev/test runs). The
            # directory is still created with the correct mode.
            pass


def copy_static_files(appliance_home: Path) -> None:
    """Copy the static helper scripts into place.

    Mirrors the "Copy backup script", "Copy appliance_restart", "Copy
    appliance_start", and "Copy appliance_upgrade" tasks:

        * backup_rrds.sh       -> <appliance_home>/backup_rrds.sh       (0700, always overwritten)
        * appliance_restart.sh -> <appliance_home>/appliance_restart.sh (0700, left alone if present)
        * appliance_start.sh   -> <appliance_home>/appliance_start.sh   (0700, left alone if present)
        * appliance_upgrade.sh -> <appliance_home>/appliance_upgrade.sh (0700, left alone if present)
    """
    appliance_home = Path(appliance_home)
    appliance_home.mkdir(parents=True, exist_ok=True)

    for filename in ALWAYS_OVERWRITE_HELPER_SCRIPTS:
        src = FILES_DIR / filename
        dest = appliance_home / filename
        shutil.copyfile(src, dest)
        dest.chmod(HELPER_SCRIPT_MODE)

    for filename in IF_ABSENT_HELPER_SCRIPTS:
        dest = appliance_home / filename
        if dest.exists():
            continue
        src = FILES_DIR / filename
        shutil.copyfile(src, dest)
        dest.chmod(HELPER_SCRIPT_MODE)


def get_app_web_container_env(client: Optional["docker.DockerClient"] = None) -> list:
    """Return the raw ``Config.Env`` list of the ubersmith-app_web-1 container.

    Mirrors the "Check for remote database" task:
    ``community.docker.docker_container_info`` on ``ubersmith-app_web-1``,
    registered as ``app_web_container_info`` -- later tasks key off
    ``app_web_container_info.container.Config.Env`` to decide whether the
    upgrade is dealing with a local (in-stack) or remote database.
    """
    if client is None:
        client = docker.from_env()

    container = client.containers.get(APP_WEB_CONTAINER_NAME)
    return list(container.attrs["Config"]["Env"])


def is_local_database(env: Sequence[str]) -> bool:
    """Return whether the app_web container's env indicates a local database.

    Mirrors the exact Ansible expression used throughout main.yml to gate
    local-database-only upgrade steps:
    ``"'DATABASE_HOST=app_db' in app_web_container_info.container.Config.Env"``
    -- a literal substring-in-list membership check, not a dict lookup (the
    variable is only ever set to the hostname "app_db" for the bundled/local
    database container; anything else, including a remote host, will not
    match this exact string).
    """
    return "DATABASE_HOST=app_db" in env


def compose_pull(
    appliance_home: Path,
    *,
    runner: Optional[SubprocessRunner] = None,
    env: Optional[Mapping[str, str]] = None,
) -> None:
    """Pull the appliance images referenced by docker-compose.yml.

    Mirrors the "Run docker compose pull" task: ``docker compose -p
    ubersmith pull``, run with ``chdir: appliance_home``.
    """
    if runner is None:
        runner = _default_runner

    base_env = dict(env) if env is not None else dict(os.environ)

    cmd = ["docker", "compose", "-p", "ubersmith", "pull"]
    runner(cmd, cwd=Path(appliance_home), env=base_env)


def stop_containers(
    appliance_home: Path,
    *,
    runner: Optional[SubprocessRunner] = None,
    env: Optional[Mapping[str, str]] = None,
) -> None:
    """Stop and remove the existing appliance containers via ``docker compose``.

    Mirrors the "Stop existing containers" (upgrade_only) task: ``docker
    compose -p ubersmith rm -sf``, run with ``chdir: appliance_home``. Unlike
    ``docker_ops.stop_containers``, no explicit service list is given -- this
    removes every service defined by the compose file.
    """
    if runner is None:
        runner = _default_runner

    base_env = dict(env) if env is not None else dict(os.environ)

    cmd = ["docker", "compose", "-p", "ubersmith", "rm", "-sf"]
    runner(cmd, cwd=Path(appliance_home), env=base_env)


def get_existing_volumes(
    appliance_home: Path,
    *,
    runner: Optional[SubprocessRunner] = None,
    env: Optional[Mapping[str, str]] = None,
) -> list:
    """Return the list of existing Docker volume names.

    Mirrors the "Get existing docker volumes" task: ``docker volume ls -q``,
    registered as ``volume_output``, run with ``chdir: appliance_home``.
    """
    if runner is None:
        runner = _default_runner

    base_env = dict(env) if env is not None else dict(os.environ)

    cmd = ["docker", "volume", "ls", "-q"]
    result = runner(cmd, cwd=Path(appliance_home), env=base_env)
    stdout = getattr(result, "stdout", "") or ""
    if isinstance(stdout, bytes):
        stdout = stdout.decode()
    return [line for line in stdout.splitlines() if line.strip()]


def remove_webroot_volume_if_present(
    appliance_home: Path,
    volumes: Sequence[str],
    *,
    runner: Optional[SubprocessRunner] = None,
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    """Remove the appliance webroot volume if it exists, so it can be replaced.

    Mirrors the "Remove ubersmith appliance webroot volume if present" task:
    ``docker volume rm ubersmith_app_webroot``, gated on
    ``volume_output.stdout.find('ubersmith_app_webroot') != -1``, run with
    ``chdir: appliance_home``. Returns whether the removal ran.
    """
    if APP_WEBROOT_VOLUME not in volumes:
        return False

    if runner is None:
        runner = _default_runner

    base_env = dict(env) if env is not None else dict(os.environ)

    cmd = ["docker", "volume", "rm", APP_WEBROOT_VOLUME]
    runner(cmd, cwd=Path(appliance_home), env=base_env)
    return True


def chown_database_files(client: Optional["docker.DockerClient"] = None) -> None:
    """Chown the ubersmith_app_database volume to uid/gid 1001.

    Mirrors the "Make sure UID/GID 1001 owns the database files" task: runs
    a short-lived busybox container (root user, auto-remove) that runs
    ``chown -R 1001:1001 /mysql`` with the ``ubersmith_app_database`` volume
    bind-mounted at ``/mysql``. Only relevant when `is_local_database` is
    True -- the caller is responsible for that check, mirroring the task's
    ``when: "'DATABASE_HOST=app_db' in app_web_container_info.container.Config.Env"``.
    """
    if client is None:
        client = docker.from_env()

    client.containers.run(
        image="busybox",
        command="chown -R 1001:1001 /mysql",
        user="root",
        remove=True,
        volumes={APP_DATABASE_VOLUME: {"bind": "/mysql", "mode": "rw"}},
    )


def compose_up(
    appliance_home: Path,
    *,
    runner: Optional[SubprocessRunner] = None,
    env: Optional[Mapping[str, str]] = None,
) -> None:
    """Bring up all appliance containers via ``docker compose``.

    Mirrors the "Run docker compose up -d" task: ``docker compose -p
    ubersmith up -d``, run with ``chdir: appliance_home``. Unlike
    ``docker_ops.compose_up``, no explicit service list or ``--quiet-pull``
    flag is given -- this brings up every service defined by the compose
    file.
    """
    if runner is None:
        runner = _default_runner

    base_env = dict(env) if env is not None else dict(os.environ)

    cmd = ["docker", "compose", "-p", "ubersmith", "up", "-d"]
    runner(cmd, cwd=Path(appliance_home), env=base_env)


def wait_for_containers_healthy(
    *,
    client: Optional["docker.DockerClient"] = None,
    retries: int = 10,
    delay: int = 30,
    sleep: Optional[SleepFn] = None,
) -> None:
    """Poll the appliance containers until Docker reports them healthy.

    Mirrors the "Wait for containers to come online" task: polls
    ``ubersmith-app_web-1``, ``ubersmith-app_db-1``, ``ubersmith-app_cron-1``
    until ``State.Health.Status == "healthy"``, with ``retries: 10`` /
    ``delay: 30``. Delegates to ``docker_ops.wait_for_containers_healthy``,
    which is functionally identical (same polling loop, just a different
    container name list).
    """
    docker_ops.wait_for_containers_healthy(
        WAIT_FOR_CONTAINERS,
        client=client,
        retries=retries,
        delay=delay,
        sleep=sleep,
    )


def prune_old_images(
    client: Optional["docker.DockerClient"] = None, until: str = "2160h"
) -> None:
    """Prune dangling/unused Docker images.

    Mirrors the "Docker image cleanup" task: ``community.docker.docker_prune``
    with ``images: true`` (no ``images_filters`` given, unlike ubersmith's
    equivalent task -- so this prunes with Docker's default filters).
    Delegates to ``docker_ops.prune_old_images``, which is functionally
    identical.
    """
    docker_ops.prune_old_images(client=client, until=until)


def step_up_mysql_57(
    registry: str,
    appliance_version: str,
    containers_release_version: str,
    app_mysql_version: str,
    is_local_database: bool,
    *,
    client: Optional["docker.DockerClient"] = None,
    sleep: Optional[SleepFn] = None,
    retries: int = 10,
    delay: int = 30,
) -> bool:
    """Step an appliance mysql 5.6 database up to 5.7, if necessary.

    Mirrors the four tightly-coupled tasks "Step the database up to mysql
    5.7, if necessary" / "Wait for mysql 5.7 container to come online" /
    "Run mysql_upgrade for mysql 5.7" / "Remove mysql 5.7 container":

        1. If `app_mysql_version` != "5.6" or `is_local_database` is False,
           does nothing and returns False -- mirrors the ``when:`` gating
           shared by the first three tasks (
           ``'DATABASE_HOST=app_db' in app_web_container_info.container.Config.Env``
           and ``app_mysql_version == "5.6"``).
        2. Otherwise, starts a temporary ``ubersmith-app_db_57`` container
           from ``{{ registry }}/ps57:{{ appliance_version }}-{{
           containers_release_version }}``, running ``mysqld
           --skip-grant-tables`` with the ``ubersmith_app_database`` volume
           mounted at ``/var/lib/mysql``.
        3. Polls it until ``State.Health.Status == "healthy"`` (retries=10,
           delay=30 by default).
        4. Execs ``mysql_upgrade -u root --skip-password`` in it. Ansible's
           ``failed_when`` only fails on exit codes other than 0 or 2, so
           this raises RuntimeError for any other return code.
        5. Removes the container ("Remove mysql 5.7 container" has no
           ``when:`` gate in the Ansible role, so this step always runs once
           the container was started -- including via a ``finally`` so the
           container is cleaned up even if the upgrade exec fails).

    Returns True if the step-up ran (regardless of whether mysql_upgrade
    reported changes), False if it was skipped by the gating conditions.
    """
    if app_mysql_version != "5.6" or not is_local_database:
        return False

    if client is None:
        client = docker.from_env()
    if sleep is None:
        sleep = time.sleep

    image = f"{registry}/ps57:{appliance_version}-{containers_release_version}"

    client.containers.run(
        name=MYSQL_57_STEPUP_CONTAINER,
        image=image,
        command="mysqld --skip-grant-tables",
        detach=True,
        volumes={APP_DATABASE_VOLUME: {"bind": "/var/lib/mysql", "mode": "rw"}},
    )

    try:
        docker_ops.wait_for_containers_healthy(
            [MYSQL_57_STEPUP_CONTAINER],
            client=client,
            retries=retries,
            delay=delay,
            sleep=sleep,
        )

        container = client.containers.get(MYSQL_57_STEPUP_CONTAINER)
        exit_code, _output = container.exec_run(
            "/bin/sh -c 'mysql_upgrade -u root --skip-password'"
        )
        if exit_code not in (0, 2):
            raise RuntimeError(
                f"mysql_upgrade in {MYSQL_57_STEPUP_CONTAINER} exited "
                f"{exit_code} (expected 0 or 2)"
            )
    finally:
        container = client.containers.get(MYSQL_57_STEPUP_CONTAINER)
        container.remove(force=True)

    return True
