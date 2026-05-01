"""HTTP-level tests for the subscription endpoints (Commit 4).

The provider seam is faked end-to-end so these tests exercise wiring
(schema → manager → DB → response) without yt-dlp / network. Manager
internals are already covered by ``test_subscription_manager.py``.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Callable
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from addons.media_import.subscription.registry import (
    ERROR_RATE_LIMITED,
    ItemHeader,
    ItemMetadata,
    REF_KIND_CHANNEL,
    REF_KIND_PLAYLIST,
    REF_KIND_VIDEO,
    SubscriptionRef,
    TranscriptResult,
    register_subscription_provider,
    _reset_subscription_registry_for_tests,
)


@dataclass
class _FakeProvider:
    name: str = "fakeyt"
    inter_item_delay_seconds: float = 0.0
    headers: list[ItemHeader] = field(default_factory=list)
    items: dict[str, ItemMetadata] = field(default_factory=dict)
    transcripts: dict[str, TranscriptResult] = field(default_factory=dict)
    resolve_url: Callable[[str], SubscriptionRef | None] | None = None

    def resolve_ref(self, url):
        return self.resolve_url(url) if self.resolve_url else None

    def list_items(self, ref, limit=None):
        out = list(self.headers)
        return out if limit is None else out[:limit]

    def fetch_item(self, ref, item_id):
        return self.items.get(
            item_id,
            ItemMetadata(
                item_id=item_id,
                canonical_url=f"https://fake/{item_id}",
                title=f"T {item_id}",
            ),
        )

    def fetch_transcript(self, ref, item_id, language=None):
        return self.transcripts.get(
            item_id, TranscriptResult(error_kind="no_transcript")
        )

    def build_loft_content(self, item):
        return {"provider": self.name, "url": item.canonical_url}


@pytest.fixture()
def fake_provider():
    def _resolve(url: str):
        if "fake/channel/" in url:
            return SubscriptionRef(
                kind=REF_KIND_CHANNEL, ref=url.rsplit("/", 1)[-1]
            )
        if "fake/playlist/" in url:
            return SubscriptionRef(
                kind=REF_KIND_PLAYLIST, ref=url.rsplit("/", 1)[-1]
            )
        if "fake/video/" in url:
            return SubscriptionRef(
                kind=REF_KIND_VIDEO, ref=url.rsplit("/", 1)[-1]
            )
        return None

    _reset_subscription_registry_for_tests()
    p = _FakeProvider(resolve_url=_resolve)
    register_subscription_provider(p)
    yield p
    _reset_subscription_registry_for_tests()


@pytest.fixture()
def client(media_import_db, drive_path, fake_provider):
    """Mount the addon router in a fresh FastAPI app for HTTP testing.

    media_import_db owns the SessionLocal patching; drive_path makes
    config.get_drive_path return a writable tmp dir.
    """
    from addons.media_import.router import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


_UC = "UCabcdefghijklmnopqrstuv"


# ---- resolve ------------------------------------------------------


class TestResolveUrl:
    def test_video_url_classified_as_video(self, client) -> None:
        res = client.post(
            "/api/addons/media_import/subscriptions/resolve",
            json={"url": "https://fake/video/abc"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["kind"] == REF_KIND_VIDEO
        assert body["provider"] == "fakeyt"
        assert body["ref"] == "abc"

    def test_channel_url_classified_as_channel(self, client) -> None:
        res = client.post(
            "/api/addons/media_import/subscriptions/resolve",
            json={"url": f"https://fake/channel/{_UC}"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["kind"] == REF_KIND_CHANNEL
        assert body["ref"] == _UC

    def test_unknown_url_returns_unknown(self, client) -> None:
        res = client.post(
            "/api/addons/media_import/subscriptions/resolve",
            json={"url": "https://other.example/x"},
        )
        assert res.status_code == 200
        assert res.json()["kind"] == "unknown"

    def test_blank_url_returns_unknown(self, client) -> None:
        res = client.post(
            "/api/addons/media_import/subscriptions/resolve",
            json={"url": "   "},
        )
        assert res.status_code == 200
        assert res.json()["kind"] == "unknown"


# ---- create -------------------------------------------------------


class TestCreateSubscription:
    def test_creates_and_returns_id(self, client) -> None:
        res = client.post(
            "/api/addons/media_import/subscriptions",
            json={
                "url": f"https://fake/channel/{_UC}",
                "drive": "d",
                "folder_path": "yt",
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["id"] >= 1
        assert body["provider"] == "fakeyt"
        assert body["source_kind"] == REF_KIND_CHANNEL
        assert body["source_ref"] == _UC
        assert body["drive"] == "d"
        assert body["folder_path"] == "yt"
        assert body["is_enabled"] is True

    def test_rejects_video_url(self, client) -> None:
        res = client.post(
            "/api/addons/media_import/subscriptions",
            json={"url": "https://fake/video/abc", "drive": "d"},
        )
        assert res.status_code == 422

    def test_rejects_unknown_url(self, client) -> None:
        res = client.post(
            "/api/addons/media_import/subscriptions",
            json={"url": "https://other.example/x", "drive": "d"},
        )
        assert res.status_code == 422


# ---- list ---------------------------------------------------------


class TestListSubscriptions:
    def test_filters_by_drive(self, client, media_import_db) -> None:
        # Seed two rows, different drives.
        db = media_import_db()
        try:
            for sid, drive in [(1, "d"), (2, "other")]:
                db.execute(
                    text(
                        "INSERT INTO subscriptions "
                        "(id, provider, source_kind, source_ref, drive, "
                        " folder_path, is_enabled, cooldown_minutes, "
                        " include_no_transcript, created_at) "
                        "VALUES (:id, 'fakeyt', 'channel', :ref, :drive, "
                        " '', 1, 60, 0, '2026-05-01T00:00:00')"
                    ),
                    {"id": sid, "ref": _UC, "drive": drive},
                )
            db.commit()
        finally:
            db.close()

        res = client.get("/api/addons/media_import/subscriptions?drive=d")
        assert res.status_code == 200
        body = res.json()
        assert len(body) == 1
        assert body[0]["drive"] == "d"

    def test_drive_query_required(self, client) -> None:
        res = client.get("/api/addons/media_import/subscriptions")
        assert res.status_code == 422


# ---- delete -------------------------------------------------------


class TestDeleteSubscription:
    def test_returns_200_and_cascades(self, client, media_import_db) -> None:
        create = client.post(
            "/api/addons/media_import/subscriptions",
            json={"url": f"https://fake/channel/{_UC}", "drive": "d"},
        )
        sub_id = create.json()["id"]

        # Seed a video row to verify CASCADE.
        db = media_import_db()
        try:
            db.execute(
                text(
                    "INSERT INTO subscription_videos "
                    "(subscription_id, item_id, status, first_seen_at) "
                    "VALUES (:id, 'v', 'pending', '2026-05-01T00:00:00')"
                ),
                {"id": sub_id},
            )
            db.commit()
        finally:
            db.close()

        res = client.delete(
            f"/api/addons/media_import/subscriptions/{sub_id}"
        )
        assert res.status_code == 200

        db = media_import_db()
        try:
            cnt = db.execute(text("SELECT COUNT(*) FROM subscription_videos")).scalar()
        finally:
            db.close()
        assert cnt == 0

    def test_unknown_id_returns_404(self, client) -> None:
        res = client.delete("/api/addons/media_import/subscriptions/9999")
        assert res.status_code == 404


# ---- sync ---------------------------------------------------------


class TestSyncSubscription:
    def test_returns_summary(self, client, fake_provider: _FakeProvider) -> None:
        fake_provider.headers = [
            ItemHeader(item_id="vid_a", title="A"),
            ItemHeader(item_id="vid_b", title="B"),
        ]
        create = client.post(
            "/api/addons/media_import/subscriptions",
            json={"url": f"https://fake/channel/{_UC}", "drive": "d"},
        )
        sub_id = create.json()["id"]

        res = client.post(
            f"/api/addons/media_import/subscriptions/{sub_id}/sync"
        )
        assert res.status_code == 200
        body = res.json()
        assert body["added"] == 2
        assert body["reused"] == 0
        assert body["failed"] == 0
        assert body["total_new"] == 2

    def test_lock_conflict_returns_409(
        self, client, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription import manager as manager_mod

        create = client.post(
            "/api/addons/media_import/subscriptions",
            json={"url": f"https://fake/channel/{_UC}", "drive": "d"},
        )
        sub_id = create.json()["id"]

        # Pre-claim the in-flight slot so the route observes contention
        # without needing a real concurrent sync.
        async def _scenario() -> int:
            mgr = manager_mod.subscription_manager
            await mgr._claim_inflight(sub_id)
            try:
                res = client.post(
                    f"/api/addons/media_import/subscriptions/{sub_id}/sync"
                )
                return res.status_code
            finally:
                mgr._release_inflight(sub_id)

        status = asyncio.run(_scenario())
        assert status == 409

    def test_unknown_id_returns_404(self, client) -> None:
        res = client.post(
            "/api/addons/media_import/subscriptions/9999/sync"
        )
        assert res.status_code == 404


# ---- list videos --------------------------------------------------


class TestListVideos:
    def test_returns_videos_for_subscription(
        self, client, media_import_db, fake_provider: _FakeProvider
    ) -> None:
        create = client.post(
            "/api/addons/media_import/subscriptions",
            json={"url": f"https://fake/channel/{_UC}", "drive": "d"},
        )
        sub_id = create.json()["id"]

        db = media_import_db()
        try:
            for iid, status in [("v1", "imported"), ("v2", "failed")]:
                db.execute(
                    text(
                        "INSERT INTO subscription_videos "
                        "(subscription_id, item_id, status, first_seen_at) "
                        "VALUES (:sid, :iid, :status, '2026-05-01T00:00:00')"
                    ),
                    {"sid": sub_id, "iid": iid, "status": status},
                )
            db.commit()
        finally:
            db.close()

        res = client.get(
            f"/api/addons/media_import/subscriptions/{sub_id}/videos"
        )
        assert res.status_code == 200
        body = res.json()
        assert len(body) == 2
        statuses = {v["status"] for v in body}
        assert statuses == {"imported", "failed"}


# ---- drive auth ---------------------------------------------------


class TestDriveAccessControl:
    """In-process addons must enforce drive auth themselves; the host
    addon_proxy's X-Lit-Drive enforcement does not apply (hako
    RpRxLPvcuF79bwnRZRcDg).
    """

    def _lock_drive(self, monkeypatch, locked_drive: str, group: str) -> None:
        import app.config as config

        def _gate(name: str) -> str | None:
            return group if name == locked_drive else None

        monkeypatch.setattr(config, "get_drive_access_group", _gate)

    def test_create_on_locked_drive_returns_404(
        self, client, fake_provider: _FakeProvider, monkeypatch,
    ) -> None:
        self._lock_drive(monkeypatch, "secret", "vip")
        res = client.post(
            "/api/addons/media_import/subscriptions",
            json={"url": f"https://fake/channel/{_UC}", "drive": "secret"},
        )
        assert res.status_code == 404

    def test_list_on_locked_drive_returns_404(
        self, client, monkeypatch,
    ) -> None:
        self._lock_drive(monkeypatch, "secret", "vip")
        res = client.get(
            "/api/addons/media_import/subscriptions?drive=secret"
        )
        assert res.status_code == 404

    def test_per_id_endpoints_404_when_drive_locked_post_creation(
        self, client, fake_provider: _FakeProvider, media_import_db,
        monkeypatch,
    ) -> None:
        # Seed a subscription on drive "secret" while it is unlocked.
        db = media_import_db()
        try:
            db.execute(
                text(
                    "INSERT INTO subscriptions "
                    "(id, provider, source_kind, source_ref, drive, "
                    " folder_path, is_enabled, cooldown_minutes, "
                    " include_no_transcript, created_at) "
                    "VALUES (1, 'fakeyt', 'channel', :ref, 'secret', "
                    " '', 1, 60, 0, '2026-05-01T00:00:00')"
                ),
                {"ref": _UC},
            )
            db.commit()
        finally:
            db.close()

        # Now lock the drive — caller has no cookie → unlocked_groups=[].
        self._lock_drive(monkeypatch, "secret", "vip")

        endpoints = [
            ("DELETE", "/api/addons/media_import/subscriptions/1"),
            ("POST", "/api/addons/media_import/subscriptions/1/sync"),
            ("GET", "/api/addons/media_import/subscriptions/1/videos"),
            ("POST", "/api/addons/media_import/subscriptions/1/videos/x/retry"),
        ]
        for method, path in endpoints:
            res = client.request(method, path)
            assert res.status_code == 404, (
                f"{method} {path} should 404 on locked drive, "
                f"got {res.status_code}"
            )


# ---- path traversal ----------------------------------------------


class TestPathTraversalRejection:
    def test_dotdot_folder_path_does_not_escape_drive(
        self, client, fake_provider: _FakeProvider, drive_path,
    ) -> None:
        # Create with a malicious folder_path. The DB row should land
        # (manager.create itself doesn't write FS), but a sync attempt
        # must refuse rather than mkdir / write outside drive_path.
        fake_provider.headers = [ItemHeader(item_id="x", title="X")]
        fake_provider.items = {
            "x": ItemMetadata(
                item_id="x",
                canonical_url="https://fake/v/x",
                title="X",
            ),
        }
        create = client.post(
            "/api/addons/media_import/subscriptions",
            json={
                "url": f"https://fake/channel/{_UC}",
                "drive": "d",
                "folder_path": "../../escape",
            },
        )
        # Create itself doesn't touch the FS, so it is allowed.
        assert create.status_code == 200
        sub_id = create.json()["id"]

        res = client.post(
            f"/api/addons/media_import/subscriptions/{sub_id}/sync"
        )
        # The sync call ultimately raises in _allocate_loft_path. The
        # batch wraps individual item failures, so total_new=1 and
        # failed=1 — but no file should land outside drive_path.
        body = res.json()
        assert res.status_code == 200
        assert body["failed"] == 1
        assert body["added"] == 0

        parent = drive_path.parent
        # No directory or file landed in drive_path.parent or above.
        leaked = list(parent.glob("escape"))
        assert leaked == [], f"path traversal escaped: {leaked}"


# ---- retry --------------------------------------------------------


class TestRetryVideo:
    def test_retry_imports_failed_item(
        self, client, media_import_db, fake_provider: _FakeProvider, drive_path
    ) -> None:
        fake_provider.items = {
            "v1": ItemMetadata(
                item_id="v1",
                canonical_url="https://fake/v/v1",
                title="V One",
            ),
        }
        create = client.post(
            "/api/addons/media_import/subscriptions",
            json={"url": f"https://fake/channel/{_UC}", "drive": "d"},
        )
        sub_id = create.json()["id"]

        # Pre-seed a failed row for v1.
        db = media_import_db()
        try:
            db.execute(
                text(
                    "INSERT INTO subscription_videos "
                    "(subscription_id, item_id, status, first_seen_at) "
                    "VALUES (:sid, 'v1', 'failed', '2026-05-01T00:00:00')"
                ),
                {"sid": sub_id},
            )
            db.commit()
        finally:
            db.close()

        res = client.post(
            f"/api/addons/media_import/subscriptions/{sub_id}/videos/v1/retry"
        )
        assert res.status_code == 200

        # Row flipped to imported, .loft was created.
        assert (drive_path / "V One.loft").exists()
        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT status, file_id FROM subscription_videos "
                    "WHERE subscription_id = :sid AND item_id = 'v1'"
                ),
                {"sid": sub_id},
            ).mappings().first()
        finally:
            db.close()
        assert row["status"] == "imported"
        assert row["file_id"] is not None

    def test_unknown_video_returns_404(
        self, client, fake_provider: _FakeProvider
    ) -> None:
        create = client.post(
            "/api/addons/media_import/subscriptions",
            json={"url": f"https://fake/channel/{_UC}", "drive": "d"},
        )
        sub_id = create.json()["id"]

        res = client.post(
            f"/api/addons/media_import/subscriptions/{sub_id}/videos/missing/retry"
        )
        assert res.status_code == 404
