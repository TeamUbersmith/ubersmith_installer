"""OS and Docker preflight checks for the Ubersmith installer."""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass, field

import distro

# Mirrors roles/common/vars/main.yml
SUPPORTED_OS = ["Debian", "CentOS", "Rocky", "Ubuntu", "AlmaLinux"]
SUPPORTED_UBUNTU_VER = "20"
SUPPORTED_CENTOS_VER = "8"
SUPPORTED_DEBIAN_VER = "10"
MIN_UBUNTU_KERNEL = "4.4.0"
MIN_CENTOS_KERNEL = "3.10.0-693"
MIN_DEBIAN_KERNEL = "4.0"
DOCKER_OK_VERSION = "20.10.18"

# Mapping from distro.id() values to the Ansible-style distribution names
# used in supported_os / roles/common/vars/main.yml.
_DISTRO_ID_TO_NAME = {
    "debian": "Debian",
    "centos": "CentOS",
    "rocky": "Rocky",
    "ubuntu": "Ubuntu",
    "almalinux": "AlmaLinux",
}


def _version_tuple(version: str) -> tuple:
    """Turn a version string into a tuple of ints/strings for comparison.

    Splits on non-alphanumeric separators ('.', '-', etc.) and converts
    numeric parts to int so that e.g. "3.10.0-693" >= "3.9.0-100" compares
    correctly component by component, rather than lexicographically.
    """
    parts = re.split(r"[.\-+]", version.strip())
    result = []
    for part in parts:
        if part.isdigit():
            result.append((1, int(part)))
        else:
            # Non-numeric components sort after numeric ones at the same
            # position, but still compare against each other lexically.
            result.append((0, part))
    return tuple(result)


def version_gte(version: str, minimum: str) -> bool:
    """Return True if `version` >= `minimum` using semantic-ish comparison."""
    return _version_tuple(version) >= _version_tuple(minimum)


@dataclass
class PreflightResult:
    """Structured outcome of running the preflight checks."""

    ok: bool = True
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.ok = False

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


def _check_os(result: PreflightResult) -> None:
    distro_id = distro.id()
    distro_name = _DISTRO_ID_TO_NAME.get(distro_id.lower())
    version = distro.version()
    kernel = platform.release()

    if distro_name is None or distro_name not in SUPPORTED_OS:
        result.add_error(
            "Ensure Host is running Debian, CentOS, Rocky, Ubuntu, or AlmaLinux. "
            f"Detected distribution: {distro.name() or distro_id!r}."
        )
        return

    if distro_name == "Ubuntu":
        if not version_gte(version, SUPPORTED_UBUNTU_VER):
            result.add_error(
                f"Ensure Host is running supported Ubuntu LTS (>= {SUPPORTED_UBUNTU_VER}). "
                f"Detected version: {version}."
            )
        if not version_gte(kernel, MIN_UBUNTU_KERNEL):
            result.add_error(
                f"Ensure Host is running Ubuntu with {MIN_UBUNTU_KERNEL}+ kernel. "
                f"Detected kernel: {kernel}."
            )
    elif distro_name in ("CentOS", "Rocky"):
        if not version_gte(version, SUPPORTED_CENTOS_VER):
            result.add_error(
                f"Ensure Host is running supported CentOS/Rocky (>= {SUPPORTED_CENTOS_VER}). "
                f"Detected version: {version}."
            )
        if not version_gte(kernel, MIN_CENTOS_KERNEL):
            result.add_error(
                f"Ensure Host is running CentOS/Rocky with kernel newer than "
                f"{MIN_CENTOS_KERNEL}. Detected kernel: {kernel}."
            )
    elif distro_name == "Debian":
        if not version_gte(version, SUPPORTED_DEBIAN_VER):
            result.add_error(
                f"Ensure Host is running supported Debian (>= {SUPPORTED_DEBIAN_VER}). "
                f"Detected version: {version}."
            )
        if not version_gte(kernel, MIN_DEBIAN_KERNEL):
            result.add_error(
                f"Ensure Host is running Debian with {MIN_DEBIAN_KERNEL}+ kernel. "
                f"Detected kernel: {kernel}."
            )
    # AlmaLinux has no explicit version/kernel requirement in the Ansible
    # source, so nothing further to check there.


def _check_docker(result: PreflightResult, docker_module=None) -> None:
    if docker_module is None:
        import docker as docker_module

    try:
        client = docker_module.from_env()
        version_info = client.version()
    except Exception as exc:  # noqa: BLE001 - any docker/connection error
        result.add_error(
            "Docker does not appear to be installed or reachable. Please install "
            f"Docker (https://docs.docker.com/engine/installation/). Details: {exc}"
        )
        return

    server_version = version_info.get("Version") if version_info else None
    if not server_version:
        result.add_error(
            "Could not determine the installed Docker server version. Please "
            "verify your Docker installation."
        )
        return

    if not version_gte(server_version, DOCKER_OK_VERSION):
        result.add_error(
            "The Docker version installed is not supported. Please upgrade Docker "
            f"(https://docs.docker.com/engine/installation/). Detected version: "
            f"{server_version}, required >= {DOCKER_OK_VERSION}."
        )


def _check_docker_service_enabled(result: PreflightResult) -> None:
    """Best-effort check that the docker service is enabled.

    Skipped on Darwin/Windows, mirroring the Ansible task's
    `ansible_os_family != "Darwin"` / `!= "Windows"` conditions.
    """
    system = platform.system()
    if system in ("Darwin", "Windows"):
        return

    try:
        import subprocess

        proc = subprocess.run(
            ["systemctl", "is-enabled", "docker"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or proc.stdout.strip() not in (
            "enabled",
            "static",
            "alias",
        ):
            result.add_warning(
                "The docker service does not appear to be enabled. You may need "
                "to run 'systemctl enable docker'."
            )
    except FileNotFoundError:
        # systemctl not available (e.g. non-systemd system); nothing we can do.
        result.add_warning(
            "Could not verify whether the docker service is enabled (systemctl "
            "not found)."
        )


def run_preflight_checks(
    docker_module=None, check_service_enabled: bool = True
) -> PreflightResult:
    """Run all preflight checks and return a structured result.

    Parameters
    ----------
    docker_module:
        Optional injection point for the `docker` module/client factory,
        primarily used for testing.
    check_service_enabled:
        Whether to attempt to verify/enable the docker service. Disabled by
        default in tests since it shells out to systemctl.
    """
    result = PreflightResult()

    _check_os(result)
    _check_docker(result, docker_module=docker_module)

    if check_service_enabled:
        _check_docker_service_enabled(result)

    return result
