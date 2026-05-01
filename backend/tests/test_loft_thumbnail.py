"""Tests for ``_save_loft_thumbnail`` — shared helper that writes a
.loft's remote thumbnail to ``data/thumbnails/`` and updates
``File.thumbnail_path``.

Both ``LoftManager._fetch_and_update`` (the /link pipeline) and
``SubscriptionManager._import_one_item`` (the subscription pipeline)
go through this helper so the two paths produce identical thumbnail
state per file_id (see hako ``IpF19kUI3OKoY_ps7iKg1`` / ``rSexxNohzBFCSwvD7oQPI``).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text


def _seed_file(db_session, *, file_id: str, drive: str, folder_path: str,
               filename: str) -> None:
    """Insert a minimal File row so the helper can update its thumbnail_path."""
    db = db_session()
    try:
        rel_path = (
            f"{folder_path}/{filename}" if folder_path else filename
        )
        db.execute(
            text(
                "INSERT INTO files (id, filename, title, description, drive, "
                " folder_path, file_path, file_size, file_type, mime_type, "
                " created_at, updated_at) "
                "VALUES (:id, :filename, :title, '', :drive, "
                " :folder_path, :file_path, 0, 'other', "
                " 'application/vnd.litloft.loft+json', "
                " '2026-05-01T00:00:00', '2026-05-01T00:00:00')"
            ),
            {
                "id": file_id,
                "filename": filename,
                "title": filename,
                "drive": drive,
                "folder_path": folder_path,
                "file_path": rel_path,
            },
        )
        db.commit()
    finally:
        db.close()


def _read_thumbnail_path(db_session, file_id: str) -> str | None:
    db = db_session()
    try:
        row = db.execute(
            text("SELECT thumbnail_path FROM files WHERE id = :id"),
            {"id": file_id},
        ).first()
    finally:
        db.close()
    return row[0] if row else None


class TestSaveLoftThumbnailSuccess:
    def test_writes_thumbnail_path_with_folder(
        self, media_import_db, drive_path, monkeypatch
    ) -> None:
        from addons.media_import import service

        _seed_file(
            media_import_db,
            file_id="f1", drive="d", folder_path="yt",
            filename="Sample.loft",
        )

        captured: dict = {}

        def _fake_download(url: str, dest: Path) -> bool:
            captured["url"] = url
            captured["dest"] = dest
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake jpg")
            return True

        monkeypatch.setattr(service, "_download_thumbnail_sync", _fake_download)

        rel = service._save_loft_thumbnail(
            file_id="f1",
            drive="d",
            folder_path="yt",
            filename="Sample.loft",
            thumbnail_url="https://i.ytimg.com/vi/abc/hqdefault.jpg",
        )

        assert rel == "d/yt/Sample.jpg"
        assert _read_thumbnail_path(media_import_db, "f1") == "d/yt/Sample.jpg"
        assert captured["url"] == "https://i.ytimg.com/vi/abc/hqdefault.jpg"

    def test_writes_thumbnail_path_without_folder(
        self, media_import_db, drive_path, monkeypatch
    ) -> None:
        from addons.media_import import service

        _seed_file(
            media_import_db,
            file_id="f2", drive="d", folder_path="",
            filename="Top.loft",
        )

        monkeypatch.setattr(
            service, "_download_thumbnail_sync",
            lambda url, dest: True,
        )

        rel = service._save_loft_thumbnail(
            file_id="f2",
            drive="d",
            folder_path="",
            filename="Top.loft",
            thumbnail_url="https://example/x.jpg",
        )

        assert rel == "d/Top.jpg"
        assert _read_thumbnail_path(media_import_db, "f2") == "d/Top.jpg"


class TestSaveLoftThumbnailNoOp:
    def test_returns_none_when_thumbnail_url_is_none(
        self, media_import_db, drive_path, monkeypatch
    ) -> None:
        from addons.media_import import service

        _seed_file(
            media_import_db, file_id="f3", drive="d",
            folder_path="", filename="X.loft",
        )

        called: list = []
        monkeypatch.setattr(
            service, "_download_thumbnail_sync",
            lambda *a, **kw: called.append(True) or True,
        )

        rel = service._save_loft_thumbnail(
            file_id="f3", drive="d", folder_path="",
            filename="X.loft", thumbnail_url=None,
        )

        assert rel is None
        assert called == []  # no download attempted
        assert _read_thumbnail_path(media_import_db, "f3") is None

    def test_returns_none_when_thumbnail_url_is_empty(
        self, media_import_db, drive_path, monkeypatch
    ) -> None:
        from addons.media_import import service

        _seed_file(
            media_import_db, file_id="f4", drive="d",
            folder_path="", filename="Y.loft",
        )
        monkeypatch.setattr(
            service, "_download_thumbnail_sync",
            lambda *a, **kw: True,
        )

        rel = service._save_loft_thumbnail(
            file_id="f4", drive="d", folder_path="",
            filename="Y.loft", thumbnail_url="",
        )

        assert rel is None
        assert _read_thumbnail_path(media_import_db, "f4") is None


class TestSaveLoftThumbnailFailures:
    def test_download_failure_leaves_thumbnail_path_unchanged(
        self, media_import_db, drive_path, monkeypatch
    ) -> None:
        from addons.media_import import service

        _seed_file(
            media_import_db, file_id="f5", drive="d",
            folder_path="yt", filename="Z.loft",
        )
        monkeypatch.setattr(
            service, "_download_thumbnail_sync",
            lambda url, dest: False,
        )

        rel = service._save_loft_thumbnail(
            file_id="f5", drive="d", folder_path="yt",
            filename="Z.loft", thumbnail_url="https://example/z.jpg",
        )

        assert rel is None
        assert _read_thumbnail_path(media_import_db, "f5") is None

    def test_missing_file_id_silently_skipped(
        self, media_import_db, drive_path, monkeypatch
    ) -> None:
        from addons.media_import import service

        # Don't seed any file row. Pretend download succeeded so we
        # exercise the file_record None branch.
        monkeypatch.setattr(
            service, "_download_thumbnail_sync",
            lambda url, dest: True,
        )

        rel = service._save_loft_thumbnail(
            file_id="ghost", drive="d", folder_path="yt",
            filename="Ghost.loft",
            thumbnail_url="https://example/ghost.jpg",
        )

        assert rel is None  # file_record lookup returns None


class TestSaveLoftThumbnailNFCNormalization:
    def test_nfd_filename_is_normalized_to_nfc_in_thumb_rel(
        self, media_import_db, drive_path, monkeypatch
    ) -> None:
        """macOS / NFD-input filenames must produce NFC thumb_rel so the
        path matches what scanner / file detail endpoints expect.
        """
        from addons.media_import import service

        # NFD form: "が" = "か" + dakuten combining mark
        nfd_filename = "がmeta.loft"  # "がmeta.loft" NFD
        _seed_file(
            media_import_db, file_id="f6", drive="d",
            folder_path="", filename=nfd_filename,
        )

        monkeypatch.setattr(
            service, "_download_thumbnail_sync",
            lambda url, dest: True,
        )

        rel = service._save_loft_thumbnail(
            file_id="f6", drive="d", folder_path="",
            filename=nfd_filename, thumbnail_url="https://example/g.jpg",
        )

        # Expected stem in NFC form: "が" = single codepoint U+304C
        assert rel == "d/がmeta.jpg"
