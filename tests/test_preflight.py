"""Tests for ubersmith_installer.preflight."""

from unittest.mock import MagicMock

import pytest

from ubersmith_installer import preflight


class FakeDockerModule:
    """A fake `docker` module for injection into run_preflight_checks."""

    def __init__(self, version_info=None, raise_on_from_env=None):
        self._version_info = version_info
        self._raise_on_from_env = raise_on_from_env

    def from_env(self):
        if self._raise_on_from_env is not None:
            raise self._raise_on_from_env
        client = MagicMock()
        client.version.return_value = self._version_info
        return client


def _ok_docker_module(version="24.0.5"):
    return FakeDockerModule(version_info={"Version": version})


def _unreachable_docker_module():
    return FakeDockerModule(raise_on_from_env=ConnectionError("no docker daemon"))


def _old_docker_module():
    return FakeDockerModule(version_info={"Version": "19.03.1"})


def test_supported_os_and_version_passes(monkeypatch):
    monkeypatch.setattr(preflight.distro, "id", lambda: "ubuntu")
    monkeypatch.setattr(preflight.distro, "name", lambda: "Ubuntu")
    monkeypatch.setattr(preflight.distro, "version", lambda: "22.04")
    monkeypatch.setattr(preflight.platform, "release", lambda: "5.15.0-91-generic")
    monkeypatch.setattr(preflight.platform, "system", lambda: "Linux")

    result = preflight.run_preflight_checks(
        docker_module=_ok_docker_module(), check_service_enabled=False
    )

    assert result.ok is True
    assert result.errors == []


def test_unsupported_os_fails(monkeypatch):
    monkeypatch.setattr(preflight.distro, "id", lambda: "fedora")
    monkeypatch.setattr(preflight.distro, "name", lambda: "Fedora")
    monkeypatch.setattr(preflight.distro, "version", lambda: "39")
    monkeypatch.setattr(preflight.platform, "release", lambda: "6.5.0")
    monkeypatch.setattr(preflight.platform, "system", lambda: "Linux")

    result = preflight.run_preflight_checks(
        docker_module=_ok_docker_module(), check_service_enabled=False
    )

    assert result.ok is False
    assert any("Debian, CentOS, Rocky, Ubuntu, or AlmaLinux" in e for e in result.errors)


def test_old_kernel_fails(monkeypatch):
    monkeypatch.setattr(preflight.distro, "id", lambda: "ubuntu")
    monkeypatch.setattr(preflight.distro, "name", lambda: "Ubuntu")
    monkeypatch.setattr(preflight.distro, "version", lambda: "22.04")
    monkeypatch.setattr(preflight.platform, "release", lambda: "3.13.0-24-generic")
    monkeypatch.setattr(preflight.platform, "system", lambda: "Linux")

    result = preflight.run_preflight_checks(
        docker_module=_ok_docker_module(), check_service_enabled=False
    )

    assert result.ok is False
    assert any("kernel" in e for e in result.errors)


def test_docker_unreachable_fails(monkeypatch):
    monkeypatch.setattr(preflight.distro, "id", lambda: "debian")
    monkeypatch.setattr(preflight.distro, "name", lambda: "Debian")
    monkeypatch.setattr(preflight.distro, "version", lambda: "12")
    monkeypatch.setattr(preflight.platform, "release", lambda: "6.1.0-13-amd64")
    monkeypatch.setattr(preflight.platform, "system", lambda: "Linux")

    result = preflight.run_preflight_checks(
        docker_module=_unreachable_docker_module(), check_service_enabled=False
    )

    assert result.ok is False
    assert any("Docker does not appear to be installed" in e for e in result.errors)


def test_docker_too_old_fails(monkeypatch):
    monkeypatch.setattr(preflight.distro, "id", lambda: "debian")
    monkeypatch.setattr(preflight.distro, "name", lambda: "Debian")
    monkeypatch.setattr(preflight.distro, "version", lambda: "12")
    monkeypatch.setattr(preflight.platform, "release", lambda: "6.1.0-13-amd64")
    monkeypatch.setattr(preflight.platform, "system", lambda: "Linux")

    result = preflight.run_preflight_checks(
        docker_module=_old_docker_module(), check_service_enabled=False
    )

    assert result.ok is False
    assert any("Docker version installed is not supported" in e for e in result.errors)


@pytest.mark.parametrize(
    "distro_id,distro_name,version,kernel",
    [
        ("centos", "CentOS", "8", "3.10.0-693"),
        ("rocky", "Rocky", "9", "5.14.0"),
        ("almalinux", "AlmaLinux", "9", "5.14.0"),
        ("debian", "Debian", "11", "5.10.0"),
    ],
)
def test_other_supported_os_versions_pass(
    monkeypatch, distro_id, distro_name, version, kernel
):
    monkeypatch.setattr(preflight.distro, "id", lambda: distro_id)
    monkeypatch.setattr(preflight.distro, "name", lambda: distro_name)
    monkeypatch.setattr(preflight.distro, "version", lambda: version)
    monkeypatch.setattr(preflight.platform, "release", lambda: kernel)
    monkeypatch.setattr(preflight.platform, "system", lambda: "Linux")

    result = preflight.run_preflight_checks(
        docker_module=_ok_docker_module(), check_service_enabled=False
    )

    assert result.ok is True, result.errors
