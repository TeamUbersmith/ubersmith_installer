"""Tests for ubersmith_installer.patch_cleanup.

These exercise the legacy ``.patched`` file / ``app/patches`` directory
cleanup logic that mirrors the ``upgrade_only``-tagged, ``when: interactive``
gated tasks in roles/ubersmith/tasks/main.yml -- no real Docker daemon or
network access is required.
"""

from ubersmith_installer import patch_cleanup


def test_non_interactive_does_nothing_even_if_patched_exists(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()
    patched_file = ubersmith_home / ".patched"
    patched_file.write_text("legacy patch marker")

    result = patch_cleanup.cleanup_legacy_patches(
        ubersmith_home, ubersmith_version="5.2.2", interactive=False
    )

    assert result is False
    assert patched_file.exists()
    assert patched_file.read_text() == "legacy patch marker"


def test_interactive_no_patched_file_does_nothing(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()

    result = patch_cleanup.cleanup_legacy_patches(
        ubersmith_home, ubersmith_version="5.2.2", interactive=True
    )

    assert result is False
    assert not (ubersmith_home / ".patched").exists()
    assert not (ubersmith_home / ".patched-pre-5.2.2").exists()


def test_interactive_patched_present_renames_and_removes_patches_dir(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()
    patched_file = ubersmith_home / ".patched"
    patched_file.write_text("legacy patch marker")
    patches_dir = ubersmith_home / "app" / "patches"
    patches_dir.mkdir(parents=True)
    (patches_dir / "some_patch.diff").write_text("diff content")

    result = patch_cleanup.cleanup_legacy_patches(
        ubersmith_home, ubersmith_version="5.2.2", interactive=True
    )

    assert result is True
    assert not patched_file.exists()
    renamed = ubersmith_home / ".patched-pre-5.2.2"
    assert renamed.exists()
    assert renamed.read_text() == "legacy patch marker"
    assert not patches_dir.exists()


def test_interactive_patched_present_no_patches_dir_still_renames(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()
    patched_file = ubersmith_home / ".patched"
    patched_file.write_text("legacy patch marker")

    result = patch_cleanup.cleanup_legacy_patches(
        ubersmith_home, ubersmith_version="5.2.2", interactive=True
    )

    assert result is True
    assert not patched_file.exists()
    renamed = ubersmith_home / ".patched-pre-5.2.2"
    assert renamed.exists()
    assert not (ubersmith_home / "app" / "patches").exists()
