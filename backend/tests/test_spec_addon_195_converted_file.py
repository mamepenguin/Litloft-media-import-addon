"""SPEC-ADDON-195: a file that is no longer a ``.loft``.

A converted file keeps its row, its subscription rows and its ``loft_metadata``,
is listed under its name without the extension, and is never refreshed, fetched
or retried again.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from yt_dlp.utils import DownloadError

from addons.media_import.tests.test_youtube_item_classification import _PREFIX

LOFT_MIME = "application/vnd.litloft.loft+json"
VIDEO_MIME = "video/mp4"
LINK = "/api/addons/media_import/link"
URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
INFO_SKIP = "loft fetch skipped, not a .loft (file {file_id})"


@pytest.fixture(autouse=True)
def _register_providers():
    from app.services import provider_registry
    from addons.media_import.provider_registration import (
        register_media_import_providers,
    )

    provider_registry._reset_for_tests()
    register_media_import_providers()
    yield
    provider_registry._reset_for_tests()


@pytest.fixture()
def client(media_import_db, drive_path):
    from addons.media_import.router import router

    app = FastAPI()
    app.include_router(router)
    c = TestClient(app, raise_server_exceptions=False)
    c.headers["X-Lit-Drive"] = "drv"
    return c


@pytest.fixture()
def enqueued():
    from addons.media_import.router import loft_manager

    captured: list[tuple[str, str, str, str]] = []

    async def _capture(
        file_id: str, url: str, drive: str, stt_mode: str = "manual"
    ) -> None:
        captured.append((file_id, url, drive, stt_mode))

    with patch.object(loft_manager, "enqueue_fetch", _capture):
        yield captured


def _seed_file(
    session,
    drive: Path,
    file_id: str,
    *,
    filename: str = "Title.mp4",
    mime: str = VIDEO_MIME,
    file_type: str = "video",
    content: bytes = b"media",
    duration: float | None = None,
    thumbnail_path: str | None = None,
) -> None:
    from app.models import File

    path = drive / filename
    path.write_bytes(content)
    now = datetime.now(UTC)
    db = session()
    try:
        db.add(
            File(
                id=file_id,
                filename=filename,
                title=filename.rsplit(".", 1)[0],
                drive="drv",
                folder_path="",
                file_path=filename,
                file_size=len(content),
                file_type=file_type,
                mime_type=mime,
                duration=duration,
                thumbnail_path=thumbnail_path,
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    finally:
        db.close()


def _seed_loft(session, drive: Path, file_id: str, *, filename: str = "Title.loft") -> None:
    body = json.dumps({"provider": "youtube", "url": URL})
    _seed_file(
        session,
        drive,
        file_id,
        filename=filename,
        mime=LOFT_MIME,
        file_type="other",
        content=body.encode("utf-8"),
    )


def _seed_meta(
    session,
    file_id: str,
    *,
    provider_item_id: str | None = "dQw4w9WgXcQ",
    has_captions: int = 1,
    downloaded: int = 0,
    kind: str | None = None,
    description: str = "old",
) -> None:
    db = session()
    try:
        db.execute(
            text(
                "INSERT INTO loft_metadata "
                "(file_id, provider, provider_item_id, url, channel, description, "
                " has_captions, captions_downloaded, caption_error_kind) "
                "VALUES (:f, 'youtube', :iid, :u, 'c', :d, :h, :dl, :k)"
            ),
            {
                "f": file_id,
                "iid": provider_item_id,
                "u": URL,
                "d": description,
                "h": has_captions,
                "dl": downloaded,
                "k": kind,
            },
        )
        db.commit()
    finally:
        db.close()


def _convert(session, file_id: str, *, filename: str = "Title.mp4") -> None:
    db = session()
    try:
        db.execute(
            text(
                "UPDATE files SET filename = :n, file_path = :n, "
                "file_type = 'video', mime_type = :m, "
                "file_size = 5, duration = NULL WHERE id = :f"
            ),
            {"n": filename, "m": VIDEO_MIME, "f": file_id},
        )
        db.commit()
    finally:
        db.close()


def _row(session, file_id: str) -> dict:
    db = session()
    try:
        return dict(
            db.execute(
                text("SELECT * FROM files WHERE id = :f"), {"f": file_id}
            ).mappings().one()
        )
    finally:
        db.close()


def _meta_row(session, file_id: str) -> dict | None:
    db = session()
    try:
        row = db.execute(
            text("SELECT * FROM loft_metadata WHERE file_id = :f"), {"f": file_id}
        ).mappings().first()
        return dict(row) if row else None
    finally:
        db.close()


def _chapter_count(session, file_id: str) -> int:
    db = session()
    try:
        return db.execute(
            text("SELECT COUNT(*) FROM file_chapters WHERE file_id = :f"),
            {"f": file_id},
        ).scalar_one()
    finally:
        db.close()


def _files_rows(session) -> int:
    db = session()
    try:
        return db.execute(text("SELECT COUNT(*) FROM files")).scalar_one()
    finally:
        db.close()


def _fetch_meta() -> dict:
    return {
        "duration": 99,
        "chapters": [
            {"start_time": 0.0, "end_time": 10.0, "title": "Intro"},
        ],
        "thumbnail_url": "https://img.example/thumb.jpg",
        "description": "new",
        "channel": "new channel",
        "published_at": "2026-01-01",
        "has_captions": True,
    }


def _run_fetch(file_id: str, *, stt_mode: str = "manual"):
    from addons.media_import.service import LoftFetchItem, LoftManager

    return LoftManager()._fetch_and_update(
        LoftFetchItem(file_id=file_id, url=URL, drive="drv", stt_mode=stt_mode)
    )


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


class TestRefresh:
    def test_spec_addon_195_i16_refresh_of_a_converted_file_answers_404_and_queues_nothing(
        self, client, media_import_db, drive_path, enqueued,
    ) -> None:
        """I16: an active file that is not a .loft, even with a metadata row, is 404."""
        _seed_file(media_import_db, drive_path, "fconverted01")
        _seed_meta(media_import_db, "fconverted01")

        res = client.post(f"{LINK}/fconverted01/refresh")

        assert res.status_code == 404, res.text
        assert res.json() == {"detail": "File not found"}
        assert enqueued == []

    def test_spec_addon_195_i16_a_loft_is_still_queued_as_before(
        self, client, media_import_db, drive_path, enqueued,
    ) -> None:
        """I16: the same route still queues a file that is still a .loft."""
        _seed_loft(media_import_db, drive_path, "fstillloft01")
        _seed_meta(media_import_db, "fstillloft01")

        res = client.post(f"{LINK}/fstillloft01/refresh")

        assert res.status_code == 200, res.text
        assert res.json() == {"status": "queued"}
        assert [item[0] for item in enqueued] == ["fstillloft01"]


# ---------------------------------------------------------------------------
# The fetch
# ---------------------------------------------------------------------------


class TestFetch:
    def test_spec_addon_195_i17_an_absent_row_makes_no_lookup(
        self, media_import_db, drive_path, caplog,
    ) -> None:
        """I17: no row means no yt-dlp call and one INFO line."""
        lookup = MagicMock(return_value=None)

        with caplog.at_level(logging.INFO), patch(
            "addons.media_import.service._fetch_metadata_sync", lookup,
        ):
            _run_fetch("fabsent00001")

        lookup.assert_not_called()
        messages = [r.getMessage() for r in caplog.records]
        assert messages.count(INFO_SKIP.format(file_id="fabsent00001")) == 1

    def test_spec_addon_195_i17_a_converted_row_makes_no_lookup_and_writes_nothing(
        self, media_import_db, drive_path, caplog, thumbnails_dir,
    ) -> None:
        """I17: a media row gets no lookup, no duration, chapters, thumbnail or metadata."""
        from app.services.thumbnail import thumbnail_slot_rel

        _seed_file(
            media_import_db, drive_path, "falready0001",
            duration=42.0, content=b"already media",
        )
        _seed_meta(media_import_db, "falready0001", description="old")
        slot = thumbnails_dir / thumbnail_slot_rel("falready0001")
        slot.parent.mkdir(parents=True, exist_ok=True)
        slot.write_bytes(b"KEPT")
        before_folder = sorted(os.listdir(drive_path))
        lookup = MagicMock(return_value=_fetch_meta())

        with caplog.at_level(logging.INFO), patch(
            "addons.media_import.service._fetch_metadata_sync", lookup,
        ):
            _run_fetch("falready0001")

        lookup.assert_not_called()
        row = _row(media_import_db, "falready0001")
        assert row["duration"] == 42.0
        assert row["thumbnail_path"] is None
        assert slot.read_bytes() == b"KEPT"
        assert _chapter_count(media_import_db, "falready0001") == 0
        assert _meta_row(media_import_db, "falready0001")["description"] == "old"
        assert sorted(os.listdir(drive_path)) == before_folder
        messages = [r.getMessage() for r in caplog.records]
        assert messages.count(INFO_SKIP.format(file_id="falready0001")) == 1

    def test_spec_addon_195_i17_a_row_converted_during_the_lookup_writes_nothing(
        self, media_import_db, drive_path, caplog, thumbnails_dir,
    ) -> None:
        """I17: a lookup that succeeds after the conversion writes no field of the row."""
        from app.services.thumbnail import thumbnail_slot_rel

        _seed_loft(media_import_db, drive_path, "fmidflight01")
        _seed_meta(media_import_db, "fmidflight01", description="old")
        slot = thumbnails_dir / thumbnail_slot_rel("fmidflight01")
        slot.parent.mkdir(parents=True, exist_ok=True)
        slot.write_bytes(b"KEPT")
        before_row = _row(media_import_db, "fmidflight01")
        before_folder = sorted(os.listdir(drive_path))
        downloaded_thumbnails: list[str] = []
        downloaded_audio: list[str] = []
        caption_downloads: list[str] = []
        emitted: list[tuple[str, dict]] = []

        def _lookup(_url):
            _convert(media_import_db, "fmidflight01")
            return _fetch_meta()

        def _thumbnail(url, dest):
            downloaded_thumbnails.append(url)
            Path(dest).write_bytes(b"NEW")
            return True

        def _stt(url, output_stem):
            downloaded_audio.append(url)
            return Path(output_stem).with_suffix(".stt_temp.m4a")

        def _captions(url, output_stem, language=None):
            caption_downloads.append(url)
            return True, None

        def _emit(name, payload, **_kwargs):
            emitted.append((name, payload))

        with caplog.at_level(logging.INFO), patch(
            "addons.media_import.service._fetch_metadata_sync", _lookup,
        ), patch(
            "addons.media_import.service._download_thumbnail_sync", _thumbnail,
        ), patch(
            "addons.media_import.service._download_stt_audio_sync", _stt,
        ), patch(
            "addons.media_import.service._download_captions_sync", _captions,
        ), patch(
            "addons.media_import.service.event_hooks.emit_sync", _emit,
        ):
            _run_fetch("fmidflight01", stt_mode="always")

        after_row = _row(media_import_db, "fmidflight01")
        assert after_row["duration"] == before_row["duration"]
        assert after_row["thumbnail_path"] is None
        assert slot.read_bytes() == b"KEPT"
        assert _chapter_count(media_import_db, "fmidflight01") == 0
        assert _meta_row(media_import_db, "fmidflight01")["description"] == "old"
        assert sorted(os.listdir(drive_path)) == before_folder
        assert downloaded_thumbnails == []
        assert downloaded_audio == []
        assert caption_downloads == []
        assert emitted == []
        messages = [r.getMessage() for r in caplog.records]
        assert messages.count(INFO_SKIP.format(file_id="fmidflight01")) == 1

    def test_spec_addon_195_i17_a_lookup_that_raises_keeps_the_permanent_caption_mark(
        self, media_import_db, drive_path,
    ) -> None:
        """I17's exception path: SPEC-ADDON-165 I6 writes caption_error_kind anyway."""
        _seed_loft(media_import_db, drive_path, "ferrored0001")
        _seed_meta(media_import_db, "ferrored0001")

        def _lookup(_url):
            _convert(media_import_db, "ferrored0001")
            raise DownloadError(_PREFIX + "Private video. Sign in if you've been granted access")

        with patch(
            "addons.media_import.service._fetch_metadata_sync", _lookup,
        ), pytest.raises(DownloadError):
            _run_fetch("ferrored0001")

        assert _meta_row(media_import_db, "ferrored0001")["caption_error_kind"] == "permanent"


