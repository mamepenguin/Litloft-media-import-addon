"""SPEC-ADDON-188 (``POST /link`` names why it refused) and SPEC-ADDON-189
(``POST /link/{id}/refresh`` on a ``.loft`` that never got a metadata row).

yt-dlp is cut at the title fetch; its failures are yt-dlp's own classes.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from yt_dlp.utils import DownloadError, ExtractorError, UnsupportedError

LOFT_MIME = "application/vnd.litloft.loft+json"
LINK = "/api/addons/media_import/link"
_YT_PREFIX = "ERROR: [youtube] dQw4w9WgXcQ: "
_RATE_SESSION = (
    "Video unavailable. This content isn't available, try again later. "
    "The current session has been rate-limited by YouTube for up to an hour. "
    "It is recommended to use `-t sleep` to add a delay between video requests"
)


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


def _unsupported(url: str) -> DownloadError:
    cause = UnsupportedError(url)
    return DownloadError(
        f"ERROR: Unsupported URL: {url}",
        exc_info=(UnsupportedError, cause, None),
    )


def _extractor_failure(message: str) -> DownloadError:
    cause = ExtractorError(message, expected=True)
    return DownloadError(
        _YT_PREFIX + message, exc_info=(ExtractorError, cause, None)
    )


def _files_rows(media_import_db) -> int:
    db = media_import_db()
    try:
        return db.execute(text("SELECT COUNT(*) FROM files")).scalar_one()
    finally:
        db.close()


def _loft_files(drive_path) -> list:
    return sorted(drive_path.rglob("*.loft"))


# ---------------------------------------------------------------------------
# SPEC-ADDON-188: the title fetch failed
# ---------------------------------------------------------------------------


TITLE_FAILURES = [
    pytest.param(
        lambda url: _unsupported(url), 422, "unsupported_url",
        id="unsupported-no-extractor",
    ),
    pytest.param(
        lambda url: DownloadError(
            f"ERROR: [generic] Unsupported URL: {url}",
            exc_info=(UnsupportedError, UnsupportedError(url), None),
        ),
        422, "unsupported_url",
        id="unsupported-generic-found-no-video",
    ),
    pytest.param(
        lambda url: DownloadError(
            _YT_PREFIX + "Video unavailable",
            exc_info=(UnsupportedError, UnsupportedError(url), None),
        ),
        422, "unsupported_url",
        id="unsupported-checked-before-unavailable",
    ),
    pytest.param(
        lambda _url: _extractor_failure("Video unavailable"), 422, "unavailable",
        id="unavailable-removed",
    ),
    pytest.param(
        lambda _url: _extractor_failure(
            "Private video. Sign in if you've been granted access to this video"
        ),
        422, "unavailable",
        id="unavailable-private",
    ),
    pytest.param(
        lambda _url: _extractor_failure(
            "This content is not available in your country"
        ),
        422, "unavailable",
        id="unavailable-region-locked",
    ),
    pytest.param(
        lambda _url: DownloadError(
            _YT_PREFIX + "Join this channel to get access to members-only "
            "content like this video"
        ),
        422, "unavailable",
        id="unavailable-members-only-without-exc-info",
    ),
    pytest.param(
        lambda _url: _extractor_failure(_RATE_SESSION), 500, "failed",
        id="rate-limited-before-unavailable",
    ),
    pytest.param(
        lambda _url: DownloadError(_YT_PREFIX + "HTTP Error 429: Too Many Requests"),
        500, "failed",
        id="rate-limited-429",
    ),
    pytest.param(
        lambda _url: _extractor_failure("This live event will begin in 3 days."),
        500, "failed",
        id="not-started",
    ),
    pytest.param(
        lambda _url: DownloadError(
            "ERROR: Unable to download webpage: <urlopen error timed out>"
        ),
        500, "failed",
        id="network",
    ),
    pytest.param(
        lambda _url: RuntimeError("something else"), 500, "failed",
        id="unclassified",
    ),
]


class TestCreateLoftNamesTheReason:
    @pytest.mark.parametrize(("raise_for", "status", "code"), TITLE_FAILURES)
    def test_spec_addon_188_title_fetch_failure_answers_its_code_and_writes_nothing(
        self, client, media_import_db, drive_path, enqueued, raise_for, status, code,
    ) -> None:
        """SPEC-ADDON-188 §1 table, I1: the code as the string ``detail``; no file, no row."""
        url = "https://example.com/some/page"
        with patch(
            "addons.media_import.service._extract_title_sync",
            side_effect=raise_for(url),
        ):
            res = client.post(
                LINK, json={"url": url, "drive": "drv", "folder_path": ""},
            )

        assert res.status_code == status, res.text
        assert res.json() == {"detail": code}
        assert _loft_files(drive_path) == []
        assert _files_rows(media_import_db) == 0
        assert enqueued == []

    @pytest.mark.parametrize(
        ("raise_for", "code"),
        [
            pytest.param(lambda url: _unsupported(url), "unsupported_url", id="unsupported"),
            pytest.param(
                lambda _url: _extractor_failure("Video unavailable"), "unavailable",
                id="unavailable",
            ),
        ],
    )
    def test_spec_addon_188_a_named_reason_is_logged_at_info_with_the_url(
        self, client, caplog, raise_for, code,
    ) -> None:
        """SPEC-ADDON-188 §8: ``logger.info`` with the URL for both named reasons."""
        url = "https://example.com/logged/page"
        with caplog.at_level(logging.INFO), patch(
            "addons.media_import.service._extract_title_sync",
            side_effect=raise_for(url),
        ):
            res = client.post(
                LINK, json={"url": url, "drive": "drv", "folder_path": ""},
            )

        assert res.json() == {"detail": code}
        info = [r for r in caplog.records if r.levelno == logging.INFO and url in r.getMessage()]
        assert info, [r.getMessage() for r in caplog.records]


class TestCreateLoftAfterTheTitle:
    def test_spec_addon_188_a_folder_that_cannot_be_made_answers_failed_and_writes_nothing(
        self, client, media_import_db, drive_path, enqueued,
    ) -> None:
        """SPEC-ADDON-188 §1 ``failed`` / I1: folder resolution after a good title."""
        (drive_path / "blocker").write_text("a plain file", encoding="utf-8")
        with patch(
            "addons.media_import.service._extract_title_sync",
            return_value="Good Title",
        ):
            res = client.post(
                LINK,
                json={
                    "url": "https://vimeo.com/123456",
                    "drive": "drv",
                    "folder_path": "blocker/sub",
                },
            )

        assert res.status_code == 500, res.text
        assert res.json() == {"detail": "failed"}
        assert _loft_files(drive_path) == []
        assert _files_rows(media_import_db) == 0
        assert enqueued == []

    def test_spec_addon_188_a_db_failure_answers_failed_and_leaves_no_file(
        self, client, media_import_db, drive_path, enqueued,
    ) -> None:
        """SPEC-ADDON-188 §1 ``failed`` / I1: the row write fails after the file was written."""
        db = media_import_db()
        try:
            db.execute(text(
                "CREATE TRIGGER refuse_files BEFORE INSERT ON files "
                "BEGIN SELECT RAISE(ABORT, 'refused'); END"
            ))
            db.commit()
        finally:
            db.close()

        with patch(
            "addons.media_import.service._extract_title_sync",
            return_value="Good Title",
        ):
            res = client.post(
                LINK,
                json={"url": "https://vimeo.com/123456", "drive": "drv", "folder_path": ""},
            )

        assert res.status_code == 500, res.text
        assert res.json() == {"detail": "failed"}
        assert _loft_files(drive_path) == []
        assert _files_rows(media_import_db) == 0
        assert enqueued == []


# ---------------------------------------------------------------------------
# SPEC-ADDON-189: refresh a .loft with no metadata row
# ---------------------------------------------------------------------------


def _seed_file(
    media_import_db,
    drive_path,
    file_id: str,
    *,
    drive: str = "drv",
    mime: str = LOFT_MIME,
    content: str | None = None,
    deleted: bool = False,
    missing: bool = False,
    row_url: str | None = None,
) -> None:
    from app.models import File

    file_path = f"{file_id}.loft"
    if content is not None:
        (drive_path / file_path).write_text(content, encoding="utf-8")
    now = datetime.now(UTC)
    db = media_import_db()
    try:
        db.add(
            File(
                id=file_id,
                filename=file_path,
                title=file_id,
                drive=drive,
                folder_path="",
                file_path=file_path,
                file_size=len(content or ""),
                file_type="other",
                mime_type=mime,
                created_at=now,
                updated_at=now,
                deleted_at=now if deleted else None,
                missing_since=now if missing else None,
            )
        )
        db.commit()
        if row_url is not None:
            db.execute(
                text(
                    "INSERT INTO loft_metadata (file_id, provider, url) "
                    "VALUES (:id, 'youtube', :url)"
                ),
                {"id": file_id, "url": row_url},
            )
            db.commit()
    finally:
        db.close()


def _loft_body(url: object, provider: str = "vimeo") -> str:
    return json.dumps({"provider": provider, "url": url})


READABLE = "freadable01"
READABLE_URL = "https://vimeo.com/987654"


class TestRefreshWithoutARow:
    def test_spec_addon_189_i4_queues_the_rows_url_or_else_the_files_url(
        self, client, media_import_db, drive_path, enqueued,
    ) -> None:
        """SPEC-ADDON-189 I4: row URL when a row exists, the ``.loft``'s ``url`` otherwise."""
        _seed_file(
            media_import_db, drive_path, "fwithrow01",
            content=_loft_body("https://vimeo.com/111"), row_url="https://row.example/v",
        )
        _seed_file(
            media_import_db, drive_path, READABLE, content=_loft_body(READABLE_URL),
        )

        with_row = client.post(f"{LINK}/fwithrow01/refresh")
        without_row = client.post(f"{LINK}/{READABLE}/refresh")

        assert with_row.status_code == 200
        assert with_row.json() == {"status": "queued"}
        assert without_row.status_code == 200, without_row.text
        assert without_row.json() == {"status": "queued"}
        assert enqueued == [
            ("fwithrow01", "https://row.example/v", "drv", "manual"),
            (READABLE, READABLE_URL, "drv", "manual"),
        ]

    @pytest.mark.parametrize(
        "seed",
        [
            pytest.param(dict(mime="video/mp4", content=_loft_body("https://vimeo.com/1")), id="not-the-loft-mime"),
            pytest.param(dict(content=None), id="file-absent"),
            pytest.param(dict(content="{not json"), id="not-json"),
            pytest.param(dict(content=json.dumps(["https://vimeo.com/1"])), id="json-array"),
            pytest.param(dict(content=json.dumps("https://vimeo.com/1")), id="json-string"),
            pytest.param(dict(content=json.dumps({"provider": "vimeo"})), id="no-url"),
            pytest.param(dict(content=_loft_body("")), id="empty-url"),
            pytest.param(dict(content=_loft_body(123)), id="numeric-url"),
            pytest.param(dict(content=_loft_body(None)), id="null-url"),
            pytest.param(dict(content=_loft_body("https://vimeo.com/1"), deleted=True), id="trashed"),
            pytest.param(dict(content=_loft_body("https://vimeo.com/1"), missing=True), id="missing"),
            pytest.param(dict(content=_loft_body("https://vimeo.com/1"), drive="other"), id="other-drive"),
        ],
    )
    def test_spec_addon_189_i4b_an_unusable_file_is_404_and_queues_nothing_beside_a_readable_one(
        self, client, media_import_db, drive_path, enqueued, seed,
    ) -> None:
        """SPEC-ADDON-189 §2, I4 / I4b: 404, nothing queued; the readable file still queues."""
        _seed_file(media_import_db, drive_path, "funusable1", **seed)
        _seed_file(
            media_import_db, drive_path, READABLE, content=_loft_body(READABLE_URL),
        )

        refused = client.post(f"{LINK}/funusable1/refresh")
        queued = client.post(f"{LINK}/{READABLE}/refresh")

        assert refused.status_code == 404
        assert "vimeo.com" not in refused.text
        assert queued.status_code == 200, queued.text
        assert enqueued == [(READABLE, READABLE_URL, "drv", "manual")]
