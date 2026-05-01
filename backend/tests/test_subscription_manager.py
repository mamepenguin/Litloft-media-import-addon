"""Tests for SubscriptionManager.

The provider seam is faked end-to-end so these tests do not rely on the
YouTubeProvider internals — that contract is already covered by
``test_youtube_provider*.py``. Each test exercises a single behavior of
the manager: create / delete / sync flow (full + single-item), dedup,
transcript handling, last-sync bookkeeping.

Concurrency / serialization is tested separately at the worker layer
(``test_subscription_worker.py``); the manager is exercised here with
direct ``_sync_blocking`` calls.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable
from unittest.mock import patch

import pytest
from sqlalchemy import text

from addons.media_import.subscription.registry import (
    ERROR_NO_TRANSCRIPT,
    ERROR_PERMANENT,
    ERROR_RATE_LIMITED,
    REF_KIND_CHANNEL,
    REF_KIND_PLAYLIST,
    REF_KIND_VIDEO,
    ItemHeader,
    ItemMetadata,
    SubscriptionRef,
    TranscriptResult,
    register_subscription_provider,
    _reset_subscription_registry_for_tests,
)


# ---- Fake provider ------------------------------------------------


@dataclass
class _FakeProvider:
    """Implements the SubscriptionProvider protocol for manager tests."""

    name: str = "fakeyt"
    inter_item_delay_seconds: float = 0.0
    headers: list[ItemHeader] = field(default_factory=list)
    items: dict[str, ItemMetadata] = field(default_factory=dict)
    transcripts: dict[str, TranscriptResult] = field(default_factory=dict)
    resolve_url: Callable[[str], SubscriptionRef | None] | None = None

    def resolve_ref(self, url: str) -> SubscriptionRef | None:
        if self.resolve_url:
            return self.resolve_url(url)
        return None

    def list_items(
        self, ref: SubscriptionRef, limit: int | None = None
    ) -> list[ItemHeader]:
        out = list(self.headers)
        return out if limit is None else out[:limit]

    def fetch_item(
        self, ref: SubscriptionRef, item_id: str
    ) -> ItemMetadata:
        if item_id in self.items:
            return self.items[item_id]
        return ItemMetadata(
            item_id=item_id,
            canonical_url=f"https://fake/{item_id}",
            title=f"Title {item_id}",
        )

    def fetch_transcript(
        self,
        ref: SubscriptionRef,
        item_id: str,
        language: str | None = None,
    ) -> TranscriptResult:
        return self.transcripts.get(
            item_id, TranscriptResult(error_kind=ERROR_NO_TRANSCRIPT)
        )

    def build_loft_content(self, item: ItemMetadata) -> dict:
        return {"provider": self.name, "url": item.canonical_url}


@pytest.fixture()
def fake_provider() -> _FakeProvider:
    """Register a fake provider in the subscription registry."""

    def _resolve(url: str) -> SubscriptionRef | None:
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


# ---- create / delete ---------------------------------------------


class TestCreate:
    def test_canonical_uc_channel_persists_as_is(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        mgr = SubscriptionManager()
        sub_id = mgr.create(
            url="https://fake/channel/UCabcdefghijklmnopqrstuv",
            drive="d", folder_path="folder",
        )
        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT provider, source_kind, source_ref, drive, "
                    " folder_path, is_enabled, cooldown_minutes "
                    "FROM subscriptions WHERE id = :id"
                ),
                {"id": sub_id},
            ).mappings().first()
        finally:
            db.close()

        assert row is not None
        assert row["provider"] == "fakeyt"
        assert row["source_kind"] == REF_KIND_CHANNEL
        assert row["source_ref"] == "UCabcdefghijklmnopqrstuv"
        assert row["drive"] == "d"
        assert row["folder_path"] == "folder"
        assert row["is_enabled"] == 1
        assert row["cooldown_minutes"] == 60

    def test_handle_is_canonicalized_via_provider_hook(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription import manager as manager_mod

        with patch.object(
            manager_mod,
            "_resolve_channel_id_via_yt_dlp",
            return_value="UCcanonicalAAAAAAAAAAAAA",
        ):
            sub_id = manager_mod.SubscriptionManager().create(
                url="https://fake/channel/@handle",
                drive="d", folder_path="",
            )

        db = media_import_db()
        try:
            ref = db.execute(
                text("SELECT source_ref FROM subscriptions WHERE id = :id"),
                {"id": sub_id},
            ).scalar()
        finally:
            db.close()
        assert ref == "UCcanonicalAAAAAAAAAAAAA"

    def test_rejects_video_url(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        with pytest.raises(ValueError):
            SubscriptionManager().create(
                url="https://fake/video/abc", drive="d",
            )

    def test_rejects_unknown_url(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        with pytest.raises(ValueError):
            SubscriptionManager().create(
                url="https://elsewhere.example/whatever", drive="d",
            )


class TestDelete:
    def test_delete_cascades_subscription_videos(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        mgr = SubscriptionManager()
        sub_id = mgr.create(
            url="https://fake/channel/UCabcdefghijklmnopqrstuv",
            drive="d", folder_path="",
        )
        db = media_import_db()
        try:
            db.execute(
                text(
                    "INSERT INTO subscription_videos "
                    "(subscription_id, item_id, status, first_seen_at) "
                    "VALUES (:id, 'vid', 'pending', '2026-05-01T00:00:00')"
                ),
                {"id": sub_id},
            )
            db.commit()
        finally:
            db.close()

        mgr.delete(sub_id)

        db = media_import_db()
        try:
            cnt = db.execute(
                text("SELECT COUNT(*) FROM subscription_videos")
            ).scalar()
        finally:
            db.close()
        assert cnt == 0


# ---- sync flow ----------------------------------------------------


def _create_subscription(mgr, drive: str, folder: str = "") -> int:
    return mgr.create(
        url="https://fake/channel/UCabcdefghijklmnopqrstuv",
        drive=drive, folder_path=folder,
    )


class TestSyncCreatesNewItems:
    def test_creates_loft_files_and_db_rows(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        fake_provider.headers = [
            ItemHeader(item_id="vid_a", title="A"),
            ItemHeader(item_id="vid_b", title="B"),
        ]
        fake_provider.items = {
            "vid_a": ItemMetadata(
                item_id="vid_a",
                canonical_url="https://fake/v/vid_a",
                title="A title",
                description="A body",
                channel="Author",
                has_captions=True,
                language="ja",
            ),
            "vid_b": ItemMetadata(
                item_id="vid_b",
                canonical_url="https://fake/v/vid_b",
                title="B title",
                has_captions=False,
            ),
        }
        fake_provider.transcripts = {
            "vid_a": TranscriptResult(
                vtt_text="WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhi\n",
                language="ja",
            ),
        }

        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d", folder="yt")

        result = mgr._sync_blocking(sub_id)
        assert result["added"] == 2
        assert result["reused"] == 0
        assert result["failed"] == 0

        # Files written
        assert (drive_path / "yt" / "A title.loft").exists()
        assert (drive_path / "yt" / "B title.loft").exists()
        # Transcript saved only for vid_a
        assert (drive_path / "yt" / "A title.vtt").exists()
        assert not (drive_path / "yt" / "B title.vtt").exists()

        # DB state
        db = media_import_db()
        try:
            files = db.execute(
                text("SELECT id, file_path FROM files ORDER BY file_path")
            ).fetchall()
            videos = db.execute(
                text(
                    "SELECT item_id, status, file_id, error_kind "
                    "FROM subscription_videos "
                    "WHERE subscription_id = :id ORDER BY item_id"
                ),
                {"id": sub_id},
            ).mappings().all()
            metas = db.execute(
                text(
                    "SELECT file_id, provider, provider_item_id, "
                    " has_captions, captions_downloaded, caption_error_kind "
                    "FROM loft_metadata ORDER BY provider_item_id"
                )
            ).mappings().all()
        finally:
            db.close()

        assert len(files) == 2
        assert len(videos) == 2
        assert {v["item_id"] for v in videos} == {"vid_a", "vid_b"}
        for v in videos:
            assert v["status"] == "imported"
            assert v["file_id"] is not None
        # vid_a has transcript, vid_b reports no_transcript via captions=False
        # so we never call fetch_transcript for vid_b.
        by_item = {v["item_id"]: v for v in videos}
        assert by_item["vid_a"]["error_kind"] is None
        assert by_item["vid_b"]["error_kind"] is None  # not attempted

        assert {m["provider_item_id"] for m in metas} == {"vid_a", "vid_b"}
        by_iid = {m["provider_item_id"]: m for m in metas}
        assert by_iid["vid_a"]["captions_downloaded"]
        assert not by_iid["vid_b"]["captions_downloaded"]


class TestSyncDedup:
    def test_existing_loft_in_same_folder_is_reused(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        fake_provider.headers = [ItemHeader(item_id="shared", title="Shared")]
        fake_provider.items = {
            "shared": ItemMetadata(
                item_id="shared",
                canonical_url="https://fake/v/shared",
                title="Shared",
            ),
        }

        # Pre-seed: a .loft already exists for (d, yt, fakeyt, shared)
        existing_loft = drive_path / "yt" / "Shared.loft"
        existing_loft.parent.mkdir(parents=True, exist_ok=True)
        existing_loft.write_text(
            json.dumps({"provider": "fakeyt", "url": "https://fake/v/shared"}),
            encoding="utf-8",
        )

        db = media_import_db()
        try:
            db.execute(
                text(
                    "INSERT INTO files (id, filename, title, description, drive, "
                    " folder_path, file_path, file_size, file_type, mime_type, "
                    " created_at, updated_at) "
                    "VALUES ('preexisting1', 'Shared.loft', 'Shared', '', 'd', "
                    " 'yt', 'yt/Shared.loft', 100, 'other', "
                    " 'application/x-loft', '2026-04-01T00:00:00', "
                    " '2026-04-01T00:00:00')"
                )
            )
            db.execute(
                text(
                    "INSERT INTO loft_metadata "
                    "(file_id, provider, provider_item_id, url) "
                    "VALUES ('preexisting1', 'fakeyt', 'shared', "
                    " 'https://fake/v/shared')"
                )
            )
            db.commit()
        finally:
            db.close()

        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d", folder="yt")
        result = mgr._sync_blocking(sub_id)

        assert result["reused"] == 1
        assert result["added"] == 0

        db = media_import_db()
        try:
            file_count = db.execute(text("SELECT COUNT(*) FROM files")).scalar()
            video_row = db.execute(
                text(
                    "SELECT file_id FROM subscription_videos "
                    "WHERE subscription_id = :id AND item_id = 'shared'"
                ),
                {"id": sub_id},
            ).first()
        finally:
            db.close()

        # No new file row introduced.
        assert file_count == 1
        assert video_row[0] == "preexisting1"


class TestSyncIdempotent:
    def test_second_pass_with_no_new_items_returns_zero_added(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        fake_provider.headers = [ItemHeader(item_id="only", title="Only")]
        fake_provider.items = {
            "only": ItemMetadata(
                item_id="only", canonical_url="https://fake/v/only",
                title="Only",
            ),
        }

        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")
        first = mgr._sync_blocking(sub_id)
        second = mgr._sync_blocking(sub_id)
        assert first["added"] == 1
        assert second["added"] == 0
        assert second["reused"] == 0


class TestSyncTranscriptFailure:
    def test_rate_limited_keeps_loft_and_records_error(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        fake_provider.headers = [ItemHeader(item_id="x", title="X")]
        fake_provider.items = {
            "x": ItemMetadata(
                item_id="x", canonical_url="https://fake/v/x",
                title="X", has_captions=True,
            ),
        }
        fake_provider.transcripts = {
            "x": TranscriptResult(error_kind=ERROR_RATE_LIMITED),
        }

        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")
        result = mgr._sync_blocking(sub_id)

        assert result["added"] == 1

        # .loft still present
        assert (drive_path / "X.loft").exists()
        # No .vtt
        assert not (drive_path / "X.vtt").exists()

        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT caption_error_kind, captions_downloaded "
                    "FROM loft_metadata WHERE provider_item_id = 'x'"
                )
            ).mappings().first()
        finally:
            db.close()
        assert row["caption_error_kind"] == ERROR_RATE_LIMITED
        assert not row["captions_downloaded"]


class TestSyncLastSyncedAt:
    def test_last_synced_at_set_after_pass(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")
        mgr._sync_blocking(sub_id)

        db = media_import_db()
        try:
            ts = db.execute(
                text("SELECT last_synced_at FROM subscriptions WHERE id = :id"),
                {"id": sub_id},
            ).scalar()
        finally:
            db.close()
        assert ts is not None


class TestSyncSingleItemRetry:
    def test_retry_existing_failed_row_imports_and_marks_imported(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        """``_sync_blocking`` with ``item_id`` re-attempts a single video.

        Mirrors the old ``retry_item`` shape: the row must already exist
        in ``subscription_videos`` (raises SubscriptionNotFound otherwise),
        and the result is a one-item summary. dedup against existing
        loft files still applies.
        """
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
            SubscriptionNotFound,
        )

        fake_provider.headers = [ItemHeader(item_id="vid", title="Vid")]
        fake_provider.items = {
            "vid": ItemMetadata(
                item_id="vid", canonical_url="https://fake/v/vid",
                title="Vid", has_captions=False,
            ),
        }

        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")

        # Seed a failed row to retry.
        db = media_import_db()
        try:
            db.execute(
                text(
                    "INSERT INTO subscription_videos "
                    "(subscription_id, item_id, status, first_seen_at) "
                    "VALUES (:sid, 'vid', 'failed', '2026-05-01T00:00:00')"
                ),
                {"sid": sub_id},
            )
            db.commit()
        finally:
            db.close()

        result = mgr._sync_blocking(sub_id, item_id="vid")
        assert result == {"added": 1, "reused": 0, "failed": 0, "total_new": 1}
        assert (drive_path / "Vid.loft").exists()

        # Retry on a non-existent row must raise SubscriptionNotFound.
        with pytest.raises(SubscriptionNotFound):
            mgr._sync_blocking(sub_id, item_id="never_seen")
