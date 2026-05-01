"""Tests for Loft caption state visibility (spec 2026-04-26).

Covers:
- ``_classify_caption_error`` branch coverage
- ``_download_captions_sync`` returns ``tuple[bool, str | None]``
- ``_fetch_and_update`` writes ``caption_error_kind`` atomically with
  ``captions_downloaded`` and resets the kind on success
- ``_retry_failed_captions`` excludes rows whose kind is ``'permanent'``
- ``LoftMetadataResponse`` exposes the two new fields
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text

from addons.media_import.service import (
    LoftManager,
    _classify_caption_error,
    _download_captions_sync,
)
from addons.media_import.schemas import LoftFetchItem, LoftMetadataResponse


# ---------------------------------------------------------------------------
# Unit tests: _classify_caption_error
# ---------------------------------------------------------------------------


class TestClassifyCaptionError:
    @pytest.mark.parametrize(
        "message",
        [
            "HTTP Error 429: Too Many Requests",
            "HTTP Error 429",
            "ERROR: Too Many Requests",
            "yt-dlp hit a rate-limit",
            "Sign in to confirm you're not a bot",
        ],
    )
    def test_rate_limited_patterns(self, message: str) -> None:
        assert _classify_caption_error(message) == "rate_limited"

    @pytest.mark.parametrize(
        "message",
        [
            "Private video",
            "ERROR: Private video. Sign in if you've been granted access.",
            "Video unavailable",
            "This video has been removed by the uploader",
            "Sign in to confirm your age (age-restricted)",
            "Join this channel to get access to members-only content",
            "Premieres in 3 days",
            "The uploader said this video is not available in your country",
        ],
    )
    def test_permanent_patterns(self, message: str) -> None:
        assert _classify_caption_error(message) == "permanent"

    @pytest.mark.parametrize(
        "message",
        [
            "Connection timed out",
            "DNS resolution failed",
            "",
            "Some unknown failure",
        ],
    )
    def test_unclassified_returns_none(self, message: str) -> None:
        assert _classify_caption_error(message) is None

    def test_none_input_is_handled(self) -> None:
        assert _classify_caption_error("") is None


# ---------------------------------------------------------------------------
# Unit tests: _download_captions_sync return shape
# ---------------------------------------------------------------------------


class TestDownloadCaptionsSyncReturnShape:
    def test_returns_tuple_when_no_vtt_generated(self, tmp_path) -> None:
        """If yt-dlp 'succeeds' but produces no .vtt, return (False, None)."""
        output_stem = tmp_path / "video"

        class _FakeYDL:
            def __init__(self, *_a, **_kw): ...
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def download(self, _urls): return 0

        with patch("yt_dlp.YoutubeDL", _FakeYDL):
            result = _download_captions_sync(
                "https://example.com/v", output_stem, language="en"
            )
        assert isinstance(result, tuple)
        assert len(result) == 2
        ok, kind = result
        assert ok is False
        assert kind is None

    def test_returns_true_when_vtt_exists(self, tmp_path) -> None:
        """Simulate yt-dlp creating a .lang.vtt file. Return (True, None)."""
        output_stem = tmp_path / "video"
        vtt = tmp_path / "video.en.vtt"
        vtt.write_text(
            "WEBVTT\n\n"
            "00:00:00.000 --> 00:00:01.000\nhello\n\n"
            "00:00:01.000 --> 00:00:02.000\nworld\n",
            encoding="utf-8",
        )

        class _FakeYDL:
            def __init__(self, *_a, **_kw): ...
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def download(self, _urls): return 0

        with patch("yt_dlp.YoutubeDL", _FakeYDL):
            result = _download_captions_sync(
                "https://example.com/v", output_stem, language="en"
            )
        ok, kind = result
        assert ok is True
        assert kind is None


# ---------------------------------------------------------------------------
# Integration: _fetch_and_update writes caption_error_kind correctly
# ---------------------------------------------------------------------------


def _seed_file(session, file_id: str = "ftest000001", drive: str = "drv") -> None:
    """Insert a minimal File row so loft_metadata FK passes."""
    from datetime import UTC, datetime

    from app.models import File

    db = session()
    try:
        f = File(
            id=file_id,
            filename=f"{file_id}.loft",
            title=file_id,
            drive=drive,
            folder_path="",
            file_path=f"{file_id}.loft",
            file_size=1,
            file_type="other",
            mime_type="application/vnd.litloft.loft+json",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(f)
        db.commit()
    finally:
        db.close()


class TestFetchAndUpdateCaptionErrorKind:
    def test_has_captions_false_leaves_kind_null(
        self, media_import_db, drive_path
    ) -> None:
        _seed_file(media_import_db)
        manager = LoftManager()
        item = LoftFetchItem(
            file_id="ftest000001",
            url="https://example.com/v",
            drive="drv",
        )

        fake_meta = {
            "title": "T",
            "duration": 10,
            "description": "d",
            "channel": "c",
            "published_at": "20260101",
            "language": "en",
            "thumbnail_url": None,
            "has_captions": False,
        }
        with patch(
            "addons.media_import.service._fetch_metadata_sync",
            return_value=fake_meta,
        ):
            manager._fetch_and_update(item)

        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT captions_downloaded, caption_error_kind "
                    "FROM loft_metadata WHERE file_id = :fid"
                ),
                {"fid": "ftest000001"},
            ).first()
        finally:
            db.close()

        assert row is not None
        assert bool(row[0]) is False
        assert row[1] is None

    def test_dl_failure_records_classified_kind(
        self, media_import_db, drive_path
    ) -> None:
        _seed_file(media_import_db)
        manager = LoftManager()
        item = LoftFetchItem(
            file_id="ftest000001",
            url="https://example.com/v",
            drive="drv",
        )

        fake_meta = {
            "title": "T",
            "duration": 10,
            "description": "d",
            "channel": "c",
            "published_at": "20260101",
            "language": "en",
            "thumbnail_url": None,
            "has_captions": True,
        }

        def _boom(*_a, **_kw):
            raise RuntimeError("HTTP Error 429: Too Many Requests")

        with patch(
            "addons.media_import.service._fetch_metadata_sync",
            return_value=fake_meta,
        ), patch(
            "addons.media_import.service._download_captions_sync",
            side_effect=_boom,
        ):
            manager._fetch_and_update(item)

        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT captions_downloaded, caption_error_kind "
                    "FROM loft_metadata WHERE file_id = :fid"
                ),
                {"fid": "ftest000001"},
            ).first()
        finally:
            db.close()

        assert row is not None
        assert bool(row[0]) is False
        assert row[1] == "rate_limited"

    def test_success_resets_kind_to_null(
        self, media_import_db, drive_path
    ) -> None:
        _seed_file(media_import_db)
        manager = LoftManager()
        item = LoftFetchItem(
            file_id="ftest000001",
            url="https://example.com/v",
            drive="drv",
        )

        fake_meta = {
            "title": "T",
            "duration": 10,
            "description": "d",
            "channel": "c",
            "published_at": "20260101",
            "language": "en",
            "thumbnail_url": None,
            "has_captions": True,
        }

        def _private(*_a, **_kw):
            raise RuntimeError("Private video")

        with patch(
            "addons.media_import.service._fetch_metadata_sync",
            return_value=fake_meta,
        ), patch(
            "addons.media_import.service._download_captions_sync",
            side_effect=_private,
        ):
            manager._fetch_and_update(item)

        db = media_import_db()
        try:
            kind = db.execute(
                text(
                    "SELECT caption_error_kind FROM loft_metadata "
                    "WHERE file_id = :fid"
                ),
                {"fid": "ftest000001"},
            ).scalar()
        finally:
            db.close()
        assert kind == "permanent"

        with patch(
            "addons.media_import.service._fetch_metadata_sync",
            return_value=fake_meta,
        ), patch(
            "addons.media_import.service._download_captions_sync",
            return_value=(True, None),
        ):
            manager._fetch_and_update(item)

        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT captions_downloaded, caption_error_kind "
                    "FROM loft_metadata WHERE file_id = :fid"
                ),
                {"fid": "ftest000001"},
            ).first()
        finally:
            db.close()
        assert bool(row[0]) is True
        assert row[1] is None


# ---------------------------------------------------------------------------
# Integration: _retry_failed_captions excludes 'permanent'
# ---------------------------------------------------------------------------


class TestRetryFailedCaptionsExcludesPermanent:
    def test_permanent_rows_not_retried(
        self, media_import_db, drive_path, monkeypatch
    ) -> None:
        _seed_file(media_import_db, file_id="fperm000001", drive="drv")
        _seed_file(media_import_db, file_id="frate000001", drive="drv")

        db = media_import_db()
        try:
            for fid, kind in (("fperm000001", "permanent"), ("frate000001", "rate_limited")):
                db.execute(
                    text(
                        "INSERT INTO loft_metadata "
                        "(file_id, provider, url, has_captions, "
                        "captions_downloaded, caption_error_kind) "
                        "VALUES (:fid, 'youtube', 'https://x', 1, 0, :k)"
                    ),
                    {"fid": fid, "k": kind},
                )
            db.commit()
        finally:
            db.close()

        manager = LoftManager()
        enqueued: list[str] = []

        async def _capture(file_id: str, url: str, drive: str) -> None:
            enqueued.append(file_id)

        async def _fast_sleep(*_a, **_kw):
            return None

        monkeypatch.setattr(manager, "enqueue_fetch", _capture)
        monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

        async def _run():
            await manager._retry_failed_captions()

        asyncio.run(_run())

        assert "frate000001" in enqueued
        assert "fperm000001" not in enqueued

    def test_null_kind_rows_are_retried(
        self, media_import_db, drive_path, monkeypatch
    ) -> None:
        _seed_file(media_import_db, file_id="fnull000001", drive="drv")
        db = media_import_db()
        try:
            db.execute(
                text(
                    "INSERT INTO loft_metadata "
                    "(file_id, provider, url, has_captions, "
                    "captions_downloaded, caption_error_kind) "
                    "VALUES ('fnull000001', 'youtube', 'https://x', 1, 0, NULL)"
                )
            )
            db.commit()
        finally:
            db.close()

        manager = LoftManager()
        enqueued: list[str] = []

        async def _capture(file_id: str, url: str, drive: str) -> None:
            enqueued.append(file_id)

        async def _fast_sleep(*_a, **_kw):
            return None

        monkeypatch.setattr(manager, "enqueue_fetch", _capture)
        monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

        async def _run():
            await manager._retry_failed_captions()

        asyncio.run(_run())
        assert enqueued == ["fnull000001"]


# ---------------------------------------------------------------------------
# Schema: LoftMetadataResponse exposes the two new fields
# ---------------------------------------------------------------------------


class TestLoftMetadataResponseSchema:
    def test_includes_captions_downloaded_and_caption_error_kind(self) -> None:
        resp = LoftMetadataResponse(
            provider="youtube",
            url="https://example.com/v",
            has_captions=True,
            captions_downloaded=False,
            caption_error_kind="rate_limited",
        )
        dumped = resp.model_dump()
        assert dumped["captions_downloaded"] is False
        assert dumped["caption_error_kind"] == "rate_limited"

    def test_defaults_are_safe(self) -> None:
        resp = LoftMetadataResponse(provider="youtube", url="https://example.com/v")
        dumped = resp.model_dump()
        assert dumped["captions_downloaded"] is False
        assert dumped["caption_error_kind"] is None
