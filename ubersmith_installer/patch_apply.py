"""Patch application for the Ubersmith installer.

This module is a faithful port of ``patch_ubersmith.yml`` (repo root) AS
ACTUALLY INVOKED by ``patch_ubersmith.sh``, the standalone shell script
admins run to fetch and apply an official Ubersmith patch release. It
mirrors, in order:

    * "Fail if patches volume is not in docker compose file"
    * "Determine if the installation has been patched" / "Pause for admin
      if ubersmith has been patched" (see the note below on why this is
      what actually runs, not the "Remove .patched file"/"Remove patches
      directory" tasks)
    * "Determine available patches" (GitHub releases API, filtered by
      ``ubersmith_version``)
    * "Prompt for patch id"
    * "Retrieve assets for selected patch" / "Retrieve selected patch"
      (download + unpack the chosen release asset)
    * "Restart the ubersmith web container"
    * "Give ubersmith user ownership over patch files" / "Copy patch files
      into place"
    * "Write patch data to .patched file"

Note on cleanup semantics: ``patch_ubersmith.yml`` DOES contain "Remove
.patched file" / "Remove patches directory" tasks, but they are tagged
``remove_patches``, and ``patch_ubersmith.sh`` always invokes the playbook
with ``--skip-tags remove_patches`` -- so in real-world usage those two
tasks NEVER run. What actually runs instead is the untagged "Determine if
the installation has been patched" + "Pause for admin if ubersmith has been
patched" tasks, which only WARN the admin (reminding them to review
``.patched`` for conflicts) if a prior patch marker exists -- they do not
delete anything. This module replicates that real behavior
(:func:`warn_if_already_patched`), not the skipped-in-practice destructive
tasks. This is a deliberately different concern from
:mod:`ubersmith_installer.patch_cleanup`, which mirrors the separate
``upgrade_only``, ``when: interactive`` gated cleanup tasks that run during
an interactive *upgrade* (not a patch apply).
"""

from __future__ import annotations

import subprocess
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

import click
import requests

from . import state

#: Mirrors the `all_patches` var: the GitHub releases API endpoint for the
#: ubersmith-patches repo.
RELEASES_URL = "https://api.github.com/repos/TeamUbersmith/ubersmith-patches/releases"

#: Mirrors the `Accept` header sent by the `lookup('url', ...)` calls.
GITHUB_ACCEPT_HEADER = "application/vnd.github.v3+json"

#: The exact string checked for in docker-compose.override.yml by "Fail if
#: patches volume is not in docker compose file".
PATCHES_MOUNT_MARKER = "/var/www/ubersmith_root/app/patches"

#: Container the web-container-targeted tasks operate on.
WEB_CONTAINER_NAME = "ubersmith-web-1"

#: Type of the HTTP getter injectable for testing. Matches the subset of
#: `requests.get`'s signature this module relies on: a callable taking a URL
#: (and optional headers) and returning an object with `.json()` and
#: `.content`/`.raise_for_status()`.
HttpGetter = Callable[..., "requests.Response"]

#: Type of the subprocess runner injectable into apply_patch's compose
#: restart, matching the pattern established in docker_ops.py.
SubprocessRunner = Callable[..., subprocess.CompletedProcess]


def _default_http_get(url: str, **kwargs) -> "requests.Response":
    headers = kwargs.pop("headers", None) or {"Accept": GITHUB_ACCEPT_HEADER}
    response = requests.get(url, headers=headers, **kwargs)
    response.raise_for_status()
    return response


def _default_runner(
    cmd: Sequence[str], *, cwd: Path
) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), cwd=str(cwd), check=True)


def check_patches_supported(ubersmith_home: Path) -> bool:
    """Mirror "Fail if patches volume is not in docker compose file".

    Reads ``<ubersmith_home>/docker-compose.override.yml`` and returns
    whether it contains the ``/var/www/ubersmith_root/app/patches`` mount
    path. Callers should treat False as "patches are not supported for this
    install" and refuse to proceed (the Ansible task hard-fails with a
    message pointing the admin to support@ubersmith.com).

    Returns False (rather than raising) if the override file doesn't exist,
    since a missing override file can never contain the mount.
    """
    override_path = Path(ubersmith_home) / "docker-compose.override.yml"
    if not override_path.exists():
        return False
    contents = override_path.read_text(encoding="utf-8")
    return PATCHES_MOUNT_MARKER in contents


