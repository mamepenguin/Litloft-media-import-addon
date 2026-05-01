"""Tests for ``_ensure_loft_table`` idempotency and core-DB sharing."""
from __future__ import annotations

from sqlalchemy import text


class TestEnsureLoftTableIdempotency:
    def test_idempotent_on_repeat_call(self, media_import_db) -> None:
        # Fixture already calls _ensure_loft_table once; calling again must
        # not raise.
        from addons.media_import.service import _ensure_loft_table

        _ensure_loft_table()
        _ensure_loft_table()

        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='loft_metadata'"
                )
            ).first()
        finally:
            db.close()

        assert row is not None
        assert row[0] == "loft_metadata"

    def test_columns_present(self, media_import_db) -> None:
        db = media_import_db()
        try:
            cols = {
                row[1]
                for row in db.execute(
                    text("PRAGMA table_info(loft_metadata)")
                ).fetchall()
            }
        finally:
            db.close()

        # Production schema columns from spec DB schema section.
        for required in (
            "file_id",
            "provider",
            "url",
            "description",
            "channel",
            "published_at",
            "language",
            "has_captions",
            "captions_downloaded",
            "caption_error_kind",
            "fetched_at",
            "fetch_error",
        ):
            assert required in cols, f"missing column: {required}"

    def test_legacy_hvlink_metadata_is_renamed(self, tmp_path, monkeypatch) -> None:
        """When only legacy ``hvlink_metadata`` exists, it gets renamed."""
        from sqlalchemy import create_engine, event
        from sqlalchemy.orm import sessionmaker

        from app.models import Base

        db_path = tmp_path / "legacy.db"
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            future=True,
        )

        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        Base.metadata.create_all(bind=engine)
        TestSession = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, future=True
        )

        # Seed the legacy table shape directly.
        db = TestSession()
        try:
            db.execute(
                text(
                    """
                    CREATE TABLE hvlink_metadata (
                        file_id TEXT PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
                        provider TEXT NOT NULL,
                        url TEXT NOT NULL
                    )
                    """
                )
            )
            db.commit()
        finally:
            db.close()

        import addons.media_import.service as service
        import app.database as database

        monkeypatch.setattr(database, "engine", engine)
        monkeypatch.setattr(database, "SessionLocal", TestSession)
        monkeypatch.setattr(service, "SessionLocal", TestSession)

        service._ensure_loft_table()

        db = TestSession()
        try:
            new_row = db.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='loft_metadata'"
                )
            ).first()
            old_row = db.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='hvlink_metadata'"
                )
            ).first()
        finally:
            db.close()

        assert new_row is not None
        # After RENAME the legacy name must no longer resolve.
        assert old_row is None
        engine.dispose()
