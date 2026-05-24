"""Tests for ``LoftManager.create_loft_sync``.

Covers:
- ``.loft`` JSON contents (provider/url) match the registered detector
- ``register_single_file`` writes a File row tied to the new .loft
- Re-creating the same URL revives a soft-deleted/missing record without
  inserting a duplicate
- Filename collisions are resolved with a counter suffix
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from addons.media_import.service import LoftManager


@pytest.fixture(autouse=True)
def _register_providers():
    """Each test runs against a clean registry with media_import providers."""
    from app.services import provider_registry
    from addons.media_import.provider_registration import (
        register_media_import_providers,
    )

    provider_registry._reset_for_tests()
    register_media_import_providers()
    yield
    provider_registry._reset_for_tests()


def _stub_title(_url: str) -> str:
    return "My Video"


class TestCreateLoftSync:
    def test_writes_loft_json_with_provider_and_url(
        self, media_import_db, drive_path
    ) -> None:
        manager = LoftManager()
        with patch(
            "addons.media_import.service._extract_title_sync",
            side_effect=_stub_title,
        ):
            file_id, filename = manager.create_loft_sync(
                "https://www.youtube.com/watch?v=abc", "drv", ""
            )

        assert filename == "My Video.loft"
        loft_file = drive_path / filename
        assert loft_file.exists()
        body = json.loads(loft_file.read_text(encoding="utf-8"))
        assert body == {
            "provider": "youtube",
            "url": "https://www.youtube.com/watch?v=abc",
        }
        assert isinstance(file_id, str) and file_id

    def test_registers_file_in_db(self, media_import_db, drive_path) -> None:
        manager = LoftManager()
        with patch(
            "addons.media_import.service._extract_title_sync",
            side_effect=_stub_title,
        ):
            file_id, _ = manager.create_loft_sync(
                "https://vimeo.com/12345", "drv", ""
            )

        db = media_import_db()
        try:
            from app.models import File

            row = db.query(File).filter(File.id == file_id).first()
        finally:
            db.close()

        assert row is not None
        assert row.drive == "drv"
        assert row.deleted_at is None
        assert row.missing_since is None

    def test_collision_appends_counter(self, media_import_db, drive_path) -> None:
        manager = LoftManager()
        # Pre-create the first .loft so the next call collides.
        (drive_path / "My Video.loft").write_text("{}", encoding="utf-8")

        with patch(
            "addons.media_import.service._extract_title_sync",
            side_effect=_stub_title,
        ):
            _, filename = manager.create_loft_sync(
                "https://www.youtube.com/watch?v=abc", "drv", ""
            )

        assert filename == "My Video (1).loft"
        assert (drive_path / "My Video (1).loft").exists()

    def test_re_create_revives_soft_deleted_record(
        self, media_import_db, drive_path
    ) -> None:
        from datetime import UTC, datetime

        manager = LoftManager()
        with patch(
            "addons.media_import.service._extract_title_sync",
            side_effect=_stub_title,
        ):
            first_id, first_name = manager.create_loft_sync(
                "https://vimeo.com/123", "drv", ""
            )

        # Soft-delete the DB record AND remove the on-disk .loft to mimic
        # the production "trashed and FS-removed" state. Without removing
        # the .loft, the collision counter would generate a new path and
        # bypass the revival branch.
        db = media_import_db()
        try:
            from app.models import File

            row = db.query(File).filter(File.id == first_id).first()
            assert row is not None
            row.deleted_at = datetime.now(UTC)
            db.commit()
        finally:
            db.close()
        (drive_path / first_name).unlink()

        # Recreating with the same target path now revives the existing
        # record rather than inserting a duplicate.
        with patch(
            "addons.media_import.service._extract_title_sync",
            side_effect=_stub_title,
        ):
            second_id, second_name = manager.create_loft_sync(
                "https://vimeo.com/123", "drv", ""
            )

        assert second_id == first_id
        assert second_name == first_name

        db = media_import_db()
        try:
            from app.models import File

            row = db.query(File).filter(File.id == first_id).first()
        finally:
            db.close()
        assert row is not None
        assert row.deleted_at is None
        assert row.missing_since is None


class TestSttMode:
    def test_missing_captions_only_runs_when_provider_reports_none(self) -> None:
        from addons.media_import.service import _should_run_stt

        assert _should_run_stt(
            "missing_captions",
            has_captions=False,
            captions_downloaded=False,
        )
        assert not _should_run_stt(
            "missing_captions",
            has_captions=True,
            captions_downloaded=False,
        )

    def test_always_runs_even_when_captions_downloaded(self) -> None:
        from addons.media_import.service import _should_run_stt

        assert _should_run_stt(
            "always",
            has_captions=True,
            captions_downloaded=True,
        )

    def test_cleanup_stt_temp_removes_part_and_final_files(
        self, tmp_path: Path
    ) -> None:
        from addons.media_import.service import _cleanup_stt_temp

        stem = tmp_path / "Video"
        final = tmp_path / "Video.stt_temp.m4a"
        part = tmp_path / "Video.stt_temp.webm.part"
        other = tmp_path / "Video.vtt"
        final.write_bytes(b"a")
        part.write_bytes(b"b")
        other.write_text("WEBVTT", encoding="utf-8")

        _cleanup_stt_temp(stem)

        assert not final.exists()
        assert not part.exists()
        assert other.exists()

    def test_stale_cleanup_scans_registered_loft_refs_only(
        self, media_import_db, drive_path
    ) -> None:
        from datetime import UTC, datetime

        from app.models import File
        from addons.media_import.service import _cleanup_stale_stt_temp_files

        loft = drive_path / "Video.loft"
        loft.write_text("{}", encoding="utf-8")
        old_temp = drive_path / "Video.stt_temp.m4a"
        old_temp.write_bytes(b"audio")
        fresh_temp = drive_path / "Other.stt_temp.m4a"
        fresh_temp.write_bytes(b"audio")
        old_ts = 946684800
        os.utime(old_temp, (old_ts, old_ts))
        os.utime(fresh_temp, (old_ts, old_ts))

        db = media_import_db()
        try:
            db.add(
                File(
                    id="fsttclean",
                    filename="Video.loft",
                    title="Video",
                    drive="drv",
                    folder_path="",
                    file_path="Video.loft",
                    file_size=1,
                    file_type="other",
                    mime_type="application/vnd.litloft.loft+json",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            db.commit()
        finally:
            db.close()

        assert _cleanup_stale_stt_temp_files() == 1
        assert not old_temp.exists()
        assert fresh_temp.exists()
