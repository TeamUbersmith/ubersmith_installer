"""Tests for the interactive prompt layer in ubersmith_installer.prompts."""

import click
import pytest
from click.testing import CliRunner

from ubersmith_installer.prompts import (
    is_lets_encrypt_requested,
    prompt_for_install_values,
    show_compose_override_reminder,
    show_pre_upgrade_reminder,
)


@click.command()
def _prompt_cmd():
    answers = prompt_for_install_values()
    for name in (
        "ubersmith_major_version",
        "ubersmith_home",
        "lets_encrypt_certificate",
        "virtual_host",
        "admin_email",
    ):
        click.echo(f"{name}={answers[name]}")


def test_prompt_defaults_when_just_pressing_enter():
    runner = CliRunner()
    # Five blank lines == pressing enter for each of the five prompts.
    result = runner.invoke(_prompt_cmd, input="\n\n\n\n\n")

    assert result.exit_code == 0
    assert "ubersmith_major_version=5" in result.output
    assert "ubersmith_home=/usr/local/ubersmith" in result.output
    assert "lets_encrypt_certificate=yes" in result.output
    assert "virtual_host=ubersmith.example.com" in result.output
    assert "admin_email=admin@example.org" in result.output


def test_prompt_with_explicit_answers():
    runner = CliRunner()
    answers_in = [
        "4",
        "/opt/ubersmith",
        "no",
        "host1.example.com,host2.example.com",
        "admin@ubersmith.com",
    ]
    result = runner.invoke(_prompt_cmd, input="\n".join(answers_in) + "\n")

    assert result.exit_code == 0
    assert "ubersmith_major_version=4" in result.output
    assert "ubersmith_home=/opt/ubersmith" in result.output
    assert "lets_encrypt_certificate=no" in result.output
    assert (
        "virtual_host=host1.example.com,host2.example.com" in result.output
    )
    assert "admin_email=admin@ubersmith.com" in result.output


def test_prompt_defaults_can_be_overridden():
    runner = CliRunner()

    @click.command()
    def _cmd():
        answers = prompt_for_install_values(
            defaults={
                "ubersmith_major_version": "4",
                "admin_email": "prior@example.com",
            }
        )
        click.echo(f"ubersmith_major_version={answers['ubersmith_major_version']}")
        click.echo(f"admin_email={answers['admin_email']}")
        click.echo(f"ubersmith_home={answers['ubersmith_home']}")

    result = runner.invoke(_cmd, input="\n\n\n\n\n")

    assert result.exit_code == 0
    assert "ubersmith_major_version=4" in result.output
    assert "admin_email=prior@example.com" in result.output
    # Un-overridden default is untouched.
    assert "ubersmith_home=/usr/local/ubersmith" in result.output


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("yes", True),
        ("Y", True),
        ("y", True),
        ("YES", True),
        ("  YES  ", True),
        ("  y  ", True),
        ("no", False),
        ("n", False),
        ("", False),
        ("garbage", False),
        (None, False),
    ],
)
def test_is_lets_encrypt_requested(answer, expected):
    assert is_lets_encrypt_requested(answer) is expected


def test_pre_upgrade_reminder_blocks_and_confirms_when_interactive(monkeypatch):
    calls = []

    def fake_confirm(text, default=None, abort=None):
        calls.append((text, default, abort))
        return True

    monkeypatch.setattr(click, "confirm", fake_confirm)

    show_pre_upgrade_reminder(interactive=True)

    assert len(calls) == 1


def test_pre_upgrade_reminder_never_confirms_when_non_interactive(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(click, "confirm", lambda *a, **k: calls.append(1) or True)

    show_pre_upgrade_reminder(interactive=False)

    assert calls == []
    out = capsys.readouterr().out
    assert "release notes" in out
    assert "backup" in out.lower()


@pytest.mark.parametrize("interactive", [True, False])
def test_compose_override_reminder_noop_for_old_versions(monkeypatch, capsys, interactive):
    calls = []
    monkeypatch.setattr(click, "confirm", lambda *a, **k: calls.append(1) or True)

    for version in ("4.6.0", "4.5.9", "1.0.0"):
        show_compose_override_reminder(interactive=interactive, installed_version=version)

    assert calls == []
    assert capsys.readouterr().out == ""


def test_compose_override_reminder_blocks_and_confirms_when_interactive(monkeypatch):
    calls = []

    def fake_confirm(text, default=None, abort=None):
        calls.append((text, default, abort))
        return True

    monkeypatch.setattr(click, "confirm", fake_confirm)

    show_compose_override_reminder(interactive=True, installed_version="5.0.0")

    assert len(calls) == 1


def test_compose_override_reminder_never_confirms_when_non_interactive(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(click, "confirm", lambda *a, **k: calls.append(1) or True)

    show_compose_override_reminder(interactive=False, installed_version="5.0.0")

    assert calls == []
    out = capsys.readouterr().out
    assert "ports directive" in out
    assert "timezone volume" in out
