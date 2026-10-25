"""Acceptance tests for the items endpoint, the Retry endpoint and the
Needs attention counts.

SPEC-ADDON-027 (Retry acceptance, Needs attention predicate),
SPEC-ADDON-028 (Needs attention predicate), SPEC-ADDON-029 (``title`` and
``source_url`` on the items endpoint).
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from addons.media_import.subscription.registry import (
    REF_KIND_CHANNEL,
    ItemMetadata,
    SubscriptionRef,
    TranscriptResult,
    _reset_subscription_registry_for_tests,
    register_subscription_provider,
)

_UC = "UCabcdefghijklmnopqrstuv"


@dataclass
class _FakeProvider:
    name: str = "fakeyt"
    inter_item_delay_seconds: float = 0.0
    fetch_calls: list[str] = field(default_factory=list)

    def resolve_ref(self, url):
        if "fake/channel/" in url:
            return SubscriptionRef(kind=REF_KIND_CHANNEL, ref=url.rsplit("/", 1)[-1])
        return None

    def list_items(self, ref, limit=None):
        return []

    def fetch_item(self, ref, item_id):
        self.fetch_calls.append(item_id)
        return ItemMetadata(
            item_id=item_id, canonical_url=f"https://fake/v/{item_id}",
            title=f"T {item_id}",
        )

    def fetch_transcript(self, ref, item_id, language=None):
        return TranscriptResult(error_kind="no_transcript")

    def build_loft_content(self, item):
        return {"provider": self.name, "url": item.canonical_url}

    def fetch_source_metadata(self, ref):
        return None

    def fetch_item_title(self, *args):
        return None

    def item_url(self, *args):
        return f"https://fake/watch/{args[-1]}"


@pytest.fixture()
def fake_provider():
    _reset_subscription_registry_for_tests()
    p = _FakeProvider()
    register_subscription_provider(p)
    from addons.media_import.subscription.providers.youtube import YouTubeProvider

    register_subscription_provider(YouTubeProvider())
    yield p
    _reset_subscription_registry_for_tests()


@pytest.fixture()
def client(media_import_db, drive_path, fake_provider):
    from addons.media_import.router import router
    from addons.media_import.subscription.worker import subscription_worker

    @asynccontextmanager
    async def lifespan(_app):
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
        c.headers["X-Lit-Drive"] = "d"
        yield c


def _drain_worker(timeout: float = 5.0) -> None:
    from addons.media_import.subscription.worker import subscription_worker

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not subscription_worker.running_ids and subscription_worker._queue.empty():
            time.sleep(0.05)
            return
        time.sleep(0.02)
    raise TimeoutError("subscription worker did not drain in time")


def _create(client, ref: str = _UC) -> int:
    res = client.post(
        "/api/addons/media_import/subscriptions",
        json={"url": f"https://fake/channel/{ref}", "drive": "d"},
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _insert_youtube_subscription(session_factory) -> int:
    db = session_factory()
    try:
        db.execute(
            text(
                "INSERT INTO subscriptions "
                "(provider, source_kind, source_ref, drive, folder_path, "
                " is_enabled, cooldown_minutes, include_no_transcript, created_at) "
                "VALUES ('youtube', 'channel', :ref, 'd', '', 1, 60, 0, "
                " '2026-05-01T00:00:00')"
            ),
            {"ref": _UC},
        )
        sub_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
        db.commit()
    finally:
        db.close()
    return sub_id


def _seed(session_factory, sub_id, item_id, status, error_kind=None, *,
          title=None, published_at=None, file_id=None, fail_count=0):
    db = session_factory()
    try:
        db.execute(
            text(
                "INSERT INTO subscription_videos "
                "(subscription_id, item_id, status, error_kind, file_id, "
                " first_seen_at, last_attempted_at, fail_count, title, published_at) "
                "VALUES (:sid, :iid, :s, :e, :fid, '2026-05-01T00:00:00', "
                " '2026-05-02T00:00:00', :fc, :t, :p)"
            ),
            {"sid": sub_id, "iid": item_id, "s": status, "e": error_kind,
             "fid": file_id, "fc": fail_count, "t": title, "p": published_at},
        )
        db.commit()
    finally:
        db.close()


def _row(session_factory, sub_id, item_id):
    db = session_factory()
    try:
        return db.execute(
            text(
                "SELECT status, error_kind, fail_count, last_attempted_at, file_id "
                "FROM subscription_videos WHERE subscription_id = :s AND item_id = :i"
            ),
            {"s": sub_id, "i": item_id},
        ).mappings().first()
    finally:
        db.close()


class TestRetryEndpointAcceptance:
    """SPEC-ADDON-027 / I2."""

    @pytest.mark.parametrize(
        "status, error_kind",
        [("imported", None), ("imported", "no_transcript"), ("failed", "dismissed")],
        ids=["imported", "imported_caption_kind", "skipped"],
    )
    def test_spec_addon_027_retry_on_imported_or_skipped_row_is_409(
        self, client, media_import_db, fake_provider, status, error_kind
    ) -> None:
        sub_id = _create(client)
        _seed(media_import_db, sub_id, "v1", status, error_kind)

        res = client.post(
            f"/api/addons/media_import/subscriptions/{sub_id}/videos/v1/retry"
        )
        _drain_worker()

        assert res.status_code == 409
        assert res.json()["detail"] == "video is not retryable"
        assert fake_provider.fetch_calls == []
        row = _row(media_import_db, sub_id, "v1")
        assert (row["status"], row["error_kind"], row["last_attempted_at"]) == (
            status, error_kind, "2026-05-02T00:00:00",
        )

    @pytest.mark.parametrize(
        "error_kind",
        ["permanent", "retry_pending", "upcoming"],
    )
    def test_spec_addon_027_retry_accepted_for_failed_retryable_rows(
        self, client, media_import_db, fake_provider, drive_path, error_kind
    ) -> None:
        sub_id = _create(client)
        _seed(media_import_db, sub_id, "v1", "failed", error_kind)

        res = client.post(
            f"/api/addons/media_import/subscriptions/{sub_id}/videos/v1/retry"
        )
        assert res.status_code == 200
        assert res.json() == {"status": "queued"}
        _drain_worker()

        assert fake_provider.fetch_calls == ["v1"]
        row = _row(media_import_db, sub_id, "v1")
        assert row["status"] == "imported"
        assert row["fail_count"] == 0


class TestItemsEndpointTitlesAndSourceUrl:
    """SPEC-ADDON-029 / I8."""

    def test_spec_addon_029_title_fallback_and_source_url_for_every_row(
        self, client, media_import_db
    ) -> None:
        sub_id = _insert_youtube_subscription(media_import_db)
        db = media_import_db()
        try:
            from app.models import File as FileModel

            db.add(
                FileModel(
                    id="f1", filename="Name From File.loft", title="Name From File",
                    drive="d", folder_path="", file_path="Name From File.loft",
                    file_size=0, file_type="other",
                    mime_type="application/vnd.litloft.loft+json",
                )
            )
            db.flush()
            db.execute(
                text(
                    "INSERT INTO loft_metadata (file_id, provider, provider_item_id, "
                    " url, published_at) VALUES ('f1', 'youtube', 'aaaaaaaaaaa', "
                    " 'https://www.youtube.com/watch?v=aaaaaaaaaaa', '2026-04-01')"
                )
            )
            db.commit()
        finally:
            db.close()
        _seed(media_import_db, sub_id, "aaaaaaaaaaa", "imported", file_id="f1",
              title="Stored A", published_at="20260301")
        # An imported row whose file was deleted (FK SET NULL).
        _seed(media_import_db, sub_id, "bbbbbbbbbbb", "imported", title="Stored B")
        _seed(media_import_db, sub_id, "ccccccccccc", "failed", "permanent",
              title="Members only", published_at="20260415")
        _seed(media_import_db, sub_id, "ddddddddddd", "failed", "retry_pending")

        res = client.get(f"/api/addons/media_import/subscriptions/{sub_id}/videos")

        assert res.status_code == 200
        by_id = {v["item_id"]: v for v in res.json()}
        assert by_id["aaaaaaaaaaa"]["title"] == "Name From File"
        assert by_id["bbbbbbbbbbb"]["title"] == "Stored B"
        assert by_id["ccccccccccc"]["title"] == "Members only"
        assert by_id["ddddddddddd"]["title"] is None
        for iid, v in by_id.items():
            assert v["source_url"] == f"https://www.youtube.com/watch?v={iid}"
        # published_at comes from loft_metadata only.
        assert by_id["aaaaaaaaaaa"]["published_at"] == "2026-04-01"
        assert by_id["ccccccccccc"]["published_at"] is None

    def test_spec_addon_029_file_name_wins_over_stored_title(
        self, client, media_import_db
    ) -> None:
        sub_id = _insert_youtube_subscription(media_import_db)
        db = media_import_db()
        try:
            from app.models import File as FileModel

            db.add(
                FileModel(
                    id="f2", filename="Renamed by user.loft", title="Renamed by user",
                    drive="d", folder_path="", file_path="Renamed by user.loft",
                    file_size=0, file_type="other",
                    mime_type="application/vnd.litloft.loft+json",
                )
            )
            db.commit()
        finally:
            db.close()
        _seed(media_import_db, sub_id, "eeeeeeeeeee", "imported", file_id="f2",
              title="Listing title")

        body = client.get(
            f"/api/addons/media_import/subscriptions/{sub_id}/videos"
        ).json()

        assert body[0]["title"] == "Renamed by user"


class TestNeedsAttentionSummary:
    """SPEC-ADDON-027, SPEC-ADDON-028 / I6: one predicate for the summary."""

    def test_spec_addon_028_summary_counts_only_needs_attention_rows(
        self, client, media_import_db
    ) -> None:
        mixed = _create(client, "UCmixedaaaaaaaaaaaaaaaaa")
        quiet = _create(client, "UCquietaaaaaaaaaaaaaaaaa")
        for iid, status, ek in (
            ("i1", "imported", "rate_limited"),
            ("i2", "imported", None),
            ("f1", "failed", "retry_pending"),
            ("f2", "failed", "upcoming"),
            ("f3", "failed", "permanent"),
            ("f4", "failed", "dismissed"),
            ("a1", "failed", "fetch_failed"),
            ("a2", "failed", None),
            ("a3", "failed", "path_conflict"),
            ("a4", "failed", "some_future_kind"),
            ("a5", "failed", "rate_limited"),
        ):
            _seed(media_import_db, mixed, iid, status, ek)
        for iid, ek in (("q1", "retry_pending"), ("q2", "upcoming"), ("q3", "permanent")):
            _seed(media_import_db, quiet, iid, "failed", ek)

        body = client.get(
            "/api/addons/media_import/subscriptions/summary?drive=d"
        ).json()

        assert body["failed_count"] == 5
        assert body["attention"] == 1
        assert body["healthy"] == 1
        assert body["imported_count"] == 2
