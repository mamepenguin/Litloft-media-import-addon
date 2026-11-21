"""SPEC-ADDON-165 (caption track chosen from metadata), SPEC-ADDON-166 (has_captions
means a usable track is listed), SPEC-ADDON-167 (fetch-path metadata unavailability is
recorded permanent).

yt-dlp is cut at ``yt_dlp.YoutubeDL``: one info dict per video serves both the metadata
extraction and the caption download, and the download writes a ``.vtt`` only for a key
that info lists, as yt-dlp does.
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text
from yt_dlp.utils import DownloadError

from addons.media_import.tests.test_youtube_item_classification import (
    _CASES,
    _PREFIX,
    UNAVAILABLE,
)

_YT = "addons.media_import.subscription.providers.youtube"
VIDEO_ID = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
TITLE = "Clip"
LOFT_MIME = "application/vnd.litloft.loft+json"
VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n"


def _vtt_for(key: str) -> str:
    return f"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\ncaption {key}\n"


def _cue_texts(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        line for line in lines
        if line and line != "WEBVTT" and "-->" not in line and not line.isdigit()
    ]


def _info(language, subtitles=(), automatic=()) -> dict:
    return {
        "id": VIDEO_ID,
        "title": TITLE,
        "duration": 10,
        "description": "d",
        "channel": "c",
        "upload_date": "20260101",
        "language": language,
        "subtitles": {k: [{"ext": "vtt", "url": f"https://s/{k}"}] for k in subtitles},
        "automatic_captions": {
            k: [{"ext": "vtt", "url": f"https://a/{k}"}] for k in automatic
        },
    }


def _ydl_for(info: dict):
    """A ``YoutubeDL`` stand-in; ``built`` records every options dict."""

    class _YDL:
        built: list[dict] = []

        def __init__(self, opts=None, *_a, **_kw):
            self.opts = opts or {}
            type(self).built.append(self.opts)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, _url, download=False, **_kw):
            if self.opts.get("extract_flat"):
                return {"id": "UCabcdefghijklmnopqrstuv", "title": "Channel", "entries": []}
            return dict(info)

        def download(self, _urls):
            listed = {k for k in info["subtitles"] if k != "live_chat"} | set(
                info["automatic_captions"]
            )
            for key in self.opts.get("subtitleslangs") or []:
                if key in listed:
                    Path(self.opts["outtmpl"].replace("%(ext)s", f"{key}.vtt")).write_text(
                        _vtt_for(key), encoding="utf-8"
                    )
            return 0

    return _YDL


def _caption_requests(ydl) -> list[list[str]]:
    return [o["subtitleslangs"] for o in ydl.built if "subtitleslangs" in o]


def _audio_builds(ydl) -> list[dict]:
    return [o for o in ydl.built if "format" in o]


# --- Track choice and has_captions on _fetch_metadata_sync ---------------------

_TRACK_TABLE = [
    pytest.param("ja", ["ja"], ["ja", "en"], "ja", id="L-in-subtitles"),
    pytest.param("en", [], ["en"], "en", id="L-in-automatic-only"),
    pytest.param("en", [], ["en-orig"], "en-orig", id="L-orig-only"),
    pytest.param("en-US", [], ["en", "en-orig"], "en", id="region-falls-to-base"),
    pytest.param("en-US", [], ["en-orig"], "en-orig", id="region-falls-to-base-orig"),
    pytest.param("en-US", ["en-US"], ["en"], "en-US", id="region-listed-wins"),
    pytest.param("en-US", [], ["en-US-orig", "en"], "en-US-orig", id="region-orig-before-base"),
    pytest.param("en-US", ["en"], ["en-US-orig"], "en-US-orig", id="candidate-order-beats-source"),
    pytest.param("es-419", [], ["es"], "es", id="numeric-region-to-base"),
    pytest.param("sr-Latn-RS", [], ["sr"], "sr", id="base-is-before-first-hyphen"),
    pytest.param(None, [], ["ja"], "ja", id="unknown-language-is-ja"),
    pytest.param(None, [], ["ja-orig"], "ja-orig", id="unknown-language-ja-orig"),
    pytest.param(None, ["live_chat"], [], None, id="chat-replay-only"),
    pytest.param(None, ["live_chat"], ["ja"], "ja", id="chat-replay-plus-automatic"),
    pytest.param("ja", ["live_chat"], [], None, id="chat-replay-only-known-language"),
    pytest.param(None, [], ["en"], None, id="unknown-language-other-listed"),
    pytest.param("en", ["en-GB"], [], None, id="base-not-widened-to-region"),
    pytest.param("en-US", [], ["en-us"], None, id="keys-are-exact-strings"),
    pytest.param("en", [], [], None, id="nothing-listed"),
]


@pytest.mark.parametrize("language, subtitles, automatic, expected", _TRACK_TABLE)
def test_spec_addon_165_166_caption_track_and_has_captions_from_listed_keys(
    language, subtitles, automatic, expected
):
    from addons.media_import.service import _fetch_metadata_sync

    with patch("yt_dlp.YoutubeDL", _ydl_for(_info(language, subtitles, automatic))):
        meta = _fetch_metadata_sync(URL)

    assert (meta["caption_track"], meta["has_captions"]) == (expected, expected is not None)


# --- Fetch path ----------------------------------------------------------------


def _seed_loft(session, drive: Path, file_id: str = "fcap0000001") -> Path:
    from app.models import File

    loft = drive / f"{TITLE}.loft"
    loft.write_text(f'{{"url": "{URL}"}}', encoding="utf-8")
    db = session()
    try:
        db.add(File(
            id=file_id,
            filename=f"{TITLE}.loft",
            title=TITLE,
            drive="drv",
            folder_path="",
            file_path=f"{TITLE}.loft",
            file_size=loft.stat().st_size,
            file_type="other",
            mime_type=LOFT_MIME,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ))
        db.commit()
    finally:
        db.close()
    return loft


def _insert_meta_row(session, file_id: str, *, has_captions=1, downloaded=0, kind=None):
    db = session()
    try:
        db.execute(
            text(
                "INSERT INTO loft_metadata "
                "(file_id, provider, url, channel, has_captions, captions_downloaded, "
                " caption_error_kind) "
                "VALUES (:f, 'youtube', :u, :t, :h, :d, :k)"
            ),
            {"f": file_id, "u": URL, "t": "c", "h": has_captions, "d": downloaded, "k": kind},
        )
        db.commit()
    finally:
        db.close()


def _caption_row(session, file_id: str):
    db = session()
    try:
        return db.execute(
            text(
                "SELECT has_captions, captions_downloaded, caption_error_kind "
                "FROM loft_metadata WHERE file_id = :f"
            ),
            {"f": file_id},
        ).first()
    finally:
        db.close()


def _run_fetch(info: dict, file_id: str = "fcap0000001"):
    from addons.media_import.service import LoftFetchItem, LoftManager

    ydl = _ydl_for(info)
    with patch("yt_dlp.YoutubeDL", ydl), \
            patch("addons.media_import.service._save_loft_thumbnail"):
        LoftManager()._fetch_and_update(LoftFetchItem(file_id=file_id, url=URL, drive="drv"))
    return ydl


def _retried(session) -> list[str]:
    from addons.media_import.service import LoftManager

    manager = LoftManager()
    enqueued: list[str] = []

    async def _capture(file_id, url, drive, *_a, **_kw):
        enqueued.append(file_id)

    async def _no_sleep(*_a, **_kw):
        return None

    with patch.object(manager, "enqueue_fetch", _capture), \
            patch.object(asyncio, "sleep", _no_sleep):
        asyncio.run(manager._retry_failed_captions())
    return enqueued


def test_spec_addon_165_fetch_path_saves_the_base_language_track_for_a_region_video(
    media_import_db, drive_path
):
    loft = _seed_loft(media_import_db, drive_path)
    _insert_meta_row(media_import_db, "fcap0000001")

    ydl = _run_fetch(_info("en-US", [], ["en", "en-orig"]))

    assert _caption_requests(ydl) == [["en"]]
    assert len(ydl.built) == 2
    assert _cue_texts(loft.parent / f"{TITLE}.vtt") == ["caption en"]
    assert tuple(_caption_row(media_import_db, "fcap0000001")) == (1, 1, None)
    assert _retried(media_import_db) == []


def test_spec_addon_166_fetch_path_chat_replay_only_has_no_captions_and_leaves_the_retry_set(
    media_import_db, drive_path
):
    loft = _seed_loft(media_import_db, drive_path)
    _insert_meta_row(media_import_db, "fcap0000001")

    ydl = _run_fetch(_info(None, ["live_chat"], []))

    assert _caption_requests(ydl) == []
    assert _audio_builds(ydl) == []
    assert len(ydl.built) == 1
    assert sorted(os.listdir(loft.parent)) == [f"{TITLE}.loft"]
    has_captions, downloaded, _kind = _caption_row(media_import_db, "fcap0000001")
    assert (bool(has_captions), bool(downloaded)) == (False, False)
    assert _retried(media_import_db) == []


# --- Subscription path -----------------------------------------------------------

_RSS_ONE = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Channel</title>
  <entry>
    <id>yt:video:{VIDEO_ID}</id>
    <yt:videoId>{VIDEO_ID}</yt:videoId>
    <yt:channelId>UCabcdefghijklmnopqrstuv</yt:channelId>
    <title>{TITLE}</title>
    <published>2026-10-01T10:00:00+00:00</published>
  </entry>
</feed>
""".encode()


