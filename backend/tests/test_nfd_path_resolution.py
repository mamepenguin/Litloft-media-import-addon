"""SPEC-ADDON-062 (I15): loft and caption files whose on-disk name is NFD are found and used.

The stored path (``files.file_path``, a subscription's ``folder_path``) is NFC; a file
copied onto a Linux drive from iOS keeps an NFD name. Every test builds the NFD name on
disk itself and skips when the file system folds the two spellings (APFS, HFS+), because
a folding file system cannot tell the cases apart and a pass there would prove nothing.
"""
from __future__ import annotations

import os
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from addons.media_import.subscription.registry import (
    REF_KIND_CHANNEL,
    ItemHeader,
    ItemMetadata,
    SubscriptionRef,
    TranscriptResult,
    _reset_subscription_registry_for_tests,
    register_subscription_provider,
)
from addons.media_import.tests.test_subscription_manager import _FakeProvider

LOFT_MIME = "application/vnd.litloft.loft+json"
VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n"
TITLE = "ガイド動画"
FOLDER = "ダウンロード"


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
    assert nfd(TITLE) != nfc(TITLE)


def _names(path: Path) -> list[str]:
    return sorted(os.listdir(path))


def _nfd_folder_with_loft(drive: Path) -> Path:
    """``<NFD folder>/<NFD title>.loft``; returns the loft's real path."""
    folder = drive / nfd(FOLDER)
    folder.mkdir()
    loft = folder / (nfd(TITLE) + ".loft")
    loft.write_text('{"url": "https://example.com/v"}', encoding="utf-8")
    return loft


def _seed_loft_row(session, *, file_id="fnfd0000001", folder=FOLDER) -> str:
    from app.models import File

    db = session()
    try:
        db.add(File(
            id=file_id,
            filename=nfc(TITLE) + ".loft",
            title=nfc(TITLE),
            drive="drv",
            folder_path=nfc(folder),
            file_path=nfc(folder + "/" + TITLE + ".loft") if folder else nfc(TITLE + ".loft"),
            file_size=2,
            file_type="other",
            mime_type=LOFT_MIME,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ))
        db.commit()
    finally:
        db.close()
    return file_id


# --- _resolve_drive_file_path -------------------------------------------------


@pytest.mark.parametrize("on_disk", [
    pytest.param("leaf", id="nfd-file-name"),
    pytest.param("ancestor", id="nfd-folder"),
    pytest.param("both", id="nfd-folder-and-file-name"),
])
def test_spec_addon_062_resolve_drive_file_path_finds_the_nfd_loft(
    drive_path, on_disk
):
    from addons.media_import.service import _resolve_drive_file_path

    folder = nfd(FOLDER) if on_disk in ("ancestor", "both") else nfc(FOLDER)
    leaf = nfd(TITLE) if on_disk in ("leaf", "both") else nfc(TITLE)
    (drive_path / folder).mkdir()
    real = drive_path / folder / (leaf + ".loft")
    real.write_text('{"url": "https://example.com/v"}', encoding="utf-8")

    resolved = _resolve_drive_file_path("drv", nfc(FOLDER + "/" + TITLE + ".loft"))

    assert resolved.exists()
    assert os.fsencode(resolved.name) == os.fsencode(real.name)
    assert resolved.read_text(encoding="utf-8") == real.read_text(encoding="utf-8")


def test_spec_addon_062_resolve_drive_file_path_keeps_the_stored_path_when_nothing_matches(
    drive_path,
):
    from addons.media_import.service import _resolve_drive_file_path

    resolved = _resolve_drive_file_path("drv", nfc(FOLDER + "/none.loft"))

    assert not resolved.exists()
    assert resolved == (drive_path / nfc(FOLDER) / "none.loft").resolve()


def test_spec_addon_062_resolve_drive_file_path_still_rejects_escape(drive_path):
    from addons.media_import.service import _resolve_drive_file_path

    with pytest.raises(ValueError):
        _resolve_drive_file_path("drv", "../outside.loft")