#: Matches the exact text of "Pause for admin if ubersmith has been patched".
ALREADY_PATCHED_WARNING_TEMPLATE = (
    "Ubersmith appears to be patched. Review the patch configuration file "
    "in {patched_path} before proceeding to avoid conflicts."
)


def warn_if_already_patched(ubersmith_home: Path, interactive: bool) -> bool:
    """Mirror "Determine if the installation has been patched" / "Pause for
    admin if ubersmith has been patched" -- the tasks that actually run in
    real usage (see the module docstring for why the "Remove .patched
    file"/"Remove patches directory" tasks are NOT replicated here: they're
    always skipped via ``--skip-tags remove_patches`` in
    ``patch_ubersmith.sh``).

    Does not delete or modify anything -- only warns. In interactive mode,
    blocks on a confirmation (matching the Ansible ``pause``); in
    non-interactive mode, logs the same message and continues, since a
    blocking prompt has no meaning there.

    Returns whether a prior ``.patched`` marker was found (i.e. whether the
    warning applied).
    """
    ubersmith_home = Path(ubersmith_home)
    patched_path = ubersmith_home / ".patched"

    if not patched_path.exists():
        return False

    message = ALREADY_PATCHED_WARNING_TEMPLATE.format(patched_path=patched_path)
    if interactive:
        click.echo(message)
        click.confirm("Continue applying the new patch?", default=True, abort=True)
    else:
        click.echo(f"[info] {message}")

    return True


def list_available_patches(
    ubersmith_version: str, *, http_get: Optional[HttpGetter] = None
) -> list[dict]:
    """Mirror "Determine available patches".

    Fetches all releases from `RELEASES_URL` and filters to those whose
    ``name`` contains `ubersmith_version` as a substring -- the exact
    ``contains(name, ubersmith_version)`` JMESPath predicate used by the
    ``patch_name_query`` in the Ansible task.

    Returns a list of dicts (in API response order), one per matching
    release, each with keys:

        * ``id`` -- the release id (used later to fetch/apply the patch)
        * ``name`` -- the release name
        * ``html_url`` -- the release's GitHub page (recorded later in
          ``.patched``)
        * ``asset_url`` -- the first asset's ``browser_download_url``
          (mirrors ``assets[].browser_download_url | [0]``), or None if the
          release has no assets

    ``http_get`` is injectable for testing; it defaults to a thin wrapper
    around ``requests.get`` sending the GitHub v3 Accept header.
    """
    getter = http_get if http_get is not None else _default_http_get
    response = getter(RELEASES_URL, headers={"Accept": GITHUB_ACCEPT_HEADER})
    releases = response.json()

    matches = []
    for release in releases:
        name = release.get("name") or ""
        if ubersmith_version not in name:
            continue
        assets = release.get("assets") or []
        asset_url = assets[0].get("browser_download_url") if assets else None
        matches.append(
            {
                "id": release["id"],
                "name": name,
                "html_url": release.get("html_url"),
                "asset_url": asset_url,
            }
        )
    return matches


def prompt_for_patch_id(patches: Sequence[dict], ubersmith_version: str) -> str:
    """Mirror "Prompt for patch id".

    Displays the filtered patch list (name, URL, ID for each) exactly as
    the Ansible ``ansible.builtin.pause`` prompt does, then asks the admin
    to type the patch ID to apply. Returns the raw string entered (matching
    ``patch_id.user_input``, which is used unvalidated as a dict/URL key
    downstream).
    """
    lines = [f"=== Available Patches for Ubersmith {ubersmith_version} ==="]
    for patch in patches:
        lines.append("")
        lines.append(f"Name: {patch['name']}")
        lines.append(f"URL : {patch['html_url']}")
        lines.append(f"ID  : {patch['id']}")
    lines.append("")
    click.echo("\n".join(lines))
    return click.prompt("Enter the patch ID to apply", type=str)


def _extract_archive(asset_path: Path, dest_dir: Path) -> list[str]:
    """Unpack `asset_path` into `dest_dir`, detecting the archive type from
    its filename extension (tar/tar.gz/tgz/zip), and return the list of
    extracted member paths -- mirroring the ``list_files`` output of
    ``ansible.builtin.unarchive``.
    """
    name = asset_path.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(asset_path) as archive:
            archive.extractall(dest_dir)
            return list(archive.namelist())

    # tarfile.open auto-detects compression (.tar, .tar.gz, .tgz, .tar.bz2, ...)
    with tarfile.open(asset_path) as archive:
        archive.extractall(dest_dir)
        return [member.name for member in archive.getmembers()]


