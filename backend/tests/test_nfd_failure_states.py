"""SPEC-ADDON-062: failure and ambiguity behaviour of the NFD path resolution in media_import."""
from __future__ import annotations

import os
import unicodedata
from unittest.mock import patch

import pytest

TITLE = "ガイド動画"
FOLDER = "ダウンロード"
VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n"


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def nfd(s: str) -> str:
    return unicodedata.normalize("NFD", s)


@pytest.fixture(autouse=True)
def _needs_a_file_system_that_keeps_nfd(tmp_path):
    probe = tmp_path / ".nfd_probe"
    probe.mkdir()
    (probe / nfd(TITLE)).write_bytes(b"")
    if (probe / nfc(TITLE)).exists():
        pytest.skip("this file system folds NFC and NFD; run on Linux (ext4/overlayfs)")


def _ambiguous_folders(drive):
    """Two spellings of FOLDER, neither equal to the stored NFC one."""
    full, mixed = nfd(FOLDER), nfd(FOLDER[0]) + FOLDER[1:]
    assert len({full, mixed, nfc(FOLDER)}) == 3
    (drive / full).mkdir()
    (drive / mixed).mkdir()


def test_spec_addon_062_listing_failure_leaves_the_stored_path_and_does_not_raise(drive_path):
    from addons.media_import.service import _resolve_drive_file_path

    (drive_path / nfd(FOLDER)).mkdir()
    with patch("app.services.diskpath.os.scandir", side_effect=PermissionError("denied")):
        resolved = _resolve_drive_file_path("drv", nfc(FOLDER + "/x.loft"))

    assert resolved == (drive_path / nfc(FOLDER) / "x.loft").resolve()


def test_spec_addon_062_read_of_an_ambiguous_folder_picks_the_first_by_bytes(drive_path):
    from addons.media_import.service import _resolve_drive_file_path

    _ambiguous_folders(drive_path)
    resolved = _resolve_drive_file_path("drv", nfc(FOLDER + "/x.loft"))

    first = sorted([nfd(FOLDER), nfd(FOLDER[0]) + FOLDER[1:]], key=os.fsencode)[0]
    assert resolved.parent.name == first


def test_spec_addon_062_create_loft_in_an_ambiguous_folder_fails_and_creates_nothing(
    media_import_db, drive_path
):
    from addons.media_import.service import LoftManager

    _ambiguous_folders(drive_path)
    before = sorted(os.listdir(drive_path))

    with patch("addons.media_import.service._extract_title_sync", return_value=TITLE):
        with pytest.raises(ValueError):
            LoftManager().create_loft_sync("https://example.com/v", "drv", nfc(FOLDER))

    assert sorted(os.listdir(drive_path)) == before


def test_spec_addon_062_allocate_in_an_ambiguous_folder_fails_and_creates_nothing(drive_path):
    from addons.media_import.subscription.manager import _allocate_loft_path

    _ambiguous_folders(drive_path)
    before = sorted(os.listdir(drive_path))

    with pytest.raises(ValueError):
        _allocate_loft_path(drive_path, nfc(FOLDER), TITLE)

    assert sorted(os.listdir(drive_path)) == before


def test_spec_addon_062_create_loft_takes_a_suffix_when_an_nfd_loft_exists(
    media_import_db, drive_path
):
    from addons.media_import.service import LoftManager

    folder = drive_path / nfd(FOLDER)
    folder.mkdir()
    (folder / (nfd(TITLE) + ".loft")).write_text("{}", encoding="utf-8")

    with patch("addons.media_import.service._extract_title_sync", return_value=TITLE):
        _, filename = LoftManager().create_loft_sync("https://example.com/v", "drv", nfc(FOLDER))

    assert filename == nfc(TITLE) + " (1).loft"
    assert os.listdir(drive_path) == [nfd(FOLDER)]
    assert (folder / (nfd(TITLE) + ".loft")).read_text(encoding="utf-8") == "{}"


def test_spec_addon_062_create_loft_at_the_drive_root_is_unchanged(media_import_db, drive_path):
    from addons.media_import.service import LoftManager

    with patch("addons.media_import.service._extract_title_sync", return_value=TITLE):
        _, filename = LoftManager().create_loft_sync("https://example.com/v", "drv", "")

    assert os.listdir(drive_path) == [filename]


def test_spec_addon_062_save_vtt_replaces_the_existing_nfd_vtt_and_adds_no_twin(drive_path):
    from addons.media_import.subscription.manager import _save_vtt

    folder = drive_path / nfd(FOLDER)
    folder.mkdir()
    existing = folder / (nfd(TITLE) + ".vtt")
    existing.write_text("old", encoding="utf-8")

    _save_vtt(folder / (nfc(TITLE) + ".loft"), VTT)

    assert os.listdir(folder) == [nfd(TITLE) + ".vtt"]
    assert existing.read_text(encoding="utf-8") == VTT


def test_spec_addon_062_folder_check_with_a_missing_folder_passes_unchanged(drive_path):
    from addons.media_import.router import _validate_folder_path

    _validate_folder_path(nfc(FOLDER + "/新規"), "drv")
