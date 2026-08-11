"""Chapters on a ``.loft``, from the provider rather than from ffprobe.

Spec ``docs/superpowers/specs/2026-08-11-media-chapters.md`` §4.2. A
``.loft`` is a small JSON reference, so there is nothing on disk for
core's scanner to probe — it stamps the file as probed and skips
ffprobe. The provider is the only source, and Media Import writes what
it reports through core's own helpers so the storage rules are not
implemented a second time here.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy import text

from addons.media_import.service import LoftManager, _fetch_metadata_sync
from addons.media_import.schemas import LoftFetchItem

FILE_ID = "fchap000001"
DRIVE = "drv"


def _seed_file(session, file_id: str = FILE_ID, drive: str = DRIVE) -> None:
    from app.models import File

    db = session()
    try:
        db.add(
            File(
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
        )
        db.commit()
    finally:
        db.close()


def _read_chapters(session, file_id: str = FILE_ID) -> list[tuple]:
    db = session()
    try:
        return [
            tuple(row)
            for row in db.execute(
                text(
                    "SELECT ordering, start_time, end_time, title, source "
                    "FROM file_chapters WHERE file_id = :fid ORDER BY ordering"
                ),
                {"fid": file_id},
            ).all()
        ]
    finally:
        db.close()


def _meta(**overrides) -> dict:
    base = {
        "title": "T",
        "duration": 300,
        "description": "d",
        "channel": "c",
        "published_at": "20260101",
        "language": "en",
        "thumbnail_url": None,
        "has_captions": False,
        "chapters": None,
    }
    base.update(overrides)
    return base


def _run_fetch(item_url: str = "https://example.com/v", **meta_overrides) -> None:
    item = LoftFetchItem(file_id=FILE_ID, url=item_url, drive=DRIVE)
    with patch(
        "addons.media_import.service._fetch_metadata_sync",
        return_value=_meta(**meta_overrides),
    ):
        LoftManager()._fetch_and_update(item)


class TestMetadataMapping:
    def test_chapters_are_carried_through_untouched(self):
        # Whatever yt-dlp reports is what gets handed on. It already
        # derives these from the provider's markers and, failing that,
        # from description timestamps; a second parser here would drift
        # from that silently.
        raw = [{"start_time": 0.0, "end_time": 5.0, "title": "Intro"}]

        class _FakeYDL:
            def __init__(self, *_a, **_kw): ...
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def extract_info(self, _url, download=False):
                return {"title": "T", "chapters": raw}

        with patch("yt_dlp.YoutubeDL", _FakeYDL):
            meta = _fetch_metadata_sync("https://example.com/v")

        assert meta["chapters"] == raw

    def test_absent_chapters_are_none_not_empty(self):
        # "The provider said nothing" and "this video has none" are
        # different claims, and only the second would justify a delete.
        class _FakeYDL:
            def __init__(self, *_a, **_kw): ...
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def extract_info(self, _url, download=False):
                return {"title": "T"}

        with patch("yt_dlp.YoutubeDL", _FakeYDL):
            meta = _fetch_metadata_sync("https://example.com/v")

        assert meta["chapters"] is None


class TestFetchAndUpdate:
    def test_writes_the_reported_chapters(self, media_import_db, drive_path):
        _seed_file(media_import_db)

        _run_fetch(
            chapters=[
                {"start_time": 0.0, "end_time": 60.0, "title": "Intro"},
                {"start_time": 60.0, "end_time": 300.0, "title": "The point"},
            ]
        )

        assert _read_chapters(media_import_db) == [
            (0, 0.0, 60.0, "Intro", "extracted"),
            (1, 60.0, 300.0, "The point", "extracted"),
        ]

    def test_applies_the_shared_rules_rather_than_its_own(
        self, media_import_db, drive_path
    ):
        # Untitled entries are dropped and the ordering closes up behind
        # them, exactly as the ffprobe path does. If this ever diverges,
        # the two producers disagree about the same kind of file.
        _seed_file(media_import_db)

        _run_fetch(
            chapters=[
                {"start_time": 0.0, "title": "One"},
                {"start_time": 10.0, "title": "   "},
                {"start_time": 20.0, "title": "Three"},
            ]
        )

        rows = _read_chapters(media_import_db)
        assert [(r[0], r[3]) for r in rows] == [(0, "One"), (1, "Three")]

    def test_no_chapters_writes_no_rows_and_no_error(
        self, media_import_db, drive_path
    ):
        _seed_file(media_import_db)

        _run_fetch(chapters=None)

        assert _read_chapters(media_import_db) == []

    def test_a_refresh_leaves_an_approved_set_alone(
        self, media_import_db, drive_path
    ):
        # The point of the curated guard: a person's approval is not
        # re-derivable, so nothing automated may overwrite it.
        from app.services.chapters import replace_chapters

        _seed_file(media_import_db)
        db = media_import_db()
        try:
            replace_chapters(
                db,
                FILE_ID,
                [
                    {
                        "start_time": 0.0,
                        "end_time": None,
                        "title": "Approved",
                        "ordering": 0,
                    }
                ],
                "curated",
            )
            db.commit()
        finally:
            db.close()

        _run_fetch(
            chapters=[{"start_time": 0.0, "end_time": 9.0, "title": "From yt-dlp"}]
        )

        assert [(r[3], r[4]) for r in _read_chapters(media_import_db)] == [
            ("Approved", "curated")
        ]

    def test_a_refresh_that_finds_none_keeps_what_is_there(
        self, media_import_db, drive_path
    ):
        # A provider that stops reporting chapters is not evidence that
        # the video lost them.
        _seed_file(media_import_db)
        _run_fetch(chapters=[{"start_time": 0.0, "title": "Kept"}])

        _run_fetch(chapters=None)

        assert [r[3] for r in _read_chapters(media_import_db)] == ["Kept"]


class TestSubscriptionImport:
    """The other path a ``.loft`` arrives by."""

    def _register(self, drive_path, chapters):
        import json

        from addons.media_import.subscription.db import register_loft
        from addons.media_import.subscription.registry import ItemMetadata

        loft_path = drive_path / "sub-video.loft"
        loft_path.write_text(
            json.dumps({"url": "https://example.com/v", "provider": "youtube"}),
            encoding="utf-8",
        )
        meta = ItemMetadata(
            item_id="vid123",
            canonical_url="https://www.youtube.com/watch?v=vid123",
            title="Sub video",
            chapters=chapters,
        )
        return register_loft(DRIVE, loft_path, "youtube", meta)

    def test_writes_the_reported_chapters(self, media_import_db, drive_path):
        file_id = self._register(
            drive_path,
            [
                {"start_time": 0.0, "end_time": 30.0, "title": "Cold open"},
                {"start_time": 30.0, "end_time": None, "title": "Body"},
            ],
        )

        assert [
            (r[0], r[3], r[4]) for r in _read_chapters(media_import_db, file_id)
        ] == [(0, "Cold open", "extracted"), (1, "Body", "extracted")]

    def test_an_item_without_chapters_registers_cleanly(
        self, media_import_db, drive_path
    ):
        file_id = self._register(drive_path, None)

        assert _read_chapters(media_import_db, file_id) == []

    def test_the_loft_itself_is_never_handed_to_ffprobe(
        self, media_import_db, drive_path
    ):
        # ``register_single_file`` runs core's probe, which must skip a
        # .loft outright: the bytes on disk are a JSON reference, and a
        # probe of them returning nothing must not be read as "this file
        # has no chapters" once the provider has supplied some.
        with patch("app.services.thumbnail.subprocess.run") as run:
            file_id = self._register(
                drive_path, [{"start_time": 0.0, "title": "Provider"}]
            )

        chapter_probes = [
            call for call in run.call_args_list
            if call.args and "-show_chapters" in call.args[0]
        ]
        assert chapter_probes == []
        assert [r[3] for r in _read_chapters(media_import_db, file_id)] == [
            "Provider"
        ]
