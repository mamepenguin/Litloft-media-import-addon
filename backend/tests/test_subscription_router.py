"""HTTP-level tests for the subscription endpoints.

The provider seam is faked end-to-end so these tests exercise wiring
(schema → manager → worker → DB → response) without yt-dlp / network.
Manager internals are covered by ``test_subscription_manager.py``;
worker mechanics by ``test_subscription_worker.py``.

The ``client`` fixture starts the SubscriptionWorker singleton via
FastAPI lifespan so route handlers that enqueue jobs trigger real
work. ``_drain_worker`` blocks the test thread until the worker idles.
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
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
    """Mount the addon router with worker lifespan for HTTP testing."""
    from addons.media_import.router import router
    from addons.media_import.subscription.worker import subscription_worker

    @asynccontextmanager
    async def lifespan(_app):
        # Reset singleton state per test so queue/in-flight don't bleed.
        subscription_worker._queue = asyncio.Queue()
        subscription_worker._running_ids.clear()
        subscription_worker._queued_ids.clear()
        subscription_worker._idle_event.set()
        await subscription_worker.start()
        try:
            yield
        finally:
            await subscription_worker.stop()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    with TestClient(app) as c:
        yield c


def _drain_worker(timeout: float = 5.0) -> None:
    """Poll-wait for the SubscriptionWorker singleton to become idle.

    The worker runs on the TestClient's portal event loop; we cannot
    await wait_idle() from sync test code, but its state (running_ids,
    queue.empty()) is safe to read from any thread.
    """
    from addons.media_import.subscription.worker import subscription_worker

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (
            not subscription_worker.running_ids
            and subscription_worker._queue.empty()
        ):
            # Give the loop one final tick so task_done() and the idle
            # event update before we return.
            time.sleep(0.05)
            return
        time.sleep(0.02)
    raise TimeoutError("subscription worker did not drain in time")


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

    def test_running_flag_reflects_worker_state(
        self, client, media_import_db
    ) -> None:
        from addons.media_import.subscription.worker import subscription_worker

        # Seed two rows with distinct source_ref to satisfy the UNIQUE
        # (provider, source_kind, source_ref, drive, folder_path) constraint.
        db = media_import_db()
        try:
            for sid, ref in [(1, _UC), (2, "UC2222222222222222222222")]:
                db.execute(
                    text(
                        "INSERT INTO subscriptions "
                        "(id, provider, source_kind, source_ref, drive, "
                        " folder_path, is_enabled, cooldown_minutes, "
                        " include_no_transcript, created_at) "
                        "VALUES (:id, 'fakeyt', 'channel', :ref, 'd', "
                        " '', 1, 60, 0, '2026-05-01T00:00:00')"
                    ),
                    {"id": sid, "ref": ref},
                )
            db.commit()
        finally:
            db.close()

        # Inject sub_id=2 directly into worker state for the snapshot.
        subscription_worker._running_ids.add(2)
        try:
            res = client.get("/api/addons/media_import/subscriptions?drive=d")
        finally:
            subscription_worker._running_ids.discard(2)

        assert res.status_code == 200
        body = res.json()
        by_id = {s["id"]: s for s in body}
        assert by_id[1]["running"] is False
        assert by_id[2]["running"] is True


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
    def test_returns_queued_then_drains_to_files(
        self, client, fake_provider: _FakeProvider, drive_path
    ) -> None:
        fake_provider.headers = [
            ItemHeader(item_id="vid_a", title="A"),
            ItemHeader(item_id="vid_b", title="B"),
        ]
        fake_provider.items = {
            "vid_a": ItemMetadata(
                item_id="vid_a", canonical_url="https://fake/v/a", title="A",
            ),
            "vid_b": ItemMetadata(
                item_id="vid_b", canonical_url="https://fake/v/b", title="B",
            ),
        }
        create = client.post(
            "/api/addons/media_import/subscriptions",
            json={"url": f"https://fake/channel/{_UC}", "drive": "d"},
        )
        sub_id = create.json()["id"]

        res = client.post(
            f"/api/addons/media_import/subscriptions/{sub_id}/sync"
        )
        assert res.status_code == 200
        assert res.json() == {"status": "queued"}

        _drain_worker()
        # Files materialized after worker completes.
        assert (drive_path / "A.loft").exists()
        assert (drive_path / "B.loft").exists()

    def test_duplicate_call_returns_already_queued(
        self, client, fake_provider: _FakeProvider
    ) -> None:
        """Two enqueues for the same id while the first is still
        in-flight collapse — the second responds 200 ``already_queued``
        instead of running again. Replaces the old 409 contract.
        """
        from addons.media_import.subscription.worker import subscription_worker

        # Long-running fake so the second call lands while the first is
        # still in the worker.
        fake_provider.headers = [ItemHeader(item_id="x", title="X")]
        fake_provider.items = {
            "x": ItemMetadata(
                item_id="x", canonical_url="https://fake/v/x", title="X",
            ),
        }

        original = fake_provider.fetch_item

        def _slow(ref, item_id):
            time.sleep(0.3)
            return original(ref, item_id)

        fake_provider.fetch_item = _slow  # type: ignore[method-assign]

        create = client.post(
            "/api/addons/media_import/subscriptions",
            json={"url": f"https://fake/channel/{_UC}", "drive": "d"},
        )
        sub_id = create.json()["id"]

        first = client.post(
            f"/api/addons/media_import/subscriptions/{sub_id}/sync"
        )
        assert first.status_code == 200
        assert first.json() == {"status": "queued"}

        # Wait for the worker to actually pick up job #1.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if sub_id in subscription_worker.running_ids:
                break
            time.sleep(0.02)

        second = client.post(
            f"/api/addons/media_import/subscriptions/{sub_id}/sync"
        )
        assert second.status_code == 200
        assert second.json() == {"status": "already_queued"}

        _drain_worker()

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
        # Worker accepts the job; the per-item failure happens inside
        # _allocate_loft_path during sync execution.
        assert res.status_code == 200
        assert res.json() == {"status": "queued"}

        _drain_worker()

        parent = drive_path.parent
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
        assert res.json() == {"status": "queued"}

        _drain_worker()

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
        """Eager existence check in the route preserves the 404 contract
        (the worker's SubscriptionNotFound would only surface via WS,
        too late for synchronous click feedback).
        """
        create = client.post(
            "/api/addons/media_import/subscriptions",
            json={"url": f"https://fake/channel/{_UC}", "drive": "d"},
        )
        sub_id = create.json()["id"]

        res = client.post(
            f"/api/addons/media_import/subscriptions/{sub_id}/videos/missing/retry"
        )
        assert res.status_code == 404