def download_and_unpack_patch(
    patch_id,
    asset_url: str,
    ubersmith_home: Path,
    *,
    http_get: Optional[HttpGetter] = None,
) -> list[str]:
    """Mirror "Retrieve selected patch" (and the directory-creation task
    immediately before it).

    Downloads the release asset at `asset_url`, unpacks it into
    ``<ubersmith_home>/app/patches/<patch_id>/`` (created if necessary), and
    returns the list of extracted file paths, mirroring
    ``ansible.builtin.unarchive``'s ``list_files: true`` output.

    Archive type is detected from `asset_url`'s filename extension (``.zip``
    vs. tar variants), using stdlib ``zipfile``/``tarfile`` -- no shelling
    out to ``tar``/``unzip``.
    """
    getter = http_get if http_get is not None else _default_http_get

    dest_dir = Path(ubersmith_home) / "app" / "patches" / str(patch_id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    response = getter(asset_url, headers={"Accept": "application/octet-stream"})
    asset_filename = asset_url.rsplit("/", 1)[-1]
    downloaded = dest_dir / asset_filename
    downloaded.write_bytes(response.content)

    try:
        return _extract_archive(downloaded, dest_dir)
    finally:
        downloaded.unlink(missing_ok=True)


def apply_patch(
    ubersmith_home: Path,
    patch_id,
    *,
    client=None,
    runner: Optional[SubprocessRunner] = None,
) -> None:
    """Mirror "Restart the ubersmith web container", "Give ubersmith user
    ownership over patch files", and "Copy patch files into place".

    Runs, in order:

        1. ``docker compose restart web`` (via `runner`, ``chdir:
           ubersmith_home``)
        2. In the web container: ``cd /var/www/ubersmith_root/app/patches;
           chown -R ubersmith:ubersmith *``
        3. In the web container: ``cd
           /var/www/ubersmith_root/app/patches/<patch_id>/; cp -a --suffix
           .bak --backup . ../../www/`` -- copies patch files into place,
           backing up (with a ``.bak`` suffix) anything they overwrite.

    `client` is an injectable Docker SDK client (as elsewhere in this
    codebase); `runner` is an injectable subprocess runner for the compose
    restart, matching `docker_ops`'s `SubprocessRunner` pattern.
    """
    if runner is None:
        runner = _default_runner
    runner(["docker", "compose", "restart", "web"], cwd=Path(ubersmith_home))

    if client is None:
        import docker

        client = docker.from_env()

    container = client.containers.get(WEB_CONTAINER_NAME)

    chown_command = (
        "/bin/bash -c \"cd /var/www/ubersmith_root/app/patches; "
        'chown -R ubersmith:ubersmith *"'
    )
    container.exec_run(chown_command)

    copy_command = (
        "/bin/bash -c \"cd /var/www/ubersmith_root/app/patches/"
        f'{patch_id}/; cp -a --suffix .bak --backup . ../../www/"'
    )
    container.exec_run(copy_command)


def record_patch_metadata(
    ubersmith_home: Path,
    patch_id,
    installer: str,
    github_page: str,
    *,
    install_date: Optional[str] = None,
) -> None:
    """Mirror "Write patch data to .patched file".

    Writes a ``Patch <patch_id>`` section to ``<ubersmith_home>/.patched``
    (an ini file, in the same read-modify-write style as
    :mod:`ubersmith_installer.state`, so any existing sections/other patches
    already recorded there are preserved) with three options:

        * ``installer`` -- who applied the patch (``ansible_user_id`` in
          the original task)
        * ``install_date`` -- defaults to now, formatted like Ansible's
          ``strftime('%a, %d %b %Y %T %z')``
        * ``github_page`` -- the release's ``html_url``
    """
    if install_date is None:
        install_date = datetime.now(timezone.utc).astimezone().strftime(
            "%a, %d %b %Y %H:%M:%S %z"
        )

    patched_path = Path(ubersmith_home) / ".patched"
    state.write_state(
        {
            "installer": installer,
            "install_date": install_date,
            "github_page": github_page,
        },
        path=patched_path,
        section=f"Patch {patch_id}",
    )
