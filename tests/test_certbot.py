"""Tests for ubersmith_installer.certbot.

These exercise the Let's Encrypt certificate request flow with a mocked
port-wait and a mocked docker SDK client -- no real port 80 or Docker
daemon is required.
"""

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from ubersmith_installer import certbot


UBERSMITH_HOME = Path("/usr/local/ubersmith")


def _expected_cert_volumes(ubersmith_home=UBERSMITH_HOME):
    return {
        str(ubersmith_home / "conf/certbot/etc"): {"bind": "/etc/letsencrypt", "mode": "rw"},
        str(ubersmith_home / "conf/certbot/lib"): {"bind": "/var/lib/letsencrypt", "mode": "rw"},
        str(ubersmith_home / "conf/certbot/log"): {"bind": "/var/log/letsencrypt", "mode": "rw"},
    }


def _expected_deploy_volumes(ubersmith_home=UBERSMITH_HOME):
    volumes = _expected_cert_volumes(ubersmith_home)
    volumes[str(ubersmith_home / "conf/ssl")] = {"bind": "/opt/certbot/deploy", "mode": "rw"}
    return volumes


def test_wait_for_port_available_returns_once_connect_raises_oserror():
    connect = MagicMock(side_effect=ConnectionRefusedError())

    certbot.wait_for_port_available(
        host="0.0.0.0", port=80, timeout=5, poll_interval=1, connect=connect
    )

    connect.assert_called_once()


def test_wait_for_port_available_retries_then_succeeds(monkeypatch):
    # First call: port still occupied (connects successfully). Second call:
    # port free (raises OSError).
    connect = MagicMock(side_effect=[None, OSError()])
    monkeypatch.setattr(certbot.time, "sleep", lambda _: None)

    certbot.wait_for_port_available(
        host="0.0.0.0", port=80, timeout=5, poll_interval=0.01, connect=connect
    )

    assert connect.call_count == 2


