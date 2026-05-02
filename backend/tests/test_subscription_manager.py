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
    SourceMetadata,
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
    source_metadata: SourceMetadata | None = None
    source_metadata_calls: list[SubscriptionRef] = field(default_factory=list)

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

    def fetch_source_metadata(
        self, ref: SubscriptionRef
    ) -> SourceMetadata | None:
        self.source_metadata_calls.append(ref)
        return self.source_metadata


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


class TestSyncThumbnail:
    """Subscription-imported .loft files must populate File.thumbnail_path
    via the shared _save_loft_thumbnail helper, mirroring the /link path
    (hako IpF19kUI3OKoY_ps7iKg1). Without this, grid views fall back to
    the placeholder image even when the provider returned a thumbnail URL.
    """

    def test_thumbnail_url_populates_file_thumbnail_path(
        self, media_import_db, drive_path, fake_provider: _FakeProvider,
        monkeypatch,
    ) -> None:
        from addons.media_import import service
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        fake_provider.headers = [ItemHeader(item_id="vt", title="WithThumb")]
        fake_provider.items = {
            "vt": ItemMetadata(
                item_id="vt",
                canonical_url="https://fake/v/vt",
                title="WithThumb",
                thumbnail_url="https://fake.cdn/vt.jpg",
            ),
        }

        # Stub the network call but materialize a file so the helper's
        # commit path is exercised end-to-end.
        def _fake_dl(url, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake jpg")
            return True

        monkeypatch.setattr(service, "_download_thumbnail_sync", _fake_dl)

        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d", folder="yt")
        result = mgr._sync_blocking(sub_id)
        assert result["added"] == 1

        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT thumbnail_path FROM files "
                    "WHERE filename = 'WithThumb.loft'"
                )
            ).first()
        finally:
            db.close()
        assert row is not None
        assert row[0] == "d/yt/WithThumb.jpg"

    def test_no_thumbnail_url_leaves_thumbnail_path_null(
        self, media_import_db, drive_path, fake_provider: _FakeProvider,
        monkeypatch,
    ) -> None:
        from addons.media_import import service
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        fake_provider.headers = [ItemHeader(item_id="nt", title="NoThumb")]
        fake_provider.items = {
            "nt": ItemMetadata(
                item_id="nt",
                canonical_url="https://fake/v/nt",
                title="NoThumb",
                # thumbnail_url left as None
            ),
        }

        called: list = []
        monkeypatch.setattr(
            service, "_download_thumbnail_sync",
            lambda *a, **kw: called.append(True) or True,
        )

        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d", folder="yt")
        mgr._sync_blocking(sub_id)

        assert called == []  # helper short-circuits before download
        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT thumbnail_path FROM files "
                    "WHERE filename = 'NoThumb.loft'"
                )
            ).first()
        finally:
            db.close()
        assert row is not None
        assert row[0] is None

    def test_filename_uniquifier_used_for_thumb_path(
        self, media_import_db, drive_path, fake_provider: _FakeProvider,
        monkeypatch,
    ) -> None:
        """When _allocate_loft_path appends '(1)' for a name collision,
        the thumbnail path must use the same uniquified stem so the core
        thumbnail endpoint can resolve filename → thumbnail.
        """
        from addons.media_import import service
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        # Pre-seed an unrelated .loft at the target name to force
        # _allocate_loft_path into the "(1)" branch.
        (drive_path / "yt").mkdir(parents=True, exist_ok=True)
        (drive_path / "yt" / "Dup.loft").write_text("{}", encoding="utf-8")

        fake_provider.headers = [ItemHeader(item_id="d2", title="Dup")]
        fake_provider.items = {
            "d2": ItemMetadata(
                item_id="d2",
                canonical_url="https://fake/v/d2",
                title="Dup",
                thumbnail_url="https://fake.cdn/d2.jpg",
            ),
        }
        monkeypatch.setattr(
            service, "_download_thumbnail_sync",
            lambda url, dest: True,
        )

        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d", folder="yt")
        mgr._sync_blocking(sub_id)

        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT filename, thumbnail_path FROM files "
                    "WHERE folder_path = 'yt' "
                    "  AND filename LIKE 'Dup%' "
                    "  AND filename != 'Dup.loft'"
                )
            ).first()
        finally:
            db.close()
        assert row is not None
        assert row[0] == "Dup (1).loft"
        # Thumb stem must match the uniquified filename.
        assert row[1] == "d/yt/Dup (1).jpg"


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


