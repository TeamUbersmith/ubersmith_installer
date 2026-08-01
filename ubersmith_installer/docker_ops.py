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


def compose_up(
    ubersmith_home: Path,
    extra_env: Optional[Mapping[str, str]] = None,
    *,
    runner: Optional[SubprocessRunner] = None,
    env: Optional[Mapping[str, str]] = None,
) -> None:
    """Bring up the core Ubersmith containers via ``docker compose``.

    Mirrors the "Update and start ubersmith containers" task:
    ``docker compose up -d --quiet-pull --no-color web cron db php solr mail
    rsyslog rwhois redis-data``, run with ``chdir: ubersmith_home`` and (in
    that task) ``MAINTENANCE: "1"`` merged into the environment.
    """
    if runner is None:
        runner = _default_runner

    base_env = dict(env) if env is not None else dict(os.environ)
    if extra_env:
        base_env.update(extra_env)

    cmd = [
        "docker",
        "compose",
        "up",
        "-d",
        "--quiet-pull",
        "--no-color",
        *COMPOSE_UP_SERVICES,
    ]
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