# ---------------------------------------------------------------------------
# The startup caption retry
# ---------------------------------------------------------------------------


def _retried(session) -> list[str]:
    from addons.media_import.service import LoftManager

    manager = LoftManager()
    enqueued: list[str] = []

    async def _capture(file_id, url, drive, *_a, **_kw):
        enqueued.append(file_id)

    async def _no_sleep(*_a, **_kw):
        return None

    with patch.object(manager, "enqueue_fetch", _capture), patch.object(
        asyncio, "sleep", _no_sleep
    ):
        asyncio.run(manager._retry_failed_captions())
    return enqueued


class TestStartupCaptionRetry:
    def test_spec_addon_195_i18_only_rows_still_a_loft_are_queued(
        self, media_import_db, drive_path,
    ) -> None:
        """I18: a converted row with a pending caption is not queued."""
        _seed_loft(media_import_db, drive_path, "fretryloft01")
        _seed_file(media_import_db, drive_path, "fretrymedia1")
        _seed_meta(media_import_db, "fretryloft01", has_captions=1, downloaded=0)
        _seed_meta(media_import_db, "fretrymedia1", has_captions=1, downloaded=0)

        assert _retried(media_import_db) == ["fretryloft01"]


# ---------------------------------------------------------------------------
# The listed title
# ---------------------------------------------------------------------------