def test_spec_addon_062_resolve_drive_file_path_rejects_a_symlink_below_an_nfd_folder(
    drive_path, tmp_path
):
    from addons.media_import.service import _resolve_drive_file_path

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.loft").write_text("{}", encoding="utf-8")
    (drive_path / nfd(FOLDER)).mkdir()
    (drive_path / nfd(FOLDER) / "link").symlink_to(outside)

    with pytest.raises(ValueError):
        _resolve_drive_file_path("drv", nfc(FOLDER + "/link/x.loft"))


# --- captions, STT temp -------------------------------------------------------


class _CaptionYDL:
    def __init__(self, opts, *_a, **_kw):
        self.outtmpl = opts["outtmpl"]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, _urls):
        Path(self.outtmpl.replace("%(ext)s", "ja.vtt")).write_text(VTT, encoding="utf-8")
        return 0


def test_spec_addon_062_caption_download_lands_beside_an_nfd_loft(
    media_import_db, drive_path
):
    from addons.media_import.service import LoftFetchItem, LoftManager

    loft = _nfd_folder_with_loft(drive_path)
    file_id = _seed_loft_row(media_import_db)
    meta = {
        "title": TITLE, "duration": 10, "description": "d", "channel": "c",
        "published_at": "20260101", "language": "ja", "thumbnail_url": None,
        "has_captions": True,
    }

    with patch("addons.media_import.service._fetch_metadata_sync", return_value=meta), \
            patch("addons.media_import.service._save_loft_thumbnail"), \
            patch("yt_dlp.YoutubeDL", _CaptionYDL):
        LoftManager()._fetch_and_update(
            LoftFetchItem(file_id=file_id, url="https://example.com/v", drive="drv")
        )

    assert _names(drive_path) == [nfd(FOLDER)]
    assert _names(loft.parent) == sorted([loft.name, nfd(TITLE) + ".vtt"])
    db = media_import_db()
    try:
        ok = db.execute(
            text("SELECT captions_downloaded FROM loft_metadata WHERE file_id = :f"),
            {"f": file_id},
        ).scalar()
    finally:
        db.close()
    assert bool(ok) is True


def test_spec_addon_062_stt_audio_is_downloaded_beside_an_nfd_loft_and_nothing_is_created_elsewhere(
    media_import_db, drive_path
):
    from addons.media_import.service import LoftFetchItem, LoftManager

    loft = _nfd_folder_with_loft(drive_path)
    file_id = _seed_loft_row(media_import_db)
    seen: list[Path] = []

    def _fake_download(_url, output_stem):
        seen.append(output_stem)
        assert output_stem.parent.is_dir()
        produced = output_stem.parent / f"{output_stem.name}.stt_temp.m4a"
        produced.write_bytes(b"audio")
        return produced

    with patch("addons.media_import.service._download_stt_audio_sync", _fake_download):
        LoftManager()._download_stt_and_notify(
            LoftFetchItem(file_id=file_id, url="https://example.com/v", drive="drv")
        )

    assert len(seen) == 1
    assert os.fsencode(seen[0].parent.name) == os.fsencode(nfd(FOLDER))
    assert _names(drive_path) == [nfd(FOLDER)]
    assert nfd(TITLE) + ".stt_temp.m4a" in _names(loft.parent)


def test_spec_addon_062_stt_failure_cleans_the_temp_beside_an_nfd_loft(
    media_import_db, drive_path
):
    from addons.media_import.service import LoftFetchItem, LoftManager

    loft = _nfd_folder_with_loft(drive_path)
    file_id = _seed_loft_row(media_import_db)

    def _fake_download(_url, output_stem):
        (output_stem.parent / f"{output_stem.name}.stt_temp.webm.part").write_bytes(b"x")
        raise RuntimeError("download failed")

    with patch("addons.media_import.service._download_stt_audio_sync", _fake_download), \
            pytest.raises(RuntimeError):
        LoftManager()._download_stt_and_notify(
            LoftFetchItem(file_id=file_id, url="https://example.com/v", drive="drv")
        )

    assert _names(drive_path) == [nfd(FOLDER)]
    assert _names(loft.parent) == [loft.name]


