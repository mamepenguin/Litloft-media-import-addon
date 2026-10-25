"""Acceptance tests for the ``subscription_videos`` upgrade.

SPEC-ADDON-028 (the one-time move of legacy failures to retry-pending) and
SPEC-ADDON-029 (``title`` / ``published_at`` columns). Invariant I9.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

_LEGACY_DDL = """
CREATE TABLE subscription_videos (
    subscription_id INTEGER NOT NULL
        REFERENCES subscriptions(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,
    status TEXT NOT NULL,
    error_kind TEXT,
    file_id TEXT REFERENCES files(id) ON DELETE SET NULL,
    first_seen_at TEXT NOT NULL,
    last_attempted_at TEXT,
    PRIMARY KEY (subscription_id, item_id)
)
"""

_LEGACY_ROWS = [
    ("m1", "failed", "fetch_failed"),
    ("m2", "failed", None),
    ("k1", "failed", "rate_limited"),
    ("k2", "failed", "path_conflict"),
    ("k3", "failed", "permanent"),
    ("k4", "failed", "dismissed"),
    ("k5", "imported", None),
    ("k6", "pending", None),
]


def _columns(session_factory) -> set[str]:
    db = session_factory()
    try:
        return {
            r[1]
            for r in db.execute(text("PRAGMA table_info(subscription_videos)")).fetchall()
        }
    finally:
        db.close()


def _install_legacy(session_factory, extra_columns: str = "") -> None:
    db = session_factory()
    try:
        db.execute(text("DROP TABLE subscription_videos"))
        ddl = _LEGACY_DDL
        if extra_columns:
            ddl = ddl.replace(
                "last_attempted_at TEXT,",
                f"last_attempted_at TEXT,\n    {extra_columns},",
            )
        db.execute(text(ddl))
        db.execute(
            text(
                "INSERT INTO subscriptions "
                "(id, provider, source_kind, source_ref, drive, folder_path, "
                " is_enabled, cooldown_minutes, include_no_transcript, created_at) "
                "VALUES (1, 'youtube', 'channel', 'UCabc', 'd', '', 1, 60, 0, "
                " '2026-05-01T00:00:00')"
            )
        )
        for iid, status, ek in _LEGACY_ROWS:
            db.execute(
                text(
                    "INSERT INTO subscription_videos "
                    "(subscription_id, item_id, status, error_kind, first_seen_at) "
                    "VALUES (1, :i, :s, :e, '2026-05-01T00:00:00')"
                ),
                {"i": iid, "s": status, "e": ek},
            )
        db.commit()
    finally:
        db.close()


def _states(session_factory, with_count: bool = True) -> dict[str, tuple]:
    cols = "item_id, status, error_kind" + (", fail_count" if with_count else "")
    db = session_factory()
    try:
        return {
            r[0]: tuple(r[1:])
            for r in db.execute(text(f"SELECT {cols} FROM subscription_videos")).fetchall()
        }
    finally:
        db.close()


class TestFreshInstall:
    """SPEC-ADDON-028, SPEC-ADDON-029."""

    def test_spec_addon_028_029_fresh_table_has_new_columns_with_default(
        self, media_import_db
    ) -> None:
        assert {"fail_count", "title", "published_at"} <= _columns(media_import_db)
        db = media_import_db()
        try:
            db.execute(
                text(
                    "INSERT INTO subscriptions "
                    "(id, provider, source_kind, source_ref, drive, folder_path, "
                    " is_enabled, cooldown_minutes, include_no_transcript, created_at) "
                    "VALUES (9, 'youtube', 'channel', 'UCabc', 'd', '', 1, 60, 0, "
                    " '2026-05-01T00:00:00')"
                )
            )
            db.execute(
                text(
                    "INSERT INTO subscription_videos "
                    "(subscription_id, item_id, status, first_seen_at) "
                    "VALUES (9, 'x', 'failed', '2026-05-01T00:00:00')"
                )
            )
            db.commit()
            row = db.execute(
                text("SELECT fail_count, title, published_at FROM subscription_videos")
            ).first()
        finally:
            db.close()
        assert tuple(row) == (0, None, None)


class TestUpgradeMove:
    """SPEC-ADDON-028 / I9."""

    def test_spec_addon_028_move_runs_once_and_only_on_legacy_failures(
        self, media_import_db
    ) -> None:
        from addons.media_import.service import _ensure_subscription_tables

        _install_legacy(media_import_db)

        _ensure_subscription_tables()

        assert {"fail_count", "title", "published_at"} <= _columns(media_import_db)
        states = _states(media_import_db)
        assert states["m1"] == ("failed", "retry_pending", 0)
        assert states["m2"] == ("failed", "retry_pending", 0)
        assert states["k1"] == ("failed", "rate_limited", 0)
        assert states["k2"] == ("failed", "path_conflict", 0)
        assert states["k3"] == ("failed", "permanent", 0)
        assert states["k4"] == ("failed", "dismissed", 0)
        assert states["k5"] == ("imported", None, 0)
        assert states["k6"] == ("pending", None, 0)

        db = media_import_db()
        try:
            db.execute(
                text(
                    "INSERT INTO subscription_videos "
                    "(subscription_id, item_id, status, error_kind, first_seen_at, "
                    " fail_count) VALUES (1, 'later', 'failed', 'fetch_failed', "
                    " '2026-06-01T00:00:00', 3), (1, 'later_null', 'failed', NULL, "
                    " '2026-06-01T00:00:00', 0)"
                )
            )
            db.commit()
        finally:
            db.close()

        _ensure_subscription_tables()

        states = _states(media_import_db)
        assert states["later"] == ("failed", "fetch_failed", 3)
        assert states["later_null"] == ("failed", None, 0)

    def test_spec_addon_029_adding_only_title_columns_moves_nothing(
        self, media_import_db
    ) -> None:
        from addons.media_import.service import _ensure_subscription_tables

        _install_legacy(
            media_import_db,
            extra_columns="fail_count INTEGER NOT NULL DEFAULT 0",
        )

        _ensure_subscription_tables()

        assert {"title", "published_at"} <= _columns(media_import_db)
        states = _states(media_import_db)
        assert states["m1"] == ("failed", "fetch_failed", 0)
        assert states["m2"] == ("failed", None, 0)

    def test_spec_addon_028_failed_move_rolls_back_column_and_propagates(
        self, media_import_db
    ) -> None:
        from addons.media_import.service import _ensure_subscription_tables

        _install_legacy(media_import_db)
        db = media_import_db()
        try:
            db.execute(
                text(
                    "CREATE TRIGGER block_move BEFORE UPDATE ON subscription_videos "
                    "BEGIN SELECT RAISE(ABORT, 'move blocked'); END"
                )
            )
            db.commit()
        finally:
            db.close()

        with pytest.raises(Exception):
            _ensure_subscription_tables()

        assert "fail_count" not in _columns(media_import_db)
        before = {iid: (s, ek) for iid, s, ek in _LEGACY_ROWS}
        assert _states(media_import_db, with_count=False) == before

        db = media_import_db()
        try:
            db.execute(text("DROP TRIGGER block_move"))
            db.commit()
        finally:
            db.close()

        _ensure_subscription_tables()

        states = _states(media_import_db)
        assert states["m1"] == ("failed", "retry_pending", 0)
        assert states["m2"] == ("failed", "retry_pending", 0)
