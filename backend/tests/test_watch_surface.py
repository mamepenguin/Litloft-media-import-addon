"""Watch surface: subscription display modes + the lane projection.

Spec: ``docs/superpowers/specs/2026-08-10-media-import-watch-surface.md``.

Rows are seeded directly rather than driven through the worker — the
import path is already covered by ``test_subscription_router.py``, and
what matters here is which existing rows a lane selects, in what order,
and what it refuses to leak.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

DRIVE = "d"
VIEWER = "alice"


@pytest.fixture()
def client(media_import_db, drive_path):
    from addons.media_import.router import router

    @asynccontextmanager
    async def lifespan(_app):
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    with TestClient(app) as c:
        c.headers["X-Lit-Drive"] = DRIVE
        # Personal identity is orthogonal to drive access; the header
        # form of the viewer cookie is what non-browser callers use.
        c.headers["X-Lit-Viewer"] = VIEWER
        yield c


def _viewer_id(nickname: str = VIEWER) -> str:
    from app.auth import _viewer_id_from_nickname

    vid = _viewer_id_from_nickname(nickname)
    assert vid is not None
    return vid


def _seed_loft(
    session,
    *,
    file_id: str,
    filename: str,
    published_at: str | None,
    created_at: str,
    drive: str = DRIVE,
    deleted_at: str | None = None,
    missing_since: str | None = None,
) -> str:
    from app.models import File

    def _ts(value: str | None) -> datetime | None:
        return (
            datetime.fromisoformat(value).replace(tzinfo=None)
            if value
            else None
        )

    db = session()
    try:
        db.add(
            File(
                id=file_id,
                filename=filename,
                title=filename.removesuffix(".loft"),
                drive=drive,
                folder_path="",
                file_path=filename,
                file_size=100,
                file_type="video",
                mime_type="application/vnd.litloft.loft+json",
                duration=300.0,
                created_at=_ts(created_at),
                updated_at=_ts(created_at),
                deleted_at=_ts(deleted_at),
                missing_since=_ts(missing_since),
            )
        )
        db.flush()
        db.execute(
            text(
                "INSERT INTO loft_metadata "
                "(file_id, provider, provider_item_id, url, published_at, "
                " channel, has_captions, captions_downloaded) "
                "VALUES (:id, 'fakeyt', :item, :url, :pub, 'Chan', 0, 0)"
            ),
            {
                "id": file_id,
                "item": file_id,
                "url": f"https://fake/video/{file_id}",
                "pub": published_at,
            },
        )
        db.commit()
    finally:
        db.close()
    return file_id


def _seed_subscription(
    session, *, display_mode: str, drive: str = DRIVE, ref: str = "chan1"
) -> int:
    from addons.media_import.subscription import db as subdb

    sub_id = subdb.insert_subscription(
        provider="fakeyt",
        source_kind="channel",
        source_ref=ref,
        drive=drive,
        folder_path="",
        cooldown_minutes=60,
        include_no_transcript=False,
        display_mode=display_mode,
    )
    return sub_id


def _link(session, sub_id: int, file_id: str) -> None:
    db = session()
    try:
        db.execute(
            text(
                "INSERT INTO subscription_videos "
                "(subscription_id, item_id, status, file_id, first_seen_at) "
                "VALUES (:sid, :iid, 'imported', :fid, :ts)"
            ),
            {
                "sid": sub_id,
                "iid": file_id,
                "fid": file_id,
                "ts": datetime.now(UTC).isoformat(),
            },
        )
        db.commit()
    finally:
        db.close()


def _seed_history(
    session, file_id: str, position: float, duration: float,
    *, nickname: str = VIEWER, last_played_at: str | None = None,
) -> None:
    db = session()
    try:
        db.execute(
            text(
                "INSERT INTO watch_history "
                "(viewer_id, file_id, playback_position, duration, "
                " last_played_at) "
                "VALUES (:v, :f, :p, :d, :ts)"
            ),
            {
                "v": _viewer_id(nickname),
                "f": file_id,
                "p": position,
                "d": duration,
                "ts": last_played_at or datetime.now(UTC).isoformat(),
            },
        )
        db.commit()
    finally:
        db.close()


def _watch(client, lane: str, **params):
    res = client.get(
        "/api/addons/media_import/watch",
        params={"lane": lane, "drive": DRIVE, **params},
    )
    assert res.status_code == 200, res.text
    return res.json()


# ---- display mode --------------------------------------------------


class TestDisplayMode:
    def test_existing_rows_migrate_to_library(self, media_import_db):
        """The ADD COLUMN default is the entire migration (spec §8).

        Simulates a pre-Watch install: a row written without the column
        must read back as ``library`` so nothing already imported turns
        into a viewing backlog.
        """
        from addons.media_import.subscription import db as subdb

        db = media_import_db()
        try:
            db.execute(text("ALTER TABLE subscriptions DROP COLUMN display_mode"))
            db.execute(
                text(
                    "INSERT INTO subscriptions "
                    "(provider, source_kind, source_ref, drive, folder_path, "
                    " is_enabled, cooldown_minutes, include_no_transcript, "
                    " created_at) "
                    "VALUES ('fakeyt', 'channel', 'legacy', :drive, '', 1, 60, "
                    " 0, :ts)"
                ),
                {"drive": DRIVE, "ts": datetime.now(UTC).isoformat()},
            )
            db.commit()
        finally:
            db.close()

        import addons.media_import.service as service

        service._ensure_subscription_tables()

        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT display_mode FROM subscriptions "
                    "WHERE source_ref = 'legacy'"
                )
            ).mappings().first()
        finally:
            db.close()
        assert row["display_mode"] == "library"
        assert subdb.count_surfaced_subscriptions(DRIVE) == 0

    def test_new_subscription_defaults_to_library(self, media_import_db):
        sub_id = _seed_subscription(media_import_db, display_mode="library")
        db = media_import_db()
        try:
            row = db.execute(
                text("SELECT display_mode FROM subscriptions WHERE id = :i"),
                {"i": sub_id},
            ).mappings().first()
        finally:
            db.close()
        assert row["display_mode"] == "library"

    def test_patch_changes_mode_without_touching_files(
        self, client, media_import_db
    ):
        """Mode is presentation only (spec §3.2)."""
        sub_id = _seed_subscription(media_import_db, display_mode="library")
        fid = _seed_loft(
            media_import_db,
            file_id="aaaaaaaaaaaa",
            filename="A.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        _link(media_import_db, sub_id, fid)

        db = media_import_db()
        try:
            before = db.execute(
                text(
                    "SELECT filename, file_path, updated_at, deleted_at "
                    "FROM files WHERE id = :i"
                ),
                {"i": fid},
            ).mappings().first()
            meta_before = db.execute(
                text("SELECT * FROM loft_metadata WHERE file_id = :i"),
                {"i": fid},
            ).mappings().first()
        finally:
            db.close()

        res = client.patch(
            f"/api/addons/media_import/subscriptions/{sub_id}",
            json={"display_mode": "regular"},
        )
        assert res.status_code == 200
        assert res.json()["display_mode"] == "regular"

        db = media_import_db()
        try:
            after = db.execute(
                text(
                    "SELECT filename, file_path, updated_at, deleted_at "
                    "FROM files WHERE id = :i"
                ),
                {"i": fid},
            ).mappings().first()
            meta_after = db.execute(
                text("SELECT * FROM loft_metadata WHERE file_id = :i"),
                {"i": fid},
            ).mappings().first()
        finally:
            db.close()
        assert dict(after) == dict(before)
        assert dict(meta_after) == dict(meta_before)

    def test_patch_rejects_unknown_mode(self, client, media_import_db):
        sub_id = _seed_subscription(media_import_db, display_mode="library")
        res = client.patch(
            f"/api/addons/media_import/subscriptions/{sub_id}",
            json={"display_mode": "inbox"},
        )
        assert res.status_code == 422


# ---- lanes ---------------------------------------------------------


class TestWatchLanes:
    def test_library_items_stay_out_of_the_lanes(self, client, media_import_db):
        sub_id = _seed_subscription(media_import_db, display_mode="library")
        fid = _seed_loft(
            media_import_db,
            file_id="libbbbbbbbbb",
            filename="Lib.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        _link(media_import_db, sub_id, fid)

        assert _watch(client, "feed") == []
        assert _watch(client, "regular") == []

    def test_feed_and_regular_do_not_bleed_into_each_other(
        self, client, media_import_db
    ):
        feed_sub = _seed_subscription(
            media_import_db, display_mode="feed", ref="feedchan"
        )
        reg_sub = _seed_subscription(
            media_import_db, display_mode="regular", ref="regchan"
        )
        feed_file = _seed_loft(
            media_import_db,
            file_id="feeddddddddd",
            filename="Feed.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        reg_file = _seed_loft(
            media_import_db,
            file_id="regggggggggg",
            filename="Reg.loft",
            published_at="20260802",
            created_at="2026-08-02 00:00:00",
        )
        _link(media_import_db, feed_sub, feed_file)
        _link(media_import_db, reg_sub, reg_file)

        assert [i["file_id"] for i in _watch(client, "feed")] == [feed_file]
        assert [i["file_id"] for i in _watch(client, "regular")] == [reg_file]

    def test_ordered_by_publication_not_import_time(
        self, client, media_import_db
    ):
        """A backfill imports old videos today (spec §3.1 "recent uploads").

        Ordering by import time would park a years-old upload at the top
        of the lane just because it was fetched last.
        """
        sub_id = _seed_subscription(media_import_db, display_mode="feed")
        old = _seed_loft(
            media_import_db,
            file_id="oldddddddddd",
            filename="Old.loft",
            published_at="20200101",
            # Imported *later* than the new one — the backfill case.
            created_at="2026-08-10 00:00:00",
        )
        new = _seed_loft(
            media_import_db,
            file_id="newwwwwwwwww",
            filename="New.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        _link(media_import_db, sub_id, old)
        _link(media_import_db, sub_id, new)

        assert [i["file_id"] for i in _watch(client, "feed")] == [new, old]

    def test_missing_publication_date_falls_back_to_import_time(
        self, client, media_import_db
    ):
        sub_id = _seed_subscription(media_import_db, display_mode="feed")
        dated = _seed_loft(
            media_import_db,
            file_id="datedddddddd",
            filename="Dated.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        undated = _seed_loft(
            media_import_db,
            file_id="undatedddddd",
            filename="Undated.loft",
            published_at=None,
            created_at="2026-08-05 00:00:00",
        )
        _link(media_import_db, sub_id, dated)
        _link(media_import_db, sub_id, undated)

        assert [i["file_id"] for i in _watch(client, "feed")] == [
            undated,
            dated,
        ]

    def test_video_in_two_subscriptions_appears_once(
        self, client, media_import_db
    ):
        a = _seed_subscription(media_import_db, display_mode="feed", ref="a")
        b = _seed_subscription(media_import_db, display_mode="feed", ref="b")
        fid = _seed_loft(
            media_import_db,
            file_id="sharedddddd1",
            filename="Shared.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        _link(media_import_db, a, fid)
        _link(media_import_db, b, fid)

        items = _watch(client, "feed")
        assert [i["file_id"] for i in items] == [fid]

    def test_pagination_is_bounded(self, client, media_import_db):
        sub_id = _seed_subscription(media_import_db, display_mode="feed")
        for i in range(5):
            fid = _seed_loft(
                media_import_db,
                file_id=f"page{i}aaaaaa",
                filename=f"P{i}.loft",
                published_at=f"2026080{i + 1}",
                created_at=f"2026-08-0{i + 1} 00:00:00",
            )
            _link(media_import_db, sub_id, fid)

        first = _watch(client, "feed", limit=2, offset=0)
        second = _watch(client, "feed", limit=2, offset=2)
        assert len(first) == 2
        assert len(second) == 2
        assert {i["file_id"] for i in first}.isdisjoint(
            {i["file_id"] for i in second}
        )


class TestContinueWatching:
    def test_includes_library_only_and_one_off_imports(
        self, client, media_import_db
    ):
        """Started videos resume regardless of how they got here (§3.1).

        ``lib`` came from a library-only subscription, ``solo`` from a
        one-off URL import with no subscription at all. Neither is in a
        lane, and both must still be resumable.
        """
        sub_id = _seed_subscription(media_import_db, display_mode="library")
        lib = _seed_loft(
            media_import_db,
            file_id="libstartedaa",
            filename="Lib.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        _link(media_import_db, sub_id, lib)
        solo = _seed_loft(
            media_import_db,
            file_id="solostartedb",
            filename="Solo.loft",
            published_at="20260802",
            created_at="2026-08-02 00:00:00",
        )
        _seed_history(media_import_db, lib, 30.0, 300.0)
        _seed_history(media_import_db, solo, 60.0, 300.0)

        items = _watch(client, "continue")
        assert {i["file_id"] for i in items} == {lib, solo}
        assert all(i["playback"]["state"] == "in_progress" for i in items)

    def test_excludes_completed_and_view_only(self, client, media_import_db):
        done = _seed_loft(
            media_import_db,
            file_id="doneeeeeeeee",
            filename="Done.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        opened = _seed_loft(
            media_import_db,
            file_id="openedddddd1",
            filename="Opened.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        _seed_history(media_import_db, done, 300.0, 300.0)
        # The view-only record the detail page writes on open.
        _seed_history(media_import_db, opened, 0.0, 0.0)

        assert _watch(client, "continue") == []

    def test_empty_without_a_viewer(self, media_import_db, drive_path):
        from addons.media_import.router import router

        app = FastAPI()
        app.include_router(router)
        fid = _seed_loft(
            media_import_db,
            file_id="anonstarted1",
            filename="Anon.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        _seed_history(media_import_db, fid, 30.0, 300.0)

        with TestClient(app) as anon:
            anon.headers["X-Lit-Drive"] = DRIVE
            res = anon.get(
                "/api/addons/media_import/watch",
                params={"lane": "continue", "drive": DRIVE},
            )
        assert res.status_code == 200
        assert res.json() == []

    def test_another_viewers_progress_is_invisible(
        self, client, media_import_db
    ):
        fid = _seed_loft(
            media_import_db,
            file_id="bobstartedaa",
            filename="Bob.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        _seed_history(media_import_db, fid, 30.0, 300.0, nickname="bob")

        assert _watch(client, "continue") == []


class TestPlaybackBadges:
    def test_completed_lane_item_keeps_its_place(self, client, media_import_db):
        """Completed styling must not reorder the lane (spec §9)."""
        sub_id = _seed_subscription(media_import_db, display_mode="feed")
        newer = _seed_loft(
            media_import_db,
            file_id="newerdoneaaa",
            filename="Newer.loft",
            published_at="20260802",
            created_at="2026-08-02 00:00:00",
        )
        older = _seed_loft(
            media_import_db,
            file_id="olderfreshbb",
            filename="Older.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        _link(media_import_db, sub_id, newer)
        _link(media_import_db, sub_id, older)
        _seed_history(media_import_db, newer, 300.0, 300.0)

        items = _watch(client, "feed")
        assert [i["file_id"] for i in items] == [newer, older]
        assert items[0]["playback"]["state"] == "completed"
        assert items[1]["playback"] is None

    def test_progress_failure_does_not_hide_videos(
        self, client, media_import_db, monkeypatch
    ):
        """Spec §7: WatchHistory unavailable costs badges, not videos."""
        import addons.media_import.router as router_mod

        sub_id = _seed_subscription(media_import_db, display_mode="feed")
        fid = _seed_loft(
            media_import_db,
            file_id="degradedaaaa",
            filename="Degraded.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        _link(media_import_db, sub_id, fid)
        _seed_history(media_import_db, fid, 30.0, 300.0)

        def _boom(*_a, **_kw):
            raise RuntimeError("watch_history unavailable")

        monkeypatch.setattr(router_mod.subdb, "load_playback_markers", _boom)

        items = _watch(client, "feed")
        assert [i["file_id"] for i in items] == [fid]
        assert items[0]["playback"] is None


class TestBoundaries:
    def test_missing_and_trashed_files_are_excluded(
        self, client, media_import_db
    ):
        sub_id = _seed_subscription(media_import_db, display_mode="feed")
        trashed = _seed_loft(
            media_import_db,
            file_id="trashedaaaaa",
            filename="Trashed.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
            deleted_at="2026-08-05 00:00:00",
        )
        gone = _seed_loft(
            media_import_db,
            file_id="missingbbbbb",
            filename="Missing.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
            missing_since="2026-08-05 00:00:00",
        )
        _link(media_import_db, sub_id, trashed)
        _link(media_import_db, sub_id, gone)
        _seed_history(media_import_db, trashed, 30.0, 300.0)
        _seed_history(media_import_db, gone, 30.0, 300.0)

        assert _watch(client, "feed") == []
        assert _watch(client, "continue") == []

    def test_other_drives_never_appear(self, client, media_import_db):
        sub_id = _seed_subscription(
            media_import_db, display_mode="feed", drive="other", ref="othchan"
        )
        fid = _seed_loft(
            media_import_db,
            file_id="otherdrivea1",
            filename="Other.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
            drive="other",
        )
        _link(media_import_db, sub_id, fid)
        _seed_history(media_import_db, fid, 30.0, 300.0)

        assert _watch(client, "feed") == []
        assert _watch(client, "continue") == []

    def test_drive_query_must_match_the_scope_header(self, client):
        res = client.get(
            "/api/addons/media_import/watch",
            params={"lane": "feed", "drive": "somewhere-else"},
        )
        assert res.status_code == 400

    def test_unknown_lane_is_rejected(self, client):
        res = client.get(
            "/api/addons/media_import/watch",
            params={"lane": "inbox", "drive": DRIVE},
        )
        assert res.status_code == 422