class TestCronEligibility:
    """``list_eligible_for_cron`` is the only entry point the scheduler uses.

    It encodes the cron-due predicate at SQL/Python boundary; tests pin
    each branch of the predicate so the scheduler can stay a thin
    pump-and-broadcast layer.
    """

    def test_null_last_synced_is_eligible(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from datetime import UTC, datetime

        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")
        # Fresh subscription has last_synced_at = NULL.
        assert mgr.list_eligible_for_cron(datetime.now(UTC)) == [sub_id]

    def test_disabled_excluded(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from datetime import UTC, datetime

        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")
        db = media_import_db()
        try:
            db.execute(
                text(
                    "UPDATE subscriptions SET is_enabled = 0 WHERE id = :id"
                ),
                {"id": sub_id},
            )
            db.commit()
        finally:
            db.close()

        assert mgr.list_eligible_for_cron(datetime.now(UTC)) == []

    def test_within_cooldown_excluded(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from datetime import UTC, datetime, timedelta

        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")

        now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        # Last synced 30 minutes ago; default cooldown_minutes=60.
        recent = (now - timedelta(minutes=30)).isoformat()
        db = media_import_db()
        try:
            db.execute(
                text(
                    "UPDATE subscriptions SET last_synced_at = :ts "
                    "WHERE id = :id"
                ),
                {"ts": recent, "id": sub_id},
            )
            db.commit()
        finally:
            db.close()

        assert mgr.list_eligible_for_cron(now) == []
        # 31 minutes later → cooldown elapsed, eligible.
        future = now + timedelta(minutes=31)
        assert mgr.list_eligible_for_cron(future) == [sub_id]

    def test_cooldown_until_blocks_eligibility(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from datetime import UTC, datetime, timedelta

        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")

        now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        future = (now + timedelta(hours=2)).isoformat()
        db = media_import_db()
        try:
            db.execute(
                text(
                    "UPDATE subscriptions SET cooldown_until = :u "
                    "WHERE id = :id"
                ),
                {"u": future, "id": sub_id},
            )
            db.commit()
        finally:
            db.close()

        assert mgr.list_eligible_for_cron(now) == []
        # Past the cooldown_until → eligible.
        assert mgr.list_eligible_for_cron(
            datetime(2026, 5, 1, 14, 30, tzinfo=UTC)
        ) == [sub_id]


class TestCooldownHelpers:
    def test_set_and_clear_cooldown_until_round_trip(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from datetime import UTC, datetime, timedelta

        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")
        until = datetime(2026, 5, 1, 13, 0, tzinfo=UTC) + timedelta(hours=1)

        mgr._set_cooldown_until(sub_id, until)
        db = media_import_db()
        try:
            value = db.execute(
                text(
                    "SELECT cooldown_until FROM subscriptions "
                    "WHERE id = :id"
                ),
                {"id": sub_id},
            ).scalar()
        finally:
            db.close()
        assert value is not None and "2026-05-01T14:00" in value

        mgr._clear_cooldown_until(sub_id)
        db = media_import_db()
        try:
            value = db.execute(
                text(
                    "SELECT cooldown_until FROM subscriptions "
                    "WHERE id = :id"
                ),
                {"id": sub_id},
            ).scalar()
        finally:
            db.close()
        assert value is None

    def test_next_backoff_minutes_first_rung_when_no_history(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")
        # last_synced_at and cooldown_until both NULL → first rung.
        assert mgr._next_backoff_minutes(sub_id) == 60

    def test_next_backoff_minutes_climbs_ladder(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from datetime import UTC, datetime, timedelta

        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")

        ls = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        db = media_import_db()
        try:
            db.execute(
                text(
                    "UPDATE subscriptions "
                    "SET last_synced_at = :ls, cooldown_until = :cu "
                    "WHERE id = :id"
                ),
                {
                    "ls": ls.isoformat(),
                    "cu": (ls + timedelta(minutes=60)).isoformat(),
                    "id": sub_id,
                },
            )
            db.commit()
        finally:
            db.close()
        # Previous rung was 60 → next is 240.
        assert mgr._next_backoff_minutes(sub_id) == 240

        # Bump to 240 → next is 1440.
        db = media_import_db()
        try:
            db.execute(
                text(
                    "UPDATE subscriptions SET cooldown_until = :cu "
                    "WHERE id = :id"
                ),
                {
                    "cu": (ls + timedelta(minutes=240)).isoformat(),
                    "id": sub_id,
                },
            )
            db.commit()
        finally:
            db.close()
        assert mgr._next_backoff_minutes(sub_id) == 1440

        # At 1440 → caps at 1440.
        db = media_import_db()
        try:
            db.execute(
                text(
                    "UPDATE subscriptions SET cooldown_until = :cu "
                    "WHERE id = :id"
                ),
                {
                    "cu": (ls + timedelta(minutes=1440)).isoformat(),
                    "id": sub_id,
                },
            )
            db.commit()
        finally:
            db.close()
        assert mgr._next_backoff_minutes(sub_id) == 1440


class TestSyncClearsCooldownOnSuccess:
    def test_full_sync_clears_cooldown_until(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from datetime import UTC, datetime, timedelta

        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")
        # Pretend a previous cron failure set a backoff.
        mgr._set_cooldown_until(
            sub_id, datetime.now(UTC) + timedelta(hours=1)
        )

        mgr._sync_blocking(sub_id)

        db = media_import_db()
        try:
            value = db.execute(
                text(
                    "SELECT cooldown_until FROM subscriptions "
                    "WHERE id = :id"
                ),
                {"id": sub_id},
            ).scalar()
        finally:
            db.close()
        assert value is None

    def test_single_item_retry_does_not_clear_cooldown(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        """Retry must not interfere with cron's backoff state machine."""
        from datetime import UTC, datetime, timedelta

        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        fake_provider.headers = [ItemHeader(item_id="vid", title="Vid")]
        fake_provider.items = {
            "vid": ItemMetadata(
                item_id="vid",
                canonical_url="https://fake/v/vid",
                title="Vid",
                has_captions=False,
            ),
        }
        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")
        until = datetime.now(UTC) + timedelta(hours=1)
        mgr._set_cooldown_until(sub_id, until)

        # Seed the row so retry can target it.
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

        mgr._sync_blocking(sub_id, item_id="vid")

        db = media_import_db()
        try:
            value = db.execute(
                text(
                    "SELECT cooldown_until FROM subscriptions "
                    "WHERE id = :id"
                ),
                {"id": sub_id},
            ).scalar()
        finally:
            db.close()
        assert value is not None  # retry must not clear cron backoff


class TestSourceMetadataAtCreate:
    """Phase 4: ``create`` opportunistically fetches avatar / display
    title via the provider so the dashboard has something to show
    before the first sync. Failures must not abort row creation."""

    def test_metadata_persists_when_provider_returns_some(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        fake_provider.source_metadata = SourceMetadata(
            title="Fake Channel",
            avatar_url="https://example/avatar.jpg",
        )
        mgr = SubscriptionManager()

        with patch(
            "addons.media_import.subscription.manager."
            "_save_subscription_avatar",
            return_value=True,
        ) as save_avatar:
            sub_id = _create_subscription(mgr, drive="d")

        save_avatar.assert_called_once_with(
            sub_id, "https://example/avatar.jpg"
        )
        assert len(fake_provider.source_metadata_calls) == 1

        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT avatar_url, display_title FROM subscriptions "
                    "WHERE id = :id"
                ),
                {"id": sub_id},
            ).mappings().first()
        finally:
            db.close()
        assert row["avatar_url"] == "https://example/avatar.jpg"
        assert row["display_title"] == "Fake Channel"

    def test_provider_returning_none_leaves_columns_null(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        fake_provider.source_metadata = None
        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")

        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT avatar_url, display_title FROM subscriptions "
                    "WHERE id = :id"
                ),
                {"id": sub_id},
            ).mappings().first()
        finally:
            db.close()
        assert row["avatar_url"] is None
        assert row["display_title"] is None

    def test_provider_exception_does_not_abort_create(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        def boom(_ref):
            raise RuntimeError("network down")

        fake_provider.fetch_source_metadata = boom  # type: ignore[assignment]
        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")

        # Row exists; columns are NULL.
        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT id, avatar_url FROM subscriptions WHERE id = :id"
                ),
                {"id": sub_id},
            ).mappings().first()
        finally:
            db.close()
        assert row is not None
        assert row["avatar_url"] is None


class TestRefreshSourceMetadata:
    """Manual refresh entry point used by the
    ``POST /subscriptions/{id}/refresh-metadata`` route in Phase C-1."""

    def test_returns_true_when_provider_yields_metadata(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        fake_provider.source_metadata = None
        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")

        # Now provider has metadata to give back.
        fake_provider.source_metadata = SourceMetadata(
            title="Refreshed", avatar_url=None
        )
        with patch(
            "addons.media_import.subscription.manager."
            "_save_subscription_avatar",
            return_value=True,
        ):
            ok = mgr.refresh_source_metadata(sub_id)
        assert ok is True

        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT display_title FROM subscriptions WHERE id = :id"
                ),
                {"id": sub_id},
            ).mappings().first()
        finally:
            db.close()
        assert row["display_title"] == "Refreshed"

    def test_returns_false_when_provider_yields_none(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        fake_provider.source_metadata = None
        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")

        ok = mgr.refresh_source_metadata(sub_id)
        assert ok is False


class TestSyncDiffOpportunisticBackfill:
    """``_sync_diff`` retries source-metadata fetch only when the row
    is missing both avatar and display_title — so populated rows do
    not pay yt-dlp on every cron tick."""

    def test_skips_when_either_field_already_set(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        fake_provider.source_metadata = SourceMetadata(
            title="Original", avatar_url=None
        )
        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")
        # Reset call log; the create-time fetch already happened.
        fake_provider.source_metadata_calls.clear()

        # Sync with no items.
        fake_provider.headers = []
        mgr._sync_blocking(sub_id)

        # display_title was set at create; backfill must not run.
        assert fake_provider.source_metadata_calls == []

    def test_runs_when_both_fields_null(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
        )

        # Create with no metadata available; both columns end up NULL.
        fake_provider.source_metadata = None
        mgr = SubscriptionManager()
        sub_id = _create_subscription(mgr, drive="d")
        fake_provider.source_metadata_calls.clear()

        # Provider gains metadata between create and the first sync.
        fake_provider.source_metadata = SourceMetadata(
            title="Late title", avatar_url=None
        )
        fake_provider.headers = []
        mgr._sync_blocking(sub_id)

        assert len(fake_provider.source_metadata_calls) == 1


class TestRefreshUnknownSubscription:
    def test_refresh_raises_when_id_not_found(
        self, media_import_db, drive_path, fake_provider: _FakeProvider
    ) -> None:
        from addons.media_import.subscription.manager import (
            SubscriptionManager,
            SubscriptionNotFound,
        )

        mgr = SubscriptionManager()
        with pytest.raises(SubscriptionNotFound):
            mgr.refresh_source_metadata(99999)
