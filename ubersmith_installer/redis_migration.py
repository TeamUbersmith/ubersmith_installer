"""Redis-volume migration dance for Ubersmith upgrades.

This module handles the narrow (but data-critical) piece of the upgrade
flow that migrates redis persistence data from the old topology -- where
the `redis` container itself owned `/data` -- to the current topology,
where a dedicated `redis-data` container/volume (`ubersmith_redis`) holds
it. It is a faithful port of these ``upgrade_only``-tagged tasks in
``roles/ubersmith/tasks/main.yml``:

    * "Get existing redis volume" / "Get existing webroot volume"
    * "Trigger save of redis data if redis volume does not already exist"
    * "Make a copy of dump.rdb if redis volume does not already exist"
    * "Remove ubersmith webroot volume if present"
    * "Copy dump.rdb if redis volume does not exist"
    * "Give redis user ownership over dump.rdb"

IMPORTANT -- two-phase migration:

The migration spans a container restart and therefore cannot be done in
one function call. Per the task ordering in main.yml:

    1. BEFORE the existing containers are stopped: check whether the
       ``ubersmith_redis`` volume already exists. If it does not, trigger
       a redis SAVE on the old ``ubersmith-redis-1`` container and copy
       ``dump.rdb`` out of it to `ubersmith_home` (`migrate_redis_volume`,
       via `trigger_redis_save` + `copy_redis_dump_out`).
    2. Containers are stopped/removed and (elsewhere) the webroot volume
       is dropped and new containers are brought up -- this is NOT part
       of this module.
    3. AFTER the new containers (including ``ubersmith-redis-data-1``) are
       up: copy the previously-saved ``dump.rdb`` into the new container
       and fix its ownership (`copy_redis_dump_in` + `chown_redis_dump`).

`migrate_redis_volume` only performs phase 1. Its boolean return value
tells the caller (the upgrade command) whether phase 3 must be run later
in the sequence -- if it returns False, the ``ubersmith_redis`` volume
already existed (an already-migrated, established install) and nothing
further needs to happen.

All functions accept an injectable docker SDK client/subprocess runner so
tests can exercise this module without a real Docker daemon.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

import docker
import docker.errors

#: Type of the subprocess runner callable injectable into the `docker cp`
#: helpers for testing. Matches docker_ops.SubprocessRunner's shape.
SubprocessRunner = Callable[..., subprocess.CompletedProcess]


def _default_runner(cmd: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), cwd=str(cwd), check=True)


def volume_exists(name: str, client: Optional["docker.DockerClient"] = None) -> bool:
    """Return whether a docker volume named `name` exists.

    Mirrors the "Get existing redis volume" / "Get existing webroot volume"
    tasks (``community.docker.docker_volume_info``), whose `.exists`
    result gates the rest of the migration.
    """
    if client is None:
        client = docker.from_env()

    try:
        client.volumes.get(name)
        return True
    except docker.errors.NotFound:
        return False


def trigger_redis_save(client: Optional["docker.DockerClient"] = None) -> None:
    """Trigger a `redis-cli SAVE` on the old `ubersmith-redis-1` container.

    Mirrors the "Trigger save of redis data if redis volume does not
    already exist" task (``community.docker.docker_container_exec``,
    ``failed_when: false``). Failures (e.g. the container isn't running)
    are expected and swallowed rather than raised.
    """
    if client is None:
        client = docker.from_env()

    try:
        container = client.containers.get("ubersmith-redis-1")
        container.exec_run(["/bin/bash", "-c", "/usr/local/bin/redis-cli SAVE"])
    except Exception:
        # Mirrors `failed_when: false` -- this is expected to sometimes
        # fail (e.g. if the container doesn't exist or isn't running).
        pass


def copy_redis_dump_out(
    ubersmith_home: Path, *, runner: Optional[SubprocessRunner] = None
) -> None:
    """Copy `dump.rdb` out of the old redis container into `ubersmith_home`.

    Mirrors the "Make a copy of dump.rdb if redis volume does not already
    exist" task: ``docker cp ubersmith-redis-1:/data/dump.rdb .``, run with
    ``chdir: ubersmith_home``. `docker cp` has no first-class Python SDK
    method, so this shells out via an injectable subprocess runner.
    """
    if runner is None:
        runner = _default_runner

    cmd = ["docker", "cp", "ubersmith-redis-1:/data/dump.rdb", "."]
    runner(cmd, cwd=Path(ubersmith_home))


def remove_webroot_volume_if_present(
    client: Optional["docker.DockerClient"] = None,
) -> None:
    """Remove the `ubersmith_webroot` volume if it exists.

    Mirrors the "Remove ubersmith webroot volume if present" task
    (``community.docker.docker_volume``, ``state: absent``, gated on
    ``webroot_volume_output.exists``) so the new version's webroot content
    can replace it.
    """
    if client is None:
        client = docker.from_env()

    if volume_exists("ubersmith_webroot", client):
        client.volumes.get("ubersmith_webroot").remove()


def copy_redis_dump_in(
    ubersmith_home: Path, *, runner: Optional[SubprocessRunner] = None
) -> None:
    """Copy the saved `dump.rdb` into the new `ubersmith-redis-data-1` container.

    Mirrors the "Copy dump.rdb if redis volume does not exist" task:
    ``docker cp dump.rdb ubersmith-redis-data-1:/data/dump.rdb``, run with
    ``chdir: ubersmith_home``. Must be called only after the new
    `redis-data` container is up (phase 3 of the migration -- see module
    docstring).
    """
    if runner is None:
        runner = _default_runner

    cmd = ["docker", "cp", "dump.rdb", "ubersmith-redis-data-1:/data/dump.rdb"]
    runner(cmd, cwd=Path(ubersmith_home))


def chown_redis_dump(client: Optional["docker.DockerClient"] = None) -> None:
    """Fix ownership of the migrated `dump.rdb` inside `ubersmith-redis-data-1`.

    Mirrors the "Give redis user ownership over dump.rdb" task
    (``community.docker.docker_container_exec``, ``failed_when: false``).
    Failures are swallowed for the same reason as `trigger_redis_save`.
    """
    if client is None:
        client = docker.from_env()

    try:
        container = client.containers.get("ubersmith-redis-data-1")
        container.exec_run(["/bin/bash", "-c", "chown redis:redis /data/dump.rdb"])
    except Exception:
        pass


def migrate_redis_volume(
    ubersmith_home: Path,
    *,
    client: Optional["docker.DockerClient"] = None,
    runner: Optional[SubprocessRunner] = None,
) -> bool:
    """Run phase 1 of the redis-volume migration dance, before containers restart.

    Checks whether the ``ubersmith_redis`` volume already exists:

        * If it does, this is an already-migrated/established install --
          nothing to do, returns False.
        * If it does not (an old-topology install), triggers a redis SAVE
          on the old `ubersmith-redis-1` container and copies its
          `dump.rdb` out to `ubersmith_home`, then returns True.

    IMPORTANT: a True return means the caller MUST invoke
    `copy_redis_dump_in` and `chown_redis_dump` again LATER, after the new
    containers (specifically `ubersmith-redis-data-1`) have been brought
    up -- this function only performs the "out" half of the copy, which
    must happen before the old containers are stopped. See the module
    docstring for the full three-phase ordering.
    """
    if client is None:
        client = docker.from_env()

    if volume_exists("ubersmith_redis", client):
        return False

    trigger_redis_save(client=client)
    copy_redis_dump_out(ubersmith_home, runner=runner)
    return True