_TITLES = [
    pytest.param("Title.loft", "Title", id="loft"),
    pytest.param("v1.2 talk.loft", "v1.2 talk", id="loft-with-dots"),
    pytest.param("a.b.c.loft", "a.b.c", id="loft-many-dots"),
    pytest.param(".loft", "", id="loft-alone"),
    pytest.param(".hidden.loft", ".hidden", id="loft-leading-dot"),
    pytest.param("Title.mp4", "Title", id="media"),
    pytest.param("v1.2 talk.mp4", "v1.2 talk", id="media-with-dots"),
    pytest.param("notes.txt", "notes", id="other-extension"),
    pytest.param("Title", "Title", id="no-extension"),
    pytest.param("", None, id="empty"),
    pytest.param(None, None, id="none"),
]


@pytest.mark.parametrize(("filename", "expected"), _TITLES)
def test_spec_addon_195_i19_the_title_is_the_name_without_its_last_extension(
    filename, expected,
) -> None:
    """I19: last extension stripped, a .loft as before, no dot whole."""
    from addons.media_import.router import _title_from_filename

    assert _title_from_filename(filename) == expected


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def test_spec_addon_195_i20_a_subscription_import_reuses_a_converted_file(
    media_import_db, drive_path,
) -> None:
    """I20: the converted row's id is reused and no new .loft is written."""
    from addons.media_import.subscription.manager import SubscriptionManager
    from addons.media_import.subscription.registry import SubscriptionRef

    _seed_file(media_import_db, drive_path, "fdedupmedia1")
    _seed_meta(
        media_import_db, "fdedupmedia1", provider_item_id="dQw4w9WgXcQ",
    )
    provider = MagicMock()
    provider.name = "youtube"
    ref = SubscriptionRef(kind="video", ref="dQw4w9WgXcQ")

    outcome = SubscriptionManager()._import_one_item(
        provider=provider,
        ref=ref,
        item_id="dQw4w9WgXcQ",
        drive="drv",
        folder_path="",
        include_no_transcript=False,
    )

    assert outcome.reused is True
    assert outcome.file_id == "fdedupmedia1"
    provider.fetch_item.assert_not_called()
    assert sorted(p.name for p in drive_path.iterdir()) == ["Title.mp4"]
    assert _files_rows(media_import_db) == 1
    assert _meta_row(media_import_db, "fdedupmedia1") is not None
