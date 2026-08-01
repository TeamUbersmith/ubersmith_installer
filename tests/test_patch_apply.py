"""Tests for ubersmith_installer.patch_apply.

These exercise the standalone patch_ubersmith.yml port with a mocked HTTP
getter and mocked docker/subprocess runners -- no real network or Docker
daemon access is required.
"""

import io
import tarfile
import zipfile
from unittest.mock import MagicMock

from ubersmith_installer import patch_apply, state


def _fake_response(json_data=None, content=b""):
    response = MagicMock()
    response.json.return_value = json_data
    response.content = content
    response.raise_for_status.return_value = None
    return response


def test_check_patches_supported_true_when_mount_present(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()
    (ubersmith_home / "docker-compose.override.yml").write_text(
        "services:\n"
        "  web:\n"
        "    volumes:\n"
        "      - ./app/patches:/var/www/ubersmith_root/app/patches\n",
        encoding="utf-8",
    )

    assert patch_apply.check_patches_supported(ubersmith_home) is True


def test_check_patches_supported_false_when_mount_absent(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()
    (ubersmith_home / "docker-compose.override.yml").write_text(
        "services:\n  web:\n    ports:\n      - '443:443'\n",
        encoding="utf-8",
    )

    assert patch_apply.check_patches_supported(ubersmith_home) is False


def test_check_patches_supported_false_when_override_file_missing(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()

    assert patch_apply.check_patches_supported(ubersmith_home) is False


def test_cleanup_previous_patch_state_removes_marker_and_dir(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()
    patched_file = ubersmith_home / ".patched"
    patched_file.write_text("stale patch data")
    patches_dir = ubersmith_home / "app" / "patches"
    patches_dir.mkdir(parents=True)
    (patches_dir / "leftover.txt").write_text("leftover")

    patch_apply.cleanup_previous_patch_state(ubersmith_home)

    assert not patched_file.exists()
    assert not patches_dir.exists()


def test_cleanup_previous_patch_state_is_idempotent_when_nothing_present(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()

    # Should not raise even though nothing exists to remove.
    patch_apply.cleanup_previous_patch_state(ubersmith_home)


def test_list_available_patches_filters_by_version_substring():
    releases = [
        {
            "id": 111,
            "name": "Patch for 5.2.2 - hotfix 1",
            "html_url": "https://github.com/x/1",
            "assets": [{"browser_download_url": "https://example.com/1.tar.gz"}],
        },
        {
            "id": 222,
            "name": "Patch for 5.2.1",
            "html_url": "https://github.com/x/2",
            "assets": [{"browser_download_url": "https://example.com/2.tar.gz"}],
        },
        {
            "id": 333,
            "name": "Patch for 5.2.2 - hotfix 2",
            "html_url": "https://github.com/x/3",
            "assets": [],
        },
    ]
    http_get = MagicMock(return_value=_fake_response(json_data=releases))

    result = patch_apply.list_available_patches("5.2.2", http_get=http_get)

    http_get.assert_called_once_with(
        patch_apply.RELEASES_URL,
        headers={"Accept": patch_apply.GITHUB_ACCEPT_HEADER},
    )
    assert [p["id"] for p in result] == [111, 333]
    assert result[0]["name"] == "Patch for 5.2.2 - hotfix 1"
    assert result[0]["html_url"] == "https://github.com/x/1"
    assert result[0]["asset_url"] == "https://example.com/1.tar.gz"
    # Release with no assets still matches, but asset_url is None.
    assert result[1]["asset_url"] is None


def test_list_available_patches_no_matches_returns_empty_list():
    releases = [
        {
            "id": 1,
            "name": "Patch for 4.6.4",
            "html_url": "https://github.com/x/1",
            "assets": [{"browser_download_url": "https://example.com/1.tar.gz"}],
        }
    ]
    http_get = MagicMock(return_value=_fake_response(json_data=releases))

    result = patch_apply.list_available_patches("5.2.2", http_get=http_get)

    assert result == []


def test_download_and_unpack_patch_tar_gz(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()

    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as archive:
        file_content = b"patch contents"
        info = tarfile.TarInfo(name="fix.php")
        info.size = len(file_content)
        archive.addfile(info, io.BytesIO(file_content))
    tar_bytes = tar_buffer.getvalue()

    http_get = MagicMock(return_value=_fake_response(content=tar_bytes))

    result = patch_apply.download_and_unpack_patch(
        "999",
        "https://example.com/releases/download/patch.tar.gz",
        ubersmith_home,
        http_get=http_get,
    )

    assert result == ["fix.php"]
    extracted = ubersmith_home / "app" / "patches" / "999" / "fix.php"
    assert extracted.read_bytes() == b"patch contents"
    # The downloaded archive itself should be cleaned up.
    assert not (ubersmith_home / "app" / "patches" / "999" / "patch.tar.gz").exists()


def test_download_and_unpack_patch_zip(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as archive:
        archive.writestr("fix.php", "patch contents")
    zip_bytes = zip_buffer.getvalue()

    http_get = MagicMock(return_value=_fake_response(content=zip_bytes))

    result = patch_apply.download_and_unpack_patch(
        "1000",
        "https://example.com/releases/download/patch.zip",
        ubersmith_home,
        http_get=http_get,
    )

    assert result == ["fix.php"]
    extracted = ubersmith_home / "app" / "patches" / "1000" / "fix.php"
    assert extracted.read_text() == "patch contents"


def test_apply_patch_restarts_web_and_runs_expected_exec_commands(tmp_path):
    ubersmith_home = tmp_path / "ubersmith"
    ubersmith_home.mkdir()

    runner = MagicMock()
    client = MagicMock()
    container = MagicMock()
    client.containers.get.return_value = container

    patch_apply.apply_patch(ubersmith_home, "42", client=client, runner=runner)

    runner.assert_called_once_with(
        ["docker", "compose", "restart", "web"], cwd=ubersmith_home
    )
    client.containers.get.assert_called_once_with(patch_apply.WEB_CONTAINER_NAME)
    assert container.exec_run.call_count == 2
    chown_call, copy_call = container.exec_run.call_args_list
    assert "chown -R ubersmith:ubersmith *" in chown_call.args[0]
    assert "app/patches" in chown_call.args[0]
    assert "app/patches/42/" in copy_call.args[0]
    assert "cp -a --suffix .bak --backup . ../../www/" in copy_call.args[0]


def test_record_patch_metadata_writes_new_section(tmp_path):
    patched_path = tmp_path / ".patched"

    patch_apply.record_patch_metadata(
        tmp_path,
        "42",
        installer="mstyne",
        github_page="https://github.com/TeamUbersmith/ubersmith-patches/releases/42",
        install_date="Sat, 01 Aug 2026 00:00:00 +0000",
    )

    parser = state.read_raw(patched_path)
    assert parser.has_section("Patch 42")
    assert parser.get("Patch 42", "installer") == "mstyne"
    assert parser.get("Patch 42", "install_date") == "Sat, 01 Aug 2026 00:00:00 +0000"
    assert parser.get("Patch 42", "github_page") == (
        "https://github.com/TeamUbersmith/ubersmith-patches/releases/42"
    )


def test_record_patch_metadata_preserves_existing_sections_and_prior_patches(tmp_path):
    patched_path = tmp_path / ".patched"
    patched_path.write_text(
        "[Patch 1]\n"
        "installer = admin\n"
        "install_date = Fri, 01 Aug 2025 00:00:00 +0000\n"
        "github_page = https://github.com/TeamUbersmith/ubersmith-patches/releases/1\n"
        "\n"
        "[Some Other Section]\n"
        "custom_key = custom_value\n",
        encoding="utf-8",
    )

    patch_apply.record_patch_metadata(
        tmp_path,
        "2",
        installer="mstyne",
        github_page="https://github.com/TeamUbersmith/ubersmith-patches/releases/2",
        install_date="Sat, 01 Aug 2026 00:00:00 +0000",
    )

    parser = state.read_raw(patched_path)
    # Prior patch section untouched.
    assert parser.has_section("Patch 1")
    assert parser.get("Patch 1", "installer") == "admin"
    # Unrelated section untouched.
    assert parser.has_section("Some Other Section")
    assert parser.get("Some Other Section", "custom_key") == "custom_value"
    # New section written correctly.
    assert parser.has_section("Patch 2")
    assert parser.get("Patch 2", "installer") == "mstyne"
    assert parser.get("Patch 2", "install_date") == "Sat, 01 Aug 2026 00:00:00 +0000"
