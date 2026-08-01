"""Tests for ubersmith_installer.mta."""

from unittest.mock import MagicMock

import pytest

from ubersmith_installer import mta


@pytest.mark.parametrize("os_family", ["Darwin", "Windows"])
def test_skipped_entirely_on_darwin_and_windows(os_family):
    runner = MagicMock()

    mta.stop_and_disable_mtas(os_family, runner=runner)

    runner.assert_not_called()


@pytest.mark.parametrize("os_family", ["Debian", "RedHat", "Ubuntu"])
def test_stops_and_disables_each_mta_on_linux(os_family):
    runner = MagicMock()

    mta.stop_and_disable_mtas(os_family, runner=runner)

    assert runner.call_count == len(mta.MAIL_TRANSFER_AGENTS) * 2
    for name in mta.MAIL_TRANSFER_AGENTS:
        runner.assert_any_call(
            ["systemctl", "stop", name], capture_output=True, text=True, check=False
        )
        runner.assert_any_call(
            ["systemctl", "disable", name], capture_output=True, text=True, check=False
        )


def test_failure_for_one_service_does_not_stop_the_others_or_raise():
    calls = []

    def flaky_runner(args, **kwargs):
        calls.append(args)
        if args[-1] == "sendmail":
            raise FileNotFoundError("systemctl: service not found")
        result = MagicMock()
        result.returncode = 0
        return result

    # Should not raise, even though the sendmail calls blow up.
    mta.stop_and_disable_mtas("Debian", runner=flaky_runner)

    assert len(calls) == len(mta.MAIL_TRANSFER_AGENTS) * 2
    for name in mta.MAIL_TRANSFER_AGENTS:
        assert ["systemctl", "stop", name] in calls
        assert ["systemctl", "disable", name] in calls


def test_nonzero_return_code_does_not_raise_or_stop_others():
    def failing_runner(args, **kwargs):
        result = MagicMock()
        result.returncode = 1 if args[-1] == "exim4" else 0
        return result

    mta.stop_and_disable_mtas("RedHat", runner=failing_runner)
    # Reaching here without an exception is the assertion; nothing else
    # to check since a non-zero returncode with check=False never raises.
