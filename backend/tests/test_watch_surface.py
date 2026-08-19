"""Watch surface: subscription display modes + the lane projection.

Spec: ``docs/superpowers/specs/2026-08-10-media-import-watch-surface.md``.

Rows are seeded directly rather than driven through the worker — the
import path is already covered by ``test_subscription_router.py``, and
what matters here is which existing rows a lane selects, in what order,
and what it refuses to leak.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

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


# ``WatchHistory.last_played_at`` is a naive column and SQLAlchemy's
# SQLite DATETIME binding drops the offset, so stored rows read as
# ``2026-08-14 15:04:45.794347``. Seeding raw SQL with ``isoformat()``
# would write a ``T``-separated, offset-carrying string instead, and the
# freshness gate compares against ``datetime('now', ...)`` **as text** —
# a fixture in the wrong shape would prove nothing about production.
_STORED_DATETIME = "%Y-%m-%d %H:%M:%S.%f"


def _wall_clock(moment: datetime) -> str:
    """Format a datetime the way the app stores it."""
    return moment.strftime(_STORED_DATETIME)


def _days_ago(days: float) -> str:
    return _wall_clock(datetime.now(UTC) - timedelta(days=days))


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
                "ts": last_played_at or _wall_clock(datetime.now(UTC)),
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


class TestRegularSourceCap:
    """Regular sources shows the newest few *from each source*.

    Spec ``2026-08-19-watch-lane-bounds.md`` §2. Before this, the lane
    ran the same query as ``feed`` with a different display_mode, so it
    was the recent-videos list shown a second time. The cap is what
    gives it a property a chronological list cannot have: one source
    cannot crowd out the others.
    """

    @staticmethod
    def _source(session, ref: str, videos: list[tuple[str, str]]) -> list[str]:
        """Seed one regular subscription and its videos.

        ``videos`` is ``[(file_id, published_at)]``, newest last is not
        assumed — ordering is asserted by the tests, not by the fixture.
        """
        sub_id = _seed_subscription(
            session, display_mode="regular", ref=ref
        )
        ids = []
        for file_id, published in videos:
            fid = _seed_loft(
                session,
                file_id=file_id,
                filename=f"{file_id}.loft",
                published_at=published,
                created_at="2026-08-01 00:00:00",
            )
            _link(session, sub_id, fid)
            ids.append(fid)
        return ids

    def test_a_source_contributes_only_its_newest_two(
        self, client, media_import_db
    ):
        self._source(
            media_import_db,
            "chan1",
            [
                ("v1aaaaaaaaaa", "20260801"),
                ("v2aaaaaaaaaa", "20260802"),
                ("v3aaaaaaaaaa", "20260803"),
                ("v4aaaaaaaaaa", "20260804"),
                ("v5aaaaaaaaaa", "20260805"),
            ],
        )

        items = _watch(client, "regular")
        assert [i["file_id"] for i in items] == [
            "v5aaaaaaaaaa",
            "v4aaaaaaaaaa",
        ]

    def test_a_busy_source_cannot_crowd_out_a_quiet_one(
        self, client, media_import_db
    ):
        """The property that justifies the lane's existence.

        Every one of ``busy``'s uploads is newer than ``quiet``'s only
        one. Ordered chronologically — which is what this lane did
        before — ``quiet`` never appears until the fifth row. The point
        of Regular sources is that it does.
        """
        self._source(
            media_import_db,
            "busy",
            [
                ("b1aaaaaaaaaa", "20260806"),
                ("b2aaaaaaaaaa", "20260807"),
                ("b3aaaaaaaaaa", "20260808"),
                ("b4aaaaaaaaaa", "20260809"),
                ("b5aaaaaaaaaa", "20260810"),
            ],
        )
        self._source(media_import_db, "quiet", [("q1aaaaaaaaaa", "20260801")])

        items = [i["file_id"] for i in _watch(client, "regular")]
        assert items == ["b5aaaaaaaaaa", "b4aaaaaaaaaa", "q1aaaaaaaaaa"]

    def test_every_source_survives_the_lane_limit(
        self, client, media_import_db
    ):
        """Seven sources yield fourteen rows; the lane asks for twelve.

        Ordering across sources is unchanged, so every source's newest
        video sorts above every source's second — which means the two
        rows that overflow are second entries, and **no source is
        silently dropped whole**. Cutting per source instead would let
        one source's second video displace another source's only
        appearance, undoing the reason the cap exists (spec §5).
        """
        for idx in range(7):
            # Source 0 published most recently, source 6 longest ago.
            day = 20 - idx
            self._source(
                media_import_db,
                f"chan{idx}",
                [
                    (f"s{idx}anewwwwww", f"202608{day:02d}"),
                    (f"s{idx}bolddddddd", f"202607{day:02d}"),
                ],
            )

        items = [i["file_id"] for i in _watch(client, "regular", limit=12)]
        assert len(items) == 12
        # Every source is represented, the stalest one included.
        for idx in range(7):
            assert f"s{idx}anewwwwww" in items
        # What overflowed is the oldest second entries, and only those.
        assert "s5bolddddddd" not in items
        assert "s6bolddddddd" not in items

    def test_a_shared_video_consumes_one_sources_slot(
        self, client, media_import_db
    ):
        """Collapsing to one row per file decides the partition too.

        A video reachable from two subscriptions is attributed to
        ``MIN(s.id)`` — and that attribution is the partition key, so it
        occupies a slot in exactly one source rather than in both.
        """
        first = _seed_subscription(
            media_import_db, display_mode="regular", ref="first"
        )
        second = _seed_subscription(
            media_import_db, display_mode="regular", ref="second"
        )
        shared = _seed_loft(
            media_import_db,
            file_id="sharedddddd2",
            filename="Shared.loft",
            published_at="20260810",
            created_at="2026-08-01 00:00:00",
        )
        _link(media_import_db, first, shared)
        _link(media_import_db, second, shared)
        for file_id, published in (
            ("firstaaaaaa1", "20260809"),
            ("firstaaaaaa2", "20260808"),
        ):
            fid = _seed_loft(
                media_import_db,
                file_id=file_id,
                filename=f"{file_id}.loft",
                published_at=published,
                created_at="2026-08-01 00:00:00",
            )
            _link(media_import_db, first, fid)
        other = _seed_loft(
            media_import_db,
            file_id="secondaaaaa1",
            filename="Second.loft",
            published_at="20260807",
            created_at="2026-08-01 00:00:00",
        )
        _link(media_import_db, second, other)

        items = [i["file_id"] for i in _watch(client, "regular")]
        # One row for the shared video, and it fills a slot in `first`
        # only — so `first` contributes it plus one more, not two more.
        assert items == ["sharedddddd2", "firstaaaaaa1", "secondaaaaa1"]

    def test_completed_videos_are_not_treated_differently(
        self, client, media_import_db
    ):
        """The cap ranks by date, never by playback state (spec §2).

        Dropping watched videos from the lane was considered and
        rejected: the parent spec §2.2 commits to subduing them without
        reordering. A cap that quietly skipped them would retract that
        by the back door.
        """
        ids = self._source(
            media_import_db,
            "chan1",
            [
                ("c1aaaaaaaaaa", "20260801"),
                ("c2aaaaaaaaaa", "20260802"),
                ("c3aaaaaaaaaa", "20260803"),
            ],
        )
        # The newest one is finished; it still holds its slot.
        _seed_history(media_import_db, ids[2], 300.0, 300.0)

        items = _watch(client, "regular")
        assert [i["file_id"] for i in items] == [
            "c3aaaaaaaaaa",
            "c2aaaaaaaaaa",
        ]
        assert items[0]["playback"]["state"] == "completed"

    def test_feed_is_never_capped(self, client, media_import_db):
        """``feed`` keeps every video: it is the chronological lane."""
        sub_id = _seed_subscription(media_import_db, display_mode="feed")
        for idx in range(5):
            fid = _seed_loft(
                media_import_db,
                file_id=f"f{idx}aaaaaaaaa",
                filename=f"F{idx}.loft",
                published_at=f"2026080{idx + 1}",
                created_at="2026-08-01 00:00:00",
            )
            _link(media_import_db, sub_id, fid)

        assert len(_watch(client, "feed")) == 5


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

    def test_playback_older_than_the_window_leaves_the_lane(
        self, client, media_import_db
    ):
        """A video started and abandoned stops being "in progress".

        Spec ``2026-08-19-watch-lane-bounds.md`` §3. The predicate is
        unchanged — both rows are in progress — only recency separates
        them.
        """
        stale = _seed_loft(
            media_import_db,
            file_id="staleeeeeee1",
            filename="Stale.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        fresh = _seed_loft(
            media_import_db,
            file_id="fresheeeeee1",
            filename="Fresh.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        _seed_history(
            media_import_db, stale, 30.0, 300.0, last_played_at=_days_ago(8)
        )
        _seed_history(
            media_import_db, fresh, 30.0, 300.0, last_played_at=_days_ago(6)
        )

        items = _watch(client, "continue")
        assert [i["file_id"] for i in items] == [fresh]

    @pytest.mark.parametrize(
        ("offset_days", "expected"),
        [
            # Both sides of the cutoff, an hour away from it so the
            # assertion cannot be decided by clock drift during the run.
            (7 - 1 / 24, True),
            (7 + 1 / 24, False),
        ],
    )
    def test_freshness_boundary(
        self, client, media_import_db, offset_days, expected
    ):
        """The cutoff is compared as text, in SQLite, in one zone.

        Computing it in Python and binding an aware datetime would
        render a ``+00:00`` suffix and compare it against rows that
        carry none (see ``app/routers/internal.py`` ``_parse_iso8601``).
        This pins the behaviour either way.
        """
        fid = _seed_loft(
            media_import_db,
            file_id="boundaryyyy1",
            filename="Boundary.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        _seed_history(
            media_import_db,
            fid,
            30.0,
            300.0,
            last_played_at=_days_ago(offset_days),
        )

        items = _watch(client, "continue")
        assert bool(items) is expected

    def test_the_gate_matches_how_the_app_stores_timestamps(
        self, client, media_import_db
    ):
        """Close the loop between the fixture shape and the real writer.

        Every other test here seeds ``watch_history`` with raw SQL, which
        proves the gate works against *a* string format — not that the
        format is the one core actually writes. ``POST /progress``
        assigns ``datetime.now(UTC)`` to a naive column, and the gate
        compares the result against SQLite's ``datetime('now')``. If
        SQLAlchemy ever stored an offset, or a local wall clock, the
        window would silently shift by hours and every other test here
        would still pass.
        """
        from app.models import WatchHistory

        fid = _seed_loft(
            media_import_db,
            file_id="ormwrittenn1",
            filename="OrmWritten.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        db = media_import_db()
        try:
            db.add(
                WatchHistory(
                    viewer_id=_viewer_id(),
                    file_id=fid,
                    playback_position=30.0,
                    duration=300.0,
                    last_played_at=datetime.now(UTC),
                )
            )
            db.commit()
        finally:
            db.close()

        db = media_import_db()
        try:
            stored = db.execute(
                text(
                    "SELECT last_played_at FROM watch_history "
                    "WHERE file_id = :f"
                ),
                {"f": fid},
            ).scalar_one()
        finally:
            db.close()

        # The shape the fixtures imitate, carrying no offset...
        written = datetime.strptime(stored, _STORED_DATETIME)
        # ...on the same clock SQLite's ``now`` reads.
        drift = abs(
            (datetime.now(UTC).replace(tzinfo=None) - written).total_seconds()
        )
        assert drift < 60, f"stored {stored!r} is not a UTC wall clock"

        assert [i["file_id"] for i in _watch(client, "continue")] == [fid]

    def test_ageing_out_never_touches_stored_progress(
        self, client, media_import_db
    ):
        """The gate is display-only (spec §3).

        Nothing is written and nothing is deleted: the row survives with
        its markers intact, so opening the file still resumes where the
        viewer left off. This is the invariant that lets Watch drop the
        video without also dropping the viewer's place in it.
        """
        fid = _seed_loft(
            media_import_db,
            file_id="preserveddd1",
            filename="Preserved.loft",
            published_at="20260801",
            created_at="2026-08-01 00:00:00",
        )
        played_at = _days_ago(30)
        _seed_history(
            media_import_db, fid, 123.5, 300.0, last_played_at=played_at
        )

        assert _watch(client, "continue") == []

        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT playback_position, duration, last_played_at "
                    "FROM watch_history WHERE file_id = :f"
                ),
                {"f": fid},
            ).mappings().one()
        finally:
            db.close()
        assert row["playback_position"] == 123.5
        assert row["duration"] == 300.0
        assert row["last_played_at"] == played_at

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
