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


def _docker_compose_override_context(**overrides):
    context = {
        "ubersmith_home": "/opt/ubersmith",
        "mysql_root_password": "root-secret",
        "mysql_ubersmith_password": "ubersmith-secret",
        "php_version": "8.4",
        "container_domain": "ubersmith.example.com",
        "ansible_os_family": "Debian",
        "timezone_file": {"stat": {"lnk_source": "/usr/share/zoneinfo/UTC"}},
    }
    context.update(overrides)
    return context


def _instance_vhost_context(**overrides):
    context = {
        "admin_email": "admin@example.com",
        "ubersmith_root": "/var/www/ubersmith_root",
        "item": "ubersmith.example.com",
        "fcgi_host": "php",
        "mozilla_ciphers": {
            "configurations": {
                "intermediate": {
                    "ciphers": {
                        "openssl": [
                            "ECDHE-RSA-AES128-GCM-SHA256",
                            "ECDHE-ECDSA-AES128-GCM-SHA256",
                        ],
                    },
                },
            },
        },
    }
    context.update(overrides)
    return context


def test_render_rwhois_does_not_raise_undefined_error():
    rendered = templates.render_rwhois(
        {
            "ubersmith_root": "/var/www/ubersmith_root",
            "main_virtual_host": "ubersmith.example.com",
        }
    )
    assert "server = /var/www/ubersmith_root/app/www/rwhois.php" in rendered
    assert "server_args = ubersmith.example.com" in rendered


def test_render_docker_compose_override_is_valid_yaml_on_linux():
    rendered = templates.render_docker_compose_override(
        _docker_compose_override_context(ansible_os_family="Debian")
    )
    parsed = yaml.safe_load(rendered)
    assert "/usr/share/zoneinfo/UTC:/etc/localtime" in rendered
    assert parsed["services"]["db"]["environment"]["MYSQL_ROOT_PASSWORD"] == (
        "root-secret"
    )


def test_render_docker_compose_override_omits_timezone_on_darwin():
    context = _docker_compose_override_context(ansible_os_family="Darwin")
    del context["timezone_file"]
    rendered = templates.render_docker_compose_override(context)
    assert "/etc/localtime" not in rendered


def test_render_docker_compose_override_auto_supplies_facts_when_absent():
    context = _docker_compose_override_context()
    del context["ansible_os_family"]
    del context["timezone_file"]
    # Should not raise even though ansible_os_family/timezone_file were not
    # supplied -- they get computed from the real host.
    rendered = templates.render_docker_compose_override(context)
    assert "services:" in rendered


def test_render_mysql_cnf_major_version_4_uses_memfree():
    rendered = templates.render_mysql_cnf(
        "4", {"ansible_memfree_mb": 8000}
    )
    assert "innodb_buffer_pool_size        = 2400M" in rendered


def test_render_mysql_cnf_major_version_5_uses_memtotal():
    rendered = templates.render_mysql_cnf(
        "5", {"ansible_memtotal_mb": 8000}
    )
    assert "innodb_buffer_pool_size        = 2400M" in rendered


def test_render_mysql_cnf_auto_supplies_memory_facts_when_absent():
    # Should not raise even without ansible_memfree_mb/ansible_memtotal_mb
    # in the context -- computed automatically from the real host.
    assert "innodb_buffer_pool_size" in templates.render_mysql_cnf("4", {})
    assert "innodb_buffer_pool_size" in templates.render_mysql_cnf("5", {})


def test_render_mysql_cnf_rejects_unsupported_major_version():
    import pytest

    with pytest.raises(ValueError):
        templates.render_mysql_cnf("6", {})


def test_render_mysql_extra_cnf_does_not_raise_undefined_error():
    rendered = templates.render_mysql_extra_cnf({})
    assert "mysql_native_password" in rendered


def test_render_instance_vhost_does_not_raise_undefined_error():
    rendered = templates.render_instance_vhost(_instance_vhost_context())
    assert "ServerName ubersmith.example.com" in rendered
    assert "ServerAdmin admin@example.com" in rendered


def test_render_postfix_deploy_hook_does_not_raise_undefined_error():
    rendered = templates.render_postfix_deploy_hook({})
    assert "RENEWED_DOMAINS" in rendered


def test_render_ubersmith_deploy_hook_does_not_raise_undefined_error():
    rendered = templates.render_ubersmith_deploy_hook({})
    assert "RENEWED_DOMAINS" in rendered


def test_render_certbot_renew_script_does_not_raise_undefined_error():
    rendered = templates.render_certbot_renew_script(
        {"ubersmith_home": "/opt/ubersmith"}
    )
    assert "cd /opt/ubersmith" in rendered


def test_get_memtotal_mb_returns_positive_int():
    memtotal = templates.get_memtotal_mb()
    assert isinstance(memtotal, int)
    assert memtotal > 0


def test_get_memfree_mb_returns_positive_int():
    memfree = templates.get_memfree_mb()
    assert isinstance(memfree, int)
    assert memfree > 0


def test_get_memfree_mb_does_not_exceed_memtotal_by_much():
    # MemAvailable/MemFree should never wildly exceed MemTotal; sanity check
    # rather than a strict invariant given the two are read independently.
    assert templates.get_memfree_mb() <= templates.get_memtotal_mb() * 1.1


def test_get_timezone_file_returns_real_looking_path():
    tz_file = templates.get_timezone_file()
    assert isinstance(tz_file, str)
    assert tz_file.startswith("/")