def test_spec_addon_062_stale_stt_cleanup_reaches_an_nfd_folder(media_import_db, drive_path):
    from addons.media_import.service import _cleanup_stale_stt_temp_files

    loft = _nfd_folder_with_loft(drive_path)
    old = loft.parent / (nfd(TITLE) + ".stt_temp.m4a")
    old.write_bytes(b"audio")
    os.utime(old, (946684800, 946684800))
    _seed_loft_row(media_import_db)

    assert _cleanup_stale_stt_temp_files() == 1
    assert _names(loft.parent) == [loft.name]


# --- router folder check ------------------------------------------------------


def test_spec_addon_062_folder_check_accepts_an_nfd_folder(drive_path):
    from addons.media_import.router import _validate_folder_path

    (drive_path / nfd(FOLDER)).mkdir()

    _validate_folder_path(nfc(FOLDER), "drv")


def test_spec_addon_062_folder_check_rejects_an_nfd_folder_that_is_a_symlink_out_of_the_drive(
    drive_path, tmp_path
):
    from addons.media_import.router import _validate_folder_path

    outside = tmp_path / "outside"
    outside.mkdir()
    (drive_path / nfd(FOLDER)).symlink_to(outside)

    with pytest.raises(HTTPException) as exc:
        _validate_folder_path(nfc(FOLDER), "drv")

    assert exc.value.status_code == 400


# --- subscription loft creation ----------------------------------------------


@pytest.fixture()
def fake_provider():
    def _resolve(url: str) -> SubscriptionRef | None:
        if "fake/channel/" in url:
            return SubscriptionRef(kind=REF_KIND_CHANNEL, ref=url.rsplit("/", 1)[-1])
        return None

    _reset_subscription_registry_for_tests()
    p = _FakeProvider(resolve_url=_resolve)
    register_subscription_provider(p)
    yield p
    _reset_subscription_registry_for_tests()


def _run_subscription(fake_provider, folder: str, title: str = TITLE) -> None:
    from addons.media_import.subscription.manager import SubscriptionManager

    fake_provider.headers = [ItemHeader(item_id="v1", title=title)]
    fake_provider.items = {
        "v1": ItemMetadata(
            item_id="v1", canonical_url="https://fake/v/v1", title=title,
            has_captions=True, language="ja",
        ),
    }
    fake_provider.transcripts = {"v1": TranscriptResult(vtt_text=VTT, language="ja")}
    mgr = SubscriptionManager()
    sub_id = mgr.create(
        url="https://fake/channel/UCabcdefghijklmnopqrstuv", drive="d", folder_path=folder
    )
    mgr._sync_blocking(sub_id)


@pytest.fixture()
def _providers():
    from app.services import provider_registry
    from addons.media_import.provider_registration import register_media_import_providers

    provider_registry._reset_for_tests()
    register_media_import_providers()
    yield
    provider_registry._reset_for_tests()


@pytest.mark.parametrize("folder", [
    pytest.param(nfc(FOLDER), id="one-level"),
    pytest.param(nfc("親フォルダ/" + FOLDER), id="nested-under-nfd-ancestor"),
])
def test_spec_addon_062_subscription_creates_loft_inside_the_nfd_folder_not_an_nfc_twin(
    media_import_db, drive_path, fake_provider, _providers, folder
):
    on_disk = drive_path
    for part in folder.split("/"):
        on_disk = on_disk / nfd(part)
    on_disk.mkdir(parents=True)

    _run_subscription(fake_provider, folder)

    top = nfd(folder.split("/")[0])
    assert _names(drive_path) == [top]
    assert _names(on_disk) == sorted([nfc(TITLE) + ".loft", nfc(TITLE) + ".vtt"])


def test_spec_addon_062_subscription_loft_name_collision_sees_an_nfd_loft(
    media_import_db, drive_path, fake_provider, _providers
):
    folder = drive_path / nfd(FOLDER)
    folder.mkdir()
    (folder / (nfd(TITLE) + ".loft")).write_text("{}", encoding="utf-8")

    _run_subscription(fake_provider, nfc(FOLDER))

    assert _names(drive_path) == [nfd(FOLDER)]
    lofts = [n for n in _names(folder) if n.endswith(".loft")]
    assert len(lofts) == 2
    assert nfd(TITLE) + ".loft" in lofts
    assert nfc(TITLE) + " (1).loft" in lofts
    assert (folder / (nfd(TITLE) + ".loft")).read_text(encoding="utf-8") == "{}"
