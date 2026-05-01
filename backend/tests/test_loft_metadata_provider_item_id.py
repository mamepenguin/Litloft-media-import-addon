"""Tests for the ``provider_item_id`` column on ``loft_metadata``.

Added in Phase 2 to support dedup across subscriptions: two subscriptions
that target the same ``(drive, folder_path)`` and pull the same provider
video must reuse a single ``.loft`` file. The lookup key is
``(drive, folder_path, provider, provider_item_id)`` joined with
``files``; the column carries the provider-internal id.
"""
from __future__ import annotations

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker


def _seed_file(session, file_id: str, drive: str = "d") -> None:
    """Insert a minimal File row so loft_metadata FK passes."""
    from app.models import File

    db = session()
    try:
        f = File(
            id=file_id,
            filename=f"{file_id}.loft",
            title=file_id,
            drive=drive,
            folder_path="",
            file_path=f"{file_id}.loft",
            file_size=1,
            file_type="video",
            mime_type="application/vnd.litloft.loft+json",
        )
        db.add(f)
        db.commit()
    finally:
        db.close()


class TestProviderItemIdColumn:
    def test_column_exists_on_fresh_schema(self, media_import_db) -> None:
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
        assert "provider_item_id" in cols

    def test_dedup_index_exists(self, media_import_db) -> None:
        """``(provider, provider_item_id)`` index supports dedup lookups."""
        db = media_import_db()
        try:
            indexes = {
                row[0]
                for row in db.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='index' AND tbl_name='loft_metadata'"
                    )
                ).fetchall()
            }
        finally:
            db.close()
        assert "idx_loft_metadata_dedup" in indexes

    def test_insert_with_provider_item_id(self, media_import_db) -> None:
        _seed_file(media_import_db, "f1")
        db = media_import_db()
        try:
            db.execute(
                text(
                    "INSERT INTO loft_metadata "
                    "(file_id, provider, provider_item_id, url) "
                    "VALUES (:fid, :p, :iid, :u)"
                ),
                {
                    "fid": "f1",
                    "p": "youtube",
                    "iid": "abcDEF12345",
                    "u": "https://youtu.be/abcDEF12345",
                },
            )
            db.commit()
            row = db.execute(
                text(
                    "SELECT provider_item_id FROM loft_metadata "
                    "WHERE file_id = 'f1'"
                )
            ).first()
        finally:
            db.close()
        assert row is not None
        assert row[0] == "abcDEF12345"

    def test_insert_without_provider_item_id_is_null(
        self, media_import_db
    ) -> None:
        """Legacy INSERT paths leave the column NULL (no NOT NULL crash)."""
        _seed_file(media_import_db, "f2")
        db = media_import_db()
        try:
            db.execute(
                text(
                    "INSERT INTO loft_metadata (file_id, provider, url) "
                    "VALUES (:fid, :p, :u)"
                ),
                {
                    "fid": "f2",
                    "p": "youtube",
                    "u": "https://youtu.be/zzz",
                },
            )
            db.commit()
            row = db.execute(
                text(
                    "SELECT provider_item_id FROM loft_metadata "
                    "WHERE file_id = 'f2'"
                )
            ).first()
        finally:
            db.close()
        assert row is not None
        assert row[0] is None

    def test_dedup_lookup_via_index(self, media_import_db) -> None:
        """Lookup ``(provider, provider_item_id)`` returns the correct row.

        This exercises the dedup query shape used by SubscriptionManager
        (Commit 3): given a candidate provider+item_id, find any existing
        ``.loft`` already pointing at the same upstream resource.
        """
        for fid in ("f3", "f4"):
            _seed_file(media_import_db, fid)
        db = media_import_db()
        try:
            for fid, item_id in (("f3", "vid-A"), ("f4", "vid-B")):
                db.execute(
                    text(
                        "INSERT INTO loft_metadata "
                        "(file_id, provider, provider_item_id, url) "
                        "VALUES (:fid, 'youtube', :iid, :u)"
                    ),
                    {
                        "fid": fid,
                        "iid": item_id,
                        "u": f"https://youtu.be/{item_id}",
                    },
                )
            db.commit()
            hit = db.execute(
                text(
                    "SELECT file_id FROM loft_metadata "
                    "WHERE provider = :p AND provider_item_id = :iid"
                ),
                {"p": "youtube", "iid": "vid-B"},
            ).first()
        finally:
            db.close()
        assert hit is not None
        assert hit[0] == "f4"


class TestPreexistingInstallMigration:
    """Pre-Phase-2 installs (no ``provider_item_id`` column) get upgraded."""

    def test_alter_table_adds_column_idempotent(
        self, tmp_path, monkeypatch
    ) -> None:
        from app.models import Base

        db_path = tmp_path / "preexisting.db"
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

        # Seed the pre-Phase-2 schema (no provider_item_id column, no index).
        db = TestSession()
        try:
            db.execute(
                text(
                    """
                    CREATE TABLE loft_metadata (
                        file_id TEXT PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
                        provider TEXT NOT NULL,
                        url TEXT NOT NULL,
                        description TEXT,
                        channel TEXT,
                        published_at TEXT,
                        language TEXT,
                        has_captions BOOLEAN DEFAULT FALSE,
                        captions_downloaded BOOLEAN DEFAULT FALSE,
                        caption_error_kind TEXT,
                        fetched_at TEXT,
                        fetch_error TEXT
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
        # Second call must be a no-op, not a crash.
        service._ensure_loft_table()

        db = TestSession()
        try:
            cols = {
                row[1]
                for row in db.execute(
                    text("PRAGMA table_info(loft_metadata)")
                ).fetchall()
            }
            indexes = {
                row[0]
                for row in db.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='index' AND tbl_name='loft_metadata'"
                    )
                ).fetchall()
            }
        finally:
            db.close()
        assert "provider_item_id" in cols
        assert "idx_loft_metadata_dedup" in indexes
        engine.dispose()