def _insert_subscription(session) -> int:
    db = session()
    try:
        db.execute(
            text(
                "INSERT INTO subscriptions "
                "(provider, source_kind, source_ref, drive, folder_path, "
                " is_enabled, cooldown_minutes, include_no_transcript, created_at, "
                " display_title) "
                "VALUES ('youtube', 'channel', 'UCabcdefghijklmnopqrstuv', 'd', '', "
                " 1, 60, 0, '2026-05-01T00:00:00', 'Channel')"
            )
        )
        sub_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
        db.commit()
    finally:
        db.close()
    return sub_id


def _sync(session, info: dict):
    from addons.media_import.subscription.manager import SubscriptionManager
    from addons.media_import.subscription.providers.youtube import YouTubeProvider
    from addons.media_import.subscription.registry import (
        _reset_subscription_registry_for_tests,
        register_subscription_provider,
    )

    ydl = _ydl_for(info)
    _reset_subscription_registry_for_tests()
    register_subscription_provider(YouTubeProvider())
    try:
        sub_id = _insert_subscription(session)
        with patch(f"{_YT}._http_get_bytes", return_value=_RSS_ONE), \
                patch("yt_dlp.YoutubeDL", ydl), \
                patch("time.sleep"):
            SubscriptionManager()._sync_blocking(sub_id)
    finally:
        _reset_subscription_registry_for_tests()

    db = session()
    try:
        video = db.execute(
            text(
                "SELECT status, error_kind, file_id FROM subscription_videos "
                "WHERE subscription_id = :s AND item_id = :i"
            ),
            {"s": sub_id, "i": VIDEO_ID},
        ).mappings().first()
        meta = db.execute(
            text(
                "SELECT has_captions, captions_downloaded FROM loft_metadata "
                "WHERE provider_item_id = :i"
            ),
            {"i": VIDEO_ID},
        ).first()
    finally:
        db.close()
    return ydl, video, meta


