"""Tests for ``_ensure_subscription_tables`` schema and FK behavior."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


SUBSCRIPTIONS_COLUMNS = (
    "id",
    "provider",
    "source_kind",
    "source_ref",
    "drive",
    "folder_path",
    "title",
    "is_enabled",
    "cooldown_minutes",
    "include_no_transcript",
    "last_synced_at",
    "cooldown_until",
    "created_at",
    # Phase 4 additions; nullable so the migration is safe on existing
    # rows. Filled by the SubscriptionManager via fetch_source_metadata
    # at create time, and refreshable later via the dedicated route.
    "avatar_url",
    "display_title",
)

SUBSCRIPTION_VIDEOS_COLUMNS = (
    "subscription_id",
    "item_id",
    "status",
    "error_kind",
    "file_id",
    "first_seen_at",
    "last_attempted_at",
)


class TestSubscriptionTablesSchema:
    def test_tables_present(self, media_import_db) -> None:
        db = media_import_db()
        try:
            names = {
                row[0]
                for row in db.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name IN ('subscriptions', 'subscription_videos')"
                    )
                ).fetchall()
            }
        finally:
            db.close()

        assert names == {"subscriptions", "subscription_videos"}

    def test_subscriptions_columns(self, media_import_db) -> None:
        db = media_import_db()
        try:
            cols = {
                row[1]
                for row in db.execute(
                    text("PRAGMA table_info(subscriptions)")
                ).fetchall()
            }
        finally:
            db.close()

        for required in SUBSCRIPTIONS_COLUMNS:
            assert required in cols, f"missing column: {required}"

    def test_subscription_videos_columns(self, media_import_db) -> None:
        db = media_import_db()
        try:
            cols = {
                row[1]
                for row in db.execute(
                    text("PRAGMA table_info(subscription_videos)")
                ).fetchall()
            }
        finally:
            db.close()

        for required in SUBSCRIPTION_VIDEOS_COLUMNS:
            assert required in cols, f"missing column: {required}"

    def test_idempotent_on_repeat_call(self, media_import_db) -> None:
        from addons.media_import.service import _ensure_subscription_tables

        # Fixture has called it once already; calling twice more must not raise.
        _ensure_subscription_tables()
        _ensure_subscription_tables()

    def test_migration_adds_phase4_columns_to_legacy_table(
        self, media_import_db
    ) -> None:
        """Simulates a Phase 2/3 install: table exists without the new
        columns, then ``_ensure_subscription_tables`` ALTERs them in.
        Hits the ``OperationalError: duplicate column`` swallow on the
        second pass to prove idempotency.
        """
        from addons.media_import.service import _ensure_subscription_tables

        db = media_import_db()
        try:
            db.execute(text("DROP TABLE subscription_videos"))
            db.execute(text("DROP TABLE subscriptions"))
            # Recreate without the Phase 4 columns to mimic a legacy install.
            db.execute(
                text(
                    """
                    CREATE TABLE subscriptions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        provider TEXT NOT NULL,
                        source_kind TEXT NOT NULL,
                        source_ref TEXT NOT NULL,
                        drive TEXT NOT NULL,
                        folder_path TEXT NOT NULL DEFAULT '',
                        title TEXT,
                        is_enabled BOOLEAN NOT NULL DEFAULT 1,
                        cooldown_minutes INTEGER NOT NULL DEFAULT 60,
                        include_no_transcript BOOLEAN NOT NULL DEFAULT 0,
                        last_synced_at TEXT,
                        cooldown_until TEXT,
                        created_at TEXT NOT NULL,
                        UNIQUE(provider, source_kind, source_ref, drive, folder_path)
                    )
                    """
                )
            )
            db.commit()
        finally:
            db.close()

        _ensure_subscription_tables()
        # Second call must not raise on the ADD COLUMN attempts.
        _ensure_subscription_tables()

        db = media_import_db()
        try:
            cols = {
                row[1]
                for row in db.execute(
                    text("PRAGMA table_info(subscriptions)")
                ).fetchall()
            }
        finally:
            db.close()

        assert "avatar_url" in cols
        assert "display_title" in cols


class TestSubscriptionsUniqueConstraint:
    def test_unique_on_provider_kind_ref_drive_folder(self, media_import_db) -> None:
        db = media_import_db()
        try:
            db.execute(
                text(
                    "INSERT INTO subscriptions "
                    "(provider, source_kind, source_ref, drive, folder_path, "
                    " is_enabled, cooldown_minutes, include_no_transcript, created_at) "
                    "VALUES ('youtube', 'channel', 'UCabc', 'media', 'yt/foo', "
                    " 1, 60, 0, '2026-05-01T00:00:00')"
                )
            )
            db.commit()

            with pytest.raises(IntegrityError):
                db.execute(
                    text(
                        "INSERT INTO subscriptions "
                        "(provider, source_kind, source_ref, drive, folder_path, "
                        " is_enabled, cooldown_minutes, include_no_transcript, created_at) "
                        "VALUES ('youtube', 'channel', 'UCabc', 'media', 'yt/foo', "
                        " 1, 60, 0, '2026-05-01T00:00:00')"
                    )
                )
                db.commit()
            db.rollback()

            # Same provider/kind/ref/drive but different folder is allowed
            db.execute(
                text(
                    "INSERT INTO subscriptions "
                    "(provider, source_kind, source_ref, drive, folder_path, "
                    " is_enabled, cooldown_minutes, include_no_transcript, created_at) "
                    "VALUES ('youtube', 'channel', 'UCabc', 'media', 'yt/bar', "
                    " 1, 60, 0, '2026-05-01T00:00:00')"
                )
            )
            db.commit()
        finally:
            db.close()


def _seed_file(db, file_id: str, drive: str = "media") -> None:
    db.execute(
        text(
            "INSERT INTO files (id, filename, title, description, drive, "
            " folder_path, file_path, file_size, file_type, mime_type, "
            " created_at, updated_at) "
            "VALUES (:id, :name, :name, '', :drive, '', :path, 0, 'other', "
            " 'application/x-loft', '2026-05-01T00:00:00', "
            " '2026-05-01T00:00:00')"
        ),
        {
            "id": file_id,
            "name": f"{file_id}.loft",
            "drive": drive,
            "path": f"/tmp/{file_id}.loft",
        },
    )


def _seed_subscription(db, sub_id: int = 1) -> None:
    db.execute(
        text(
            "INSERT INTO subscriptions "
            "(id, provider, source_kind, source_ref, drive, folder_path, "
            " is_enabled, cooldown_minutes, include_no_transcript, created_at) "
            "VALUES (:id, 'youtube', 'channel', 'UCabc', 'media', '', "
            " 1, 60, 0, '2026-05-01T00:00:00')"
        ),
        {"id": sub_id},
    )


class TestForeignKeyBehavior:
    def test_cascade_on_subscription_delete(self, media_import_db) -> None:
        db = media_import_db()
        try:
            _seed_subscription(db, sub_id=1)
            db.execute(
                text(
                    "INSERT INTO subscription_videos "
                    "(subscription_id, item_id, status, first_seen_at) "
                    "VALUES (1, 'vid_a', 'pending', '2026-05-01T00:00:00')"
                )
            )
            db.commit()

            db.execute(text("DELETE FROM subscriptions WHERE id = 1"))
            db.commit()

            remaining = db.execute(
                text("SELECT COUNT(*) FROM subscription_videos")
            ).scalar()
            assert remaining == 0
        finally:
            db.close()

    def test_set_null_on_file_delete(self, media_import_db) -> None:
        db = media_import_db()
        try:
            _seed_subscription(db, sub_id=1)
            _seed_file(db, file_id="file_x")
            db.execute(
                text(
                    "INSERT INTO subscription_videos "
                    "(subscription_id, item_id, status, file_id, first_seen_at) "
                    "VALUES (1, 'vid_a', 'imported', 'file_x', '2026-05-01T00:00:00')"
                )
            )
            db.commit()

            db.execute(text("DELETE FROM files WHERE id = 'file_x'"))
            db.commit()

            row = db.execute(
                text(
                    "SELECT file_id, status FROM subscription_videos "
                    "WHERE subscription_id = 1 AND item_id = 'vid_a'"
                )
            ).first()
            assert row is not None
            assert row[0] is None
            # The row itself must still exist.
            assert row[1] == "imported"
        finally:
            db.close()


class TestPrimaryKey:
    def test_pk_blocks_duplicate_item(self, media_import_db) -> None:
        db = media_import_db()
        try:
            _seed_subscription(db, sub_id=1)
            db.execute(
                text(
                    "INSERT INTO subscription_videos "
                    "(subscription_id, item_id, status, first_seen_at) "
                    "VALUES (1, 'vid_a', 'pending', '2026-05-01T00:00:00')"
                )
            )
            db.commit()

            with pytest.raises(IntegrityError):
                db.execute(
                    text(
                        "INSERT INTO subscription_videos "
                        "(subscription_id, item_id, status, first_seen_at) "
                        "VALUES (1, 'vid_a', 'pending', '2026-05-01T00:00:00')"
                    )
                )
                db.commit()
            db.rollback()
        finally:
            db.close()
