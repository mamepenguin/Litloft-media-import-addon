"""HTTP-level tests for the Media Import addon router.

Verifies the 3 endpoints honour the spec contract:
- POST /link            → creates .loft and queues a metadata fetch
- GET  /link/{id}/metadata → returns persisted metadata or 404
- POST /link/{id}/refresh  → queues a re-fetch or 404 when unknown
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text


@pytest.fixture()
def client(media_import_db, drive_path):
    from addons.media_import.router import router

    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    # POST /link is now scope=drive: it requires X-Lit-Drive matching
    # the drive in the body. All current tests operate on drive "drv";
    # locked-drive tests can override per-request.
    c.headers["X-Lit-Drive"] = "drv"
    return c


class TestCreateLoftEndpoint:
    def test_returns_file_id_and_filename(self, client) -> None:
        from addons.media_import import service
        from addons.media_import.router import loft_manager

        async def _fake_enqueue(*_a, **_kw) -> None:
            return None

        # Stub yt-dlp + the queueing side effect (no event loop in tests).
        with patch.object(loft_manager, "enqueue_fetch", _fake_enqueue), patch(
            "addons.media_import.service._extract_title_sync",
            return_value="Test Video",
        ):
            res = client.post(
                "/api/addons/media_import/link",
                json={
                    "url": "https://www.youtube.com/watch?v=abc",
                    "drive": "drv",
                    "folder_path": "",
                },
            )

        assert res.status_code == 200
        body = res.json()
        assert body["filename"] == "Test Video.loft"
        assert isinstance(body["file_id"], str) and body["file_id"]

    def test_rejects_blank_url(self, client) -> None:
        res = client.post(
            "/api/addons/media_import/link",
            json={"url": "   ", "drive": "drv", "folder_path": ""},
        )
        assert res.status_code == 422

    def test_rejects_unknown_drive(self, client, monkeypatch) -> None:
        import app.config as config

        def _raise(_name: str):
            raise ValueError("unknown drive")

        monkeypatch.setattr(config, "get_drive_path", _raise)

        res = client.post(
            "/api/addons/media_import/link",
            json={
                "url": "https://www.youtube.com/watch?v=abc",
                "drive": "missing",
                "folder_path": "",
            },
        )
        assert res.status_code == 404


class TestMetadataEndpoint:
    def test_returns_404_when_metadata_missing(self, client) -> None:
        res = client.get("/api/addons/media_import/link/nonexistent/metadata")
        assert res.status_code == 404

    def test_returns_metadata_when_present(self, client, media_import_db) -> None:
        # Seed a file row + a loft_metadata row.
        from datetime import UTC, datetime

        from app.models import File

        db = media_import_db()
        try:
            db.add(
                File(
                    id="fseed00001",
                    filename="seed.loft",
                    title="seed",
                    drive="drv",
                    folder_path="",
                    file_path="seed.loft",
                    file_size=1,
                    file_type="other",
                    mime_type="application/vnd.litloft.loft+json",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            db.commit()
            db.execute(
                text(
                    "INSERT INTO loft_metadata "
                    "(file_id, provider, url, channel, has_captions, "
                    "captions_downloaded) VALUES "
                    "('fseed00001', 'youtube', 'https://x', 'C', 1, 0)"
                )
            )
            db.commit()
        finally:
            db.close()

        res = client.get("/api/addons/media_import/link/fseed00001/metadata")
        assert res.status_code == 200
        body = res.json()
        assert body["provider"] == "youtube"
        assert body["channel"] == "C"
        assert body["has_captions"] is True
        assert body["captions_downloaded"] is False


class TestRefreshEndpoint:
    def test_returns_404_when_metadata_missing(self, client) -> None:
        res = client.post("/api/addons/media_import/link/missing/refresh")
        assert res.status_code == 404

    def test_queues_refetch_when_metadata_present(
        self, client, media_import_db
    ) -> None:
        from datetime import UTC, datetime

        from app.models import File
        from addons.media_import.router import loft_manager

        db = media_import_db()
        try:
            db.add(
                File(
                    id="frefresh01",
                    filename="x.loft",
                    title="x",
                    drive="drv",
                    folder_path="",
                    file_path="x.loft",
                    file_size=1,
                    file_type="other",
                    mime_type="application/vnd.litloft.loft+json",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            db.commit()
            db.execute(
                text(
                    "INSERT INTO loft_metadata (file_id, provider, url) "
                    "VALUES ('frefresh01', 'youtube', 'https://x')"
                )
            )
            db.commit()
        finally:
            db.close()

        captured: list[tuple[str, str, str]] = []

        async def _capture(file_id: str, url: str, drive: str) -> None:
            captured.append((file_id, url, drive))

        with patch.object(loft_manager, "enqueue_fetch", _capture):
            res = client.post(
                "/api/addons/media_import/link/frefresh01/refresh"
            )

        assert res.status_code == 200
        assert res.json() == {"status": "queued"}
        assert captured == [("frefresh01", "https://x", "drv")]