def test_spec_addon_165_subscription_saves_the_base_language_track_for_a_region_video(
    media_import_db, drive_path
):
    ydl, video, meta = _sync(media_import_db, _info("en-US", [], ["en", "en-orig"]))

    assert _caption_requests(ydl) == [["en"]]
    assert video["status"] == "imported"
    assert (drive_path / f"{TITLE}.loft").exists()
    assert _cue_texts(drive_path / f"{TITLE}.vtt") == ["caption en"]
    assert (bool(meta[0]), bool(meta[1])) == (True, True)


def test_spec_addon_166_subscription_imports_a_chat_replay_only_video_without_a_caption_download(
    media_import_db, drive_path
):
    ydl, video, meta = _sync(media_import_db, _info(None, ["live_chat"], []))

    assert _caption_requests(ydl) == []
    assert _audio_builds(ydl) == []
    assert (video["status"], video["error_kind"]) == ("imported", None)
    assert (drive_path / f"{TITLE}.loft").exists()
    assert not (drive_path / f"{TITLE}.vtt").exists()
    assert (bool(meta[0]), bool(meta[1])) == (False, False)


# --- Metadata unavailability on the fetch path -----------------------------------


def _row_snapshot(session, file_id: str) -> tuple[dict | None, dict | None]:
    db = session()
    try:
        meta = db.execute(
            text("SELECT * FROM loft_metadata WHERE file_id = :f"), {"f": file_id}
        ).mappings().first()
        file = db.execute(
            text("SELECT * FROM files WHERE id = :f"), {"f": file_id}
        ).mappings().first()
    finally:
        db.close()
    return (dict(meta) if meta else None), (dict(file) if file else None)


