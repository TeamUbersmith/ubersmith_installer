"""Tests for ubersmith_installer.retry_letsencrypt.

These exercise the Let's Encrypt certificate *retry* flow with a mocked
docker SDK client -- no real Docker daemon is required.
"""

from pathlib import Path
from unittest.mock import MagicMock, call

from ubersmith_installer import certbot, retry_letsencrypt


UBERSMITH_HOME = Path("/usr/local/ubersmith")


def _expected_webroot_volumes(ubersmith_home=UBERSMITH_HOME):
    return {
        str(ubersmith_home / "conf/certbot/etc"): {"bind": "/etc/letsencrypt", "mode": "rw"},
        str(ubersmith_home / "conf/certbot/lib"): {"bind": "/var/lib/letsencrypt", "mode": "rw"},
        str(ubersmith_home / "conf/certbot/log"): {"bind": "/var/log/letsencrypt", "mode": "rw"},
        "ubersmith_webroot": {"bind": "/var/www/ubersmith_root", "mode": "rw"},
    }


def _expected_deploy_volumes(ubersmith_home=UBERSMITH_HOME):
    volumes = {
        str(ubersmith_home / "conf/certbot/etc"): {"bind": "/etc/letsencrypt", "mode": "rw"},
        str(ubersmith_home / "conf/certbot/lib"): {"bind": "/var/lib/letsencrypt", "mode": "rw"},
        str(ubersmith_home / "conf/certbot/log"): {"bind": "/var/log/letsencrypt", "mode": "rw"},
    }
    volumes[str(ubersmith_home / "conf/ssl")] = {"bind": "/opt/certbot/deploy", "mode": "rw"}
    return volumes


def test_run_certbot_webroot_request_uses_docker_sdk_shape():
    client = MagicMock()

    retry_letsencrypt.run_certbot_webroot_request(
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
            "certonly -vvv -n -d ubersmith.example.com --webroot "
            "--webroot-path /var/www/ubersmith_root/app/www --agree-tos "
            "-m admin@example.org"
        ),
        name="certbot",
        user="1000:1000",
        remove=True,
        volumes=_expected_webroot_volumes(),
    )


def test_run_certbot_webroot_deploy_hook_delegates_to_certbot_module(monkeypatch):
    client = MagicMock()
    delegate = MagicMock()
    monkeypatch.setattr(retry_letsencrypt, "run_certbot_deploy_hook", delegate)

    retry_letsencrypt.run_certbot_webroot_deploy_hook(
        "ubersmith.example.com",
        UBERSMITH_HOME,
        "v3.2.0",
        client=client,
        uid=1000,
        gid=1000,
    )

    delegate.assert_called_once_with(
        "ubersmith.example.com",
        UBERSMITH_HOME,
        "v3.2.0",
        client=client,
        uid=1000,
        gid=1000,
    )


def test_run_certbot_webroot_deploy_hook_uses_docker_sdk_shape():
    # End-to-end (no monkeypatching): confirms the deploy hook is really
    # identical in shape to certbot.run_certbot_deploy_hook.
    client = MagicMock()

    retry_letsencrypt.run_certbot_webroot_deploy_hook(
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


def test_graceful_apache_reload_execs_apachectl_graceful():
    client = MagicMock()
    container = MagicMock()
    client.containers.get.return_value = container

    retry_letsencrypt.graceful_apache_reload(client=client)

    client.containers.get.assert_called_once_with("ubersmith-web-1")
    container.exec_run.assert_called_once_with(
        ["/bin/bash", "-c", "/usr/local/apache2/bin/apachectl graceful"]
    )


def test_retry_letsencrypt_full_flow_multiple_hosts(monkeypatch):
    client = MagicMock()
    container = MagicMock()
    client.containers.get.return_value = container
    install_cron = MagicMock()
    monkeypatch.setattr(certbot, "install_renewal_cron_task", install_cron)

    virtual_hosts = ["ubersmith.example.com", "billing.example.com"]

    retry_letsencrypt.retry_letsencrypt(
        virtual_hosts,
        UBERSMITH_HOME,
        "admin@example.org",
        "v3.2.0",
        client=client,
        uid=1000,
        gid=1000,
    )

    # containers.run called once per host for the cert request, and once
    # per host for the deploy hook: 2 hosts * 2 steps = 4 calls.
    assert client.containers.run.call_count == 4

    expected_image = "ghcr.io/teamubersmith/certbot:v3.2.0"
    webroot_volumes = _expected_webroot_volumes()
    deploy_volumes = _expected_deploy_volumes()

    expected_cert_calls = [
        call(
            image=expected_image,
            command=(
                f"certonly -vvv -n -d {host} --webroot "
                "--webroot-path /var/www/ubersmith_root/app/www --agree-tos "
                "-m admin@example.org"
            ),
            name="certbot",
            user="1000:1000",
            remove=True,
            volumes=webroot_volumes,
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

    # The graceful apache reload happens exactly once, after all the cert
    # requests and deploy hooks.
    client.containers.get.assert_called_once_with("ubersmith-web-1")
    container.exec_run.assert_called_once_with(
        ["/bin/bash", "-c", "/usr/local/apache2/bin/apachectl graceful"]
    )

    # The renewal cron task is (re-)installed once.
    install_cron.assert_called_once_with(UBERSMITH_HOME)


def test_retry_letsencrypt_single_host_loops_once_per_step(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(certbot, "install_renewal_cron_task", MagicMock())

    retry_letsencrypt.retry_letsencrypt(
        ["ubersmith.example.com"],
        UBERSMITH_HOME,
        "admin@example.org",
        "v3.2.0",
        client=client,
        uid=1000,
        gid=1000,
    )

    # One virtual host -> one cert-request call + one deploy-hook call.
    assert client.containers.run.call_count == 2


def test_retry_letsencrypt_no_lets_encrypt_gate_argument(monkeypatch):
    # Unlike certbot.request_letsencrypt_certificates, retry_letsencrypt has
    # no lets_encrypt_certificate gate -- it always runs.
    client = MagicMock()
    install_cron = MagicMock()
    monkeypatch.setattr(certbot, "install_renewal_cron_task", install_cron)

    retry_letsencrypt.retry_letsencrypt(
        [],
        UBERSMITH_HOME,
        "admin@example.org",
        "v3.2.0",
        client=client,
        uid=1000,
        gid=1000,
    )

    # No hosts -> no cert-request/deploy-hook calls, but the graceful
    # reload and cron install still happen unconditionally.
    client.containers.run.assert_not_called()
    client.containers.get.assert_called_once_with("ubersmith-web-1")
