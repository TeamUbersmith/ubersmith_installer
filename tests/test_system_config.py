import subprocess
import warnings

from ubersmith_installer import system_config


def test_set_journald_retention_replaces_commented_line(tmp_path):
    conf = tmp_path / "journald.conf"
    conf.write_text("[Journal]\n#MaxRetentionSec=\nStorage=persistent\n")

    system_config.set_journald_retention(conf)

    text = conf.read_text()
    assert "MaxRetentionSec=1year" in text
    assert "#MaxRetentionSec=" not in text
    assert "Storage=persistent" in text


def test_set_journald_retention_appends_if_absent(tmp_path):
    conf = tmp_path / "journald.conf"
    conf.write_text("[Journal]\nStorage=persistent\n")

    system_config.set_journald_retention(conf)

    assert "MaxRetentionSec=1year" in conf.read_text()


def test_set_journald_retention_leaves_explicit_value_alone(tmp_path):
    conf = tmp_path / "journald.conf"
    conf.write_text("[Journal]\nMaxRetentionSec=2weeks\n")

    system_config.set_journald_retention(conf)

    text = conf.read_text()
    assert "MaxRetentionSec=2weeks" in text
    assert "1year" not in text


def test_set_journald_retention_missing_file_warns_not_raises(tmp_path):
    missing = tmp_path / "does-not-exist" / "journald.conf"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        system_config.set_journald_retention(missing)

    assert any("journal retention" in str(w.message) for w in caught)


def test_restart_systemd_journald_invokes_expected_command():
    calls = []

    def fake_runner(cmd):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    system_config.restart_systemd_journald(runner=fake_runner)

    assert calls == [["systemctl", "restart", "systemd-journald"]]


def test_restart_systemd_journald_missing_systemctl_warns_not_raises():
    def fake_runner(cmd):
        raise FileNotFoundError("systemctl not found")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        system_config.restart_systemd_journald(runner=fake_runner)

    assert any("systemd-journald" in str(w.message) for w in caught)