def _fetch_raising(file_id: str, message: str) -> None:
    from addons.media_import.service import LoftFetchItem, LoftManager

    with patch(
        "addons.media_import.service._fetch_metadata_sync",
        side_effect=DownloadError(_PREFIX + message),
    ), pytest.raises(DownloadError):
        LoftManager()._fetch_and_update(LoftFetchItem(file_id=file_id, url=URL, drive="drv"))


def test_spec_addon_167_metadata_error_writes_permanent_exactly_when_it_is_unavailable(
    media_import_db, drive_path
):
    from app.models import File

    db = media_import_db()
    try:
        for n, _ in enumerate(_CASES):
            db.add(File(
                id=f"ferr{n:07d}", filename=f"e{n}.loft", title=f"e{n}", drive="drv",
                folder_path="", file_path=f"e{n}.loft", file_size=1, file_type="other",
                mime_type=LOFT_MIME, created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ))
        db.commit()
    finally:
        db.close()

    observed: dict[str, object] = {}
    expected: dict[str, object] = {}
    for n, (message, outcome) in enumerate(_CASES):
        file_id = f"ferr{n:07d}"
        _insert_meta_row(media_import_db, file_id)
        before_meta, before_file = _row_snapshot(media_import_db, file_id)

        _fetch_raising(file_id, message)

        after_meta, after_file = _row_snapshot(media_import_db, file_id)
        assert after_file == before_file, message
        assert {k: v for k, v in after_meta.items() if k != "caption_error_kind"} == {
            k: v for k, v in before_meta.items() if k != "caption_error_kind"
        }, message
        observed[message] = after_meta["caption_error_kind"]
        expected[message] = "permanent" if outcome == UNAVAILABLE else None

    assert observed == expected


def test_spec_addon_167_unavailable_metadata_keeps_a_downloaded_rows_captions(
    media_import_db, drive_path
):
    loft = _seed_loft(media_import_db, drive_path)
    vtt = loft.parent / f"{TITLE}.vtt"
    vtt.write_text(VTT, encoding="utf-8")
    _insert_meta_row(media_import_db, "fcap0000001", downloaded=1)
    before_meta, before_file = _row_snapshot(media_import_db, "fcap0000001")
    before_listing = sorted(os.listdir(drive_path))

    _fetch_raising("fcap0000001", "Private video. Sign in if you've been granted access")

    after_meta, after_file = _row_snapshot(media_import_db, "fcap0000001")
    assert after_meta == {**before_meta, "caption_error_kind": "permanent"}
    assert after_file == before_file
    assert sorted(os.listdir(drive_path)) == before_listing
    assert vtt.read_text(encoding="utf-8") == VTT
    assert loft.read_text(encoding="utf-8") == f'{{"url": "{URL}"}}'


def test_spec_addon_167_unavailable_metadata_without_a_row_writes_nothing_and_a_row_with_one_is_permanent(
    media_import_db, drive_path
):
    from app.models import File

    db = media_import_db()
    try:
        for fid in ("fnorow00001", "fwithrow001"):
            db.add(File(
                id=fid, filename=f"{fid}.loft", title=fid, drive="drv", folder_path="",
                file_path=f"{fid}.loft", file_size=1, file_type="other",
                mime_type=LOFT_MIME, created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ))
        db.commit()
    finally:
        db.close()
    _insert_meta_row(media_import_db, "fwithrow001")

    _fetch_raising("fnorow00001", "Video unavailable")
    _fetch_raising("fwithrow001", "Video unavailable")

    assert _row_snapshot(media_import_db, "fnorow00001")[0] is None
    assert _caption_row(media_import_db, "fwithrow001")[2] == "permanent"
