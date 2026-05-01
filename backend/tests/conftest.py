"""Shared fixtures for media_import addon tests.

Each test gets its own SQLite DB in a tmp dir, swapped in via monkeypatch
on the ``app.database`` module so all addon code paths use the per-test
DB. The core ``files`` table is created from ``app.models.Base`` and the
``loft_metadata`` table is materialized via the addon's ``_ensure_loft_table``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def media_import_db(tmp_path, monkeypatch):
    db_path = tmp_path / "media_import.db"
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

    import app.database as database
    from app.models import Base

    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True
    )
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", TestSession)

    # The addon imports SessionLocal directly into its module namespace.
    # Patch each call site so per-test isolation actually applies.
    import addons.media_import.service as service
    import addons.media_import.router as router

    monkeypatch.setattr(service, "SessionLocal", TestSession)
    monkeypatch.setattr(router, "SessionLocal", TestSession)

    # Materialize loft_metadata via the addon's own migration helper so
    # the schema under test is the production schema.
    service._ensure_loft_table()

    # Silence side-effects that have no in-test infrastructure:
    #   - WS broadcast (no running event loop in fetch worker thread)
    #   - intelligence event hook (no addon registry in unit tests)
    monkeypatch.setattr(
        service, "broadcast_from_thread", lambda *_a, **_kw: None
    )
    import app.services.event_hooks as event_hooks

    monkeypatch.setattr(event_hooks, "emit_sync", lambda *_a, **_kw: None)

    yield TestSession
    engine.dispose()


@pytest.fixture()
def drive_path(tmp_path, monkeypatch):
    """Provide a writable drive path that ``config.get_drive_path`` returns."""
    drive_dir = tmp_path / "drive"
    drive_dir.mkdir()

    import app.config as config

    def _fake_get_drive_path(name: str) -> Path:
        return drive_dir

    monkeypatch.setattr(config, "get_drive_path", _fake_get_drive_path)
    return drive_dir
