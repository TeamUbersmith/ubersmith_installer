"""Tests for ubersmith_installer.templates.

These exercise the plain-Jinja2 rendering layer (not Ansible) against the
three templates copied into ubersmith_installer/templates/.
"""

import yaml

from ubersmith_installer import templates


def _docker_compose_context(**overrides):
    """A representative context providing every variable
    docker-compose.yml.j2 references."""
    context = {
        "registry": "ghcr.io/teamubersmith",
        "ubersmith_version": "5.2.2",
        "containers_release_version": "r3",
        "container_domain": "ubersmith.example.com",
        "ubersmith_home": "/opt/ubersmith",
        "ubersmith_major_version": "5",
        "ubersmith_release": {
            "5": {
                "mysql_version": 84,
                "php_version": 84,
                "backup_version": 84,
                "ubersmith_release_version": "5.2.2",
                "containers": {
                    "release_version": "r3",
                    "web_container_repo": "ubersmith",
                    "haproxy_version": "1.7-alpine",
                },
            },
        },
        "mozilla_ciphers": {
            "configurations": {
                "intermediate": {
                    "ciphers": {
                        "openssl": [
                            "ECDHE-RSA-AES128-GCM-SHA256",
                            "ECDHE-ECDSA-AES128-GCM-SHA256",
                            "ECDHE-ECDSA-AES256-GCM-SHA384",
                            "ECDHE-RSA-AES256-GCM-SHA384",
                        ],
                    },
                },
            },
        },
        "pmm_version": 2,
        "certbot_version": "v3.2.0",
        # Only referenced inside commented-out "# command: ..." lines in the
        # template, but Jinja still evaluates {{ }} blocks there.
        "virtual_hosts": ["ubersmith.example.com"],
        "notify_email": "admin@example.com",
        # Explicit ansible_os_family so the test is deterministic regardless
        # of the host running the test suite.
        "ansible_os_family": "Debian",
    }
    context.update(overrides)
    return context


def _ubersmith_ini_context(**overrides):
    context = {
        "php_gc_maxlifetime": 86400,
        "php_memory_limit": "512M",
        "php_default_socket_timeout": 6000,
        "php_max_input_time": 6000,
        "php_max_input_vars": 2000,
        "php_max_execution_time": 3600,
        "php_upload_max_filesize": "16M",
        "php_post_max_size": "50M",
    }
    context.update(overrides)
    return context


def test_render_docker_compose_is_valid_yaml_with_expected_top_level_keys():
    rendered = templates.render_docker_compose(_docker_compose_context())

    parsed = yaml.safe_load(rendered)

    assert isinstance(parsed, dict)
    assert "services" in parsed
    assert "volumes" in parsed
    assert isinstance(parsed["services"], dict)
    assert isinstance(parsed["volumes"], dict)
    # Sanity check a couple of expected services made it through templating.
    assert "web" in parsed["services"]
    assert "db" in parsed["services"]


def test_render_docker_compose_omits_journald_logging_on_darwin():
    rendered = templates.render_docker_compose(
        _docker_compose_context(ansible_os_family="Darwin")
    )
    parsed = yaml.safe_load(rendered)

    assert "logging" not in parsed["services"]["web"]


def test_render_docker_compose_includes_journald_logging_off_darwin():
    rendered = templates.render_docker_compose(
        _docker_compose_context(ansible_os_family="Debian")
    )
    parsed = yaml.safe_load(rendered)

    assert parsed["services"]["web"]["logging"]["driver"] == "journald"


def test_get_os_family_returns_nonempty_string():
    # Just exercises the real host-detection path (no context override).
    family = templates.get_os_family()
    assert isinstance(family, str)
    assert family


def test_render_docker_compose_auto_supplies_os_family_when_absent():
    context = _docker_compose_context()
    del context["ansible_os_family"]
    # Should not raise even though ansible_os_family was not supplied.
    rendered = templates.render_docker_compose(context)
    assert "services:" in rendered


def test_render_dot_env_does_not_raise_undefined_error():
    rendered = templates.render_dot_env({})
    assert "MAINTENANCE" in rendered


def test_render_ubersmith_ini_does_not_raise_undefined_error():
    rendered = templates.render_ubersmith_ini(_ubersmith_ini_context())
    assert "session.gc_maxlifetime = 86400" in rendered
    assert "memory_limit = 512M" in rendered