def test_wait_for_port_available_times_out(monkeypatch):
    # Always connects successfully -- port never becomes free.
    connect = MagicMock(return_value=None)
    times = iter([0, 0, 10, 10])
    monkeypatch.setattr(certbot.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(certbot.time, "sleep", lambda _: None)

    with pytest.raises(TimeoutError):
        certbot.wait_for_port_available(
            host="0.0.0.0", port=80, timeout=5, poll_interval=0.01, connect=connect
        )


@pytest.mark.parametrize("answer", ["yes", "Y", " YES ", "y"])
def test_request_letsencrypt_certificates_full_flow(answer):
    client = MagicMock()
    wait_for_port = MagicMock()
    cron_reader = MagicMock(return_value="")
    cron_writer = MagicMock()
    virtual_hosts = ["ubersmith.example.com", "billing.example.com"]

    certbot.request_letsencrypt_certificates(
        virtual_hosts,
        UBERSMITH_HOME,
        "admin@example.org",
        "v3.2.0",
        answer,
        client=client,
        wait_for_port=wait_for_port,
        uid=1000,
        gid=1000,
        cron_reader=cron_reader,
        cron_writer=cron_writer,
    )

    # Port wait happens three times: before cert request, before deploy
    # hooks, and after deploy hooks.
    assert wait_for_port.call_count == 3

    # The renewal cron task is installed once, using the given ubersmith_home.
    cron_writer.assert_called_once()
    assert f"{UBERSMITH_HOME}/ubersmith_certbot_renew.sh" in cron_writer.call_args[0][0]
    for wait_call in wait_for_port.call_args_list:
        assert wait_call.kwargs["host"] == "0.0.0.0"
        assert wait_call.kwargs["port"] == 80

    # containers.run called once per host for the cert request, and once
    # per host for the deploy hook: 2 hosts * 2 steps = 4 calls.
    assert client.containers.run.call_count == 4

    expected_image = "ghcr.io/teamubersmith/certbot:v3.2.0"
    cert_volumes = _expected_cert_volumes()
    deploy_volumes = _expected_deploy_volumes()

    expected_cert_calls = [
        call(
            image=expected_image,
            command=(
                f"certonly -n -d {host} --standalone --agree-tos "
                "-m admin@example.org"
            ),
            name="certbot",
            user="1000:1000",
            ports={"80/tcp": 80},
            remove=True,
            volumes=cert_volumes,
        )
        for host in virtual_hosts
    ]
    expected_deploy_calls = [
        call(
            image=expected_image,
            entrypoint="/etc/letsencrypt/renewal-hooks/deploy/ubersmith-deploy.sh",
            name="certbot",
            user="1000:1000",
            remove=True,
            environment={"RENEWED_DOMAINS": host},
            volumes=deploy_volumes,
        )
        for host in virtual_hosts
    ]

    actual_calls = client.containers.run.call_args_list
    assert actual_calls[:2] == expected_cert_calls
    assert actual_calls[2:] == expected_deploy_calls


@pytest.mark.parametrize("answer", ["no", "n", "", "  ", "nope", None])
def test_request_letsencrypt_certificates_noop_when_not_requested(answer):
    client = MagicMock()
    wait_for_port = MagicMock()

    certbot.request_letsencrypt_certificates(
        ["ubersmith.example.com"],
        UBERSMITH_HOME,
        "admin@example.org",
        "v3.2.0",
        answer,
        client=client,
        wait_for_port=wait_for_port,
        uid=1000,
        gid=1000,
    )

    wait_for_port.assert_not_called()
    client.containers.run.assert_not_called()


def test_request_letsencrypt_certificates_single_host_loops_once_per_step():
    client = MagicMock()
    wait_for_port = MagicMock()

    certbot.request_letsencrypt_certificates(
        ["ubersmith.example.com"],
        UBERSMITH_HOME,
        "admin@example.org",
        "v3.2.0",
        "yes",
        client=client,
        wait_for_port=wait_for_port,
        uid=1000,
        gid=1000,
        cron_reader=lambda: "",
        cron_writer=MagicMock(),
    )

    # One virtual host -> one cert-request call + one deploy-hook call.
    assert client.containers.run.call_count == 2


def test_request_letsencrypt_certificates_skips_cron_when_install_cron_false():
    client = MagicMock()
    wait_for_port = MagicMock()
    cron_writer = MagicMock()

    certbot.request_letsencrypt_certificates(
        ["ubersmith.example.com"],
        UBERSMITH_HOME,
        "admin@example.org",
        "v3.2.0",
        "yes",
        client=client,
        wait_for_port=wait_for_port,
        uid=1000,
        gid=1000,
        install_cron=False,
        cron_writer=cron_writer,
    )

    cron_writer.assert_not_called()


def test_install_renewal_cron_task_writes_expected_entry():
    writer = MagicMock()

    certbot.install_renewal_cron_task(
        UBERSMITH_HOME, reader=lambda: "", writer=writer
    )

    written = writer.call_args[0][0]
    assert certbot.RENEWAL_CRON_MARKER in written
    assert f"@daily {UBERSMITH_HOME}/ubersmith_certbot_renew.sh" in written


def test_install_renewal_cron_task_replaces_previous_entry_idempotently():
    existing = (
        "0 3 * * * /some/other/job\n"
        f"{certbot.RENEWAL_CRON_MARKER}\n"
        "@daily /old/path/ubersmith_certbot_renew.sh\n"
    )
    writer = MagicMock()

    certbot.install_renewal_cron_task(
        UBERSMITH_HOME, reader=lambda: existing, writer=writer
    )

    written = writer.call_args[0][0]
    # The unrelated job survives untouched.
    assert "0 3 * * * /some/other/job" in written
    # The old managed entry is gone, replaced by exactly one new one.
    assert written.count(certbot.RENEWAL_CRON_MARKER) == 1
    assert "/old/path/ubersmith_certbot_renew.sh" not in written
    assert f"@daily {UBERSMITH_HOME}/ubersmith_certbot_renew.sh" in written


def test_run_certbot_request_uses_docker_sdk_shape():
    client = MagicMock()

    certbot.run_certbot_request(
        "ubersmith.example.com",
        UBERSMITH_HOME,
        "admin@example.org",
        "v3.2.0",
        client=client,
        uid=1000,
        gid=1000,
    )

    client.containers.run.assert_called_once_with(
        image="ghcr.io/teamubersmith/certbot:v3.2.0",
        command=(
            "certonly -n -d ubersmith.example.com --standalone --agree-tos "
            "-m admin@example.org"
        ),
        name="certbot",
        user="1000:1000",
        ports={"80/tcp": 80},
        remove=True,
        volumes=_expected_cert_volumes(),
    )


def test_run_certbot_deploy_hook_uses_docker_sdk_shape():
    client = MagicMock()

    certbot.run_certbot_deploy_hook(
        "ubersmith.example.com",
        UBERSMITH_HOME,
        "v3.2.0",
        client=client,
        uid=1000,
        gid=1000,
    )

    client.containers.run.assert_called_once_with(
        image="ghcr.io/teamubersmith/certbot:v3.2.0",
        entrypoint="/etc/letsencrypt/renewal-hooks/deploy/ubersmith-deploy.sh",
        name="certbot",
        user="1000:1000",
        remove=True,
        environment={"RENEWED_DOMAINS": "ubersmith.example.com"},
        volumes=_expected_deploy_volumes(),
    )
