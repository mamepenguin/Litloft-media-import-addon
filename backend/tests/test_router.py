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

    def test_passes_stt_mode_to_fetch_queue(self, client) -> None:
        from addons.media_import.router import loft_manager

        captured: list[tuple[str, str, str, str]] = []

        async def _capture(
            file_id: str, url: str, drive: str, stt_mode: str = "manual"
        ) -> None:
            captured.append((file_id, url, drive, stt_mode))

        with patch.object(loft_manager, "enqueue_fetch", _capture), patch(
            "addons.media_import.service._extract_title_sync",
            return_value="Test Video",
        ):
            res = client.post(
                "/api/addons/media_import/link",
                json={
                    "url": "https://www.youtube.com/watch?v=abc",
                    "drive": "drv",
                    "folder_path": "",
                    "stt_mode": "missing_captions",
                },
            )

        assert res.status_code == 200
        assert captured == [
            (
                res.json()["file_id"],
                "https://www.youtube.com/watch?v=abc",
                "drv",
                "missing_captions",
            )
        ]

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


def _seed_loft(
    media_import_db,
    file_id: str,
    drive: str,
    *,
    deleted: bool = False,
    missing: bool = False,
) -> None:
    from datetime import UTC, datetime

    from app.models import File

    now = datetime.now(UTC)
    db = media_import_db()
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
                created_at=now,
                updated_at=now,
                deleted_at=now if deleted else None,
                missing_since=now if missing else None,
            )
        )
        db.commit()
        db.execute(
            text(
                "INSERT INTO loft_metadata (file_id, provider, url) "
                "VALUES (:id, 'youtube', 'https://x')"
            ),
            {"id": file_id},
        )
        db.commit()
    finally:
        db.close()


class TestLinkDriveAccess:
    """GET /metadata and POST /refresh must hold the drive boundary: a
    file on a drive the caller cannot see, or on a drive other than the
    scoped one, is indistinguishable from one that does not exist.
    """

    ENDPOINTS = [
        ("GET", "/api/addons/media_import/link/{id}/metadata"),
        ("POST", "/api/addons/media_import/link/{id}/refresh"),
    ]

    @pytest.fixture()
    def enqueued(self):
        from addons.media_import.router import loft_manager

        captured: list[tuple] = []

        async def _capture(*args, **_kw) -> None:
            captured.append(args)

        with patch.object(loft_manager, "enqueue_fetch", _capture):
            yield captured

    def _lock_drive(self, monkeypatch, locked_drive: str, group: str) -> None:
        import app.config as config

        monkeypatch.setattr(
            config,
            "get_drive_access_group",
            lambda name: group if name == locked_drive else None,
        )

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_locked_drive_returns_404(
        self, client, media_import_db, monkeypatch, enqueued, method, path
    ) -> None:
        _seed_loft(media_import_db, "flocked001", "secret")
        self._lock_drive(monkeypatch, "secret", "vip")

        res = client.request(
            method,
            path.format(id="flocked001"),
            headers={"X-Lit-Drive": "secret"},
        )

        assert res.status_code == 404
        assert "youtube" not in res.text
        assert enqueued == []

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_file_on_other_drive_returns_404(
        self, client, media_import_db, enqueued, method, path
    ) -> None:
        _seed_loft(media_import_db, "fother0001", "other")

        res = client.request(method, path.format(id="fother0001"))

        assert res.status_code == 404
        assert enqueued == []

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_missing_drive_header_returns_400(
        self, client, media_import_db, enqueued, method, path
    ) -> None:
        _seed_loft(media_import_db, "fnohdr0001", "drv")

        res = client.request(
            method,
            path.format(id="fnohdr0001"),
            headers={"X-Lit-Drive": ""},
        )

        assert res.status_code == 400
        assert enqueued == []

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_absent_drive_header_returns_400(
        self, client, media_import_db, enqueued, method, path
    ) -> None:
        _seed_loft(media_import_db, "fabsent001", "drv")
        del client.headers["X-Lit-Drive"]

        res = client.request(method, path.format(id="fabsent001"))

        assert res.status_code == 400
        assert enqueued == []

    def test_percent_encoded_non_ascii_drive_is_decoded(
        self, client, media_import_db, enqueued
    ) -> None:
        from urllib.parse import quote

        _seed_loft(media_import_db, "fnonascii1", "動画")
        headers = {"X-Lit-Drive": quote("動画")}

        meta = client.get(
            "/api/addons/media_import/link/fnonascii1/metadata",
            headers=headers,
        )
        refresh = client.post(
            "/api/addons/media_import/link/fnonascii1/refresh",
            headers=headers,
        )

        assert meta.status_code == 200
        assert refresh.status_code == 200
        assert enqueued == [("fnonascii1", "https://x", "動画")]

    @pytest.mark.parametrize("state", ["deleted", "missing"])
    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_inactive_file_returns_404(
        self, client, media_import_db, enqueued, method, path, state
    ) -> None:
        _seed_loft(media_import_db, "finact0001", "drv", **{state: True})

        res = client.request(method, path.format(id="finact0001"))

        assert res.status_code == 404
        assert enqueued == []

    def test_metadata_on_unlocked_drive_returns_200(
        self, client, media_import_db, monkeypatch
    ) -> None:
        from app.auth import get_unlocked_groups

        _seed_loft(media_import_db, "fopen00001", "secret")
        self._lock_drive(monkeypatch, "secret", "vip")
        app = client.app
        app.dependency_overrides[get_unlocked_groups] = lambda: ["vip"]
        try:
            res = client.get(
                "/api/addons/media_import/link/fopen00001/metadata",
                headers={"X-Lit-Drive": "secret"},
            )
        finally:
            app.dependency_overrides.clear()

        assert res.status_code == 200
        assert res.json()["provider"] == "youtube"


class TestManualSttEndpoint:
    def test_queues_manual_stt(self, client) -> None:
        from addons.media_import.router import loft_manager

        captured: list[tuple[str, str]] = []

        async def _capture(file_id: str, drive: str) -> str:
            captured.append((file_id, drive))
            return "queued"

        with patch.object(loft_manager, "enqueue_stt", _capture):
            res = client.post("/api/addons/media_import/link/fstt01/stt")

        assert res.status_code == 200
        assert res.json() == {"status": "queued"}
        assert captured == [("fstt01", "drv")]

    def test_manual_stt_unknown_file_returns_404(self, client) -> None:
        from addons.media_import.router import loft_manager

        async def _raise(_file_id: str, _drive: str) -> str:
            raise FileNotFoundError("missing")

        with patch.object(loft_manager, "enqueue_stt", _raise):
            res = client.post("/api/addons/media_import/link/missing/stt")

        assert res.status_code == 404
