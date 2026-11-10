"""Keyset paging on ``GET /watch`` (SPEC-ADDON-091).

Rows are seeded directly, as in ``test_watch_surface.py``: what matters is
which rows a lane returns, in what order, and how ``after`` positions a read.
"""
from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, text

DRIVE = "d"
WATCH = "/api/addons/media_import/watch"


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
        c.headers["X-Lit-Viewer"] = "alice"
        yield c


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _seed_loft(
    session, *, file_id: str, published_at: str | None, created_at: str
) -> str:
    from app.models import File

    db = session()
    try:
        db.add(
            File(
                id=file_id,
                filename=f"{file_id}.loft",
                title=file_id,
                drive=DRIVE,
                folder_path="",
                file_path=f"{file_id}.loft",
                file_size=100,
                file_type="video",
                mime_type="application/vnd.litloft.loft+json",
                duration=300.0,
                created_at=_ts(created_at),
                updated_at=_ts(created_at),
            )
        )
        db.flush()
        db.execute(
            text(
                "INSERT INTO loft_metadata "
                "(file_id, provider, provider_item_id, url, published_at, "
                " channel, has_captions, captions_downloaded) "
                "VALUES (:id, 'fakeyt', :id, :url, :pub, 'Chan', 0, 0)"
            ),
            {"id": file_id, "url": f"https://fake/{file_id}", "pub": published_at},
        )
        db.commit()
    finally:
        db.close()
    return file_id


def _seed_subscription(display_mode: str, ref: str) -> int:
    from addons.media_import.subscription import db as subdb

    return subdb.insert_subscription(
        provider="fakeyt",
        source_kind="channel",
        source_ref=ref,
        drive=DRIVE,
        folder_path="",
        cooldown_minutes=60,
        include_no_transcript=False,
        display_mode=display_mode,
    )


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


def _source(session, mode: str, ref: str, videos: list[tuple[str, str | None, str]]):
    """Seed one subscription and its videos, ``[(file_id, published, created)]``."""
    sub_id = _seed_subscription(mode, ref)
    for file_id, published, created in videos:
        _seed_loft(session, file_id=file_id, published_at=published, created_at=created)
        _link(session, sub_id, file_id)


def _get(client, lane: str, **params):
    return client.get(WATCH, params={"lane": lane, "drive": DRIVE, **params})


def _watch(client, lane: str, **params) -> list[dict]:
    res = _get(client, lane, **params)
    assert res.status_code == 200, res.text
    return res.json()


def _page_through(client, lane: str, limit: int) -> list[str]:
    """Read the lane page by page, each page after the last row's cursor."""
    seen: list[str] = []
    after: str | None = None
    for _ in range(100):
        params = {"limit": limit}
        if after is not None:
            params["after"] = after
        page = _watch(client, lane, **params)
        seen.extend(item["file_id"] for item in page)
        if len(page) < limit:
            return seen
        after = page[-1]["cursor"]
    raise AssertionError("paging did not end")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(cursor: str):
    padded = cursor + "=" * (-len(cursor) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


# Feed rows mixing ties on both keys, ties on the publish date only, and
# undated rows whose sort falls back to the import time. Inserted in
# ascending file_id order, so a lane that leaves ties in insertion order
# reads them the wrong way round.
_FEED_ROWS = [
    ("tieaaaaaaaa1", "20260805", "2026-08-05 00:00:00"),
    ("tieaaaaaaaa2", "20260805", "2026-08-05 00:00:00"),
    ("tieaaaaaaaa3", "20260805", "2026-08-05 00:00:00"),
    ("pubtieaaaaa1", "20260804", "2026-08-01 00:00:00"),
    ("pubtieaaaaa2", "20260804", "2026-08-03 00:00:00"),
    ("undatedaaaa1", None, "2026-08-06 00:00:00"),
    ("undatedaaaa2", None, "2026-08-02 00:00:00"),
    ("plainaaaaaa1", "20260801", "2026-08-01 00:00:00"),
    ("plainaaaaaa2", "20260803", "2026-08-09 00:00:00"),
]


class TestTieOrder:
    def test_rows_tied_on_both_keys_read_by_file_id_descending_spec_addon_091_i2(
        self, client, media_import_db
    ):
        _source(
            media_import_db,
            "feed",
            "feedchan",
            [
                ("tieaaaaaaaa1", "20260801", "2026-08-01 00:00:00"),
                ("tieaaaaaaaa2", "20260801", "2026-08-01 00:00:00"),
                ("tieaaaaaaaa3", "20260801", "2026-08-01 00:00:00"),
            ],
        )

        assert [i["file_id"] for i in _watch(client, "feed")] == [
            "tieaaaaaaaa3",
            "tieaaaaaaaa2",
            "tieaaaaaaaa1",
        ]

    def test_regular_cap_keeps_the_two_highest_file_ids_among_ties_spec_addon_091_i2(
        self, client, media_import_db
    ):
        _source(
            media_import_db,
            "regular",
            "regchan",
            [
                ("capaaaaaaaa1", "20260801", "2026-08-01 00:00:00"),
                ("capaaaaaaaa2", "20260801", "2026-08-01 00:00:00"),
                ("capaaaaaaaa3", "20260801", "2026-08-01 00:00:00"),
            ],
        )

        for _ in range(3):
            assert [i["file_id"] for i in _watch(client, "regular")] == [
                "capaaaaaaaa3",
                "capaaaaaaaa2",
            ]


class TestCursor:
    def test_every_item_carries_a_cursor_naming_its_file_spec_addon_091(
        self, client, media_import_db
    ):
        _source(media_import_db, "feed", "feedchan", _FEED_ROWS)

        items = _watch(client, "feed")
        assert items
        for item in items:
            assert isinstance(item["cursor"], str) and item["cursor"]
            decoded = _decode(item["cursor"])
            assert isinstance(decoded, list) and len(decoded) == 3
            assert decoded[2] == item["file_id"]

    @pytest.mark.parametrize("lane", ["feed", "regular"])
    def test_items_no_longer_carry_playback_spec_addon_091(
        self, client, media_import_db, lane
    ):
        _source(
            media_import_db,
            lane,
            f"{lane}chan",
            [("playaaaaaaa1", "20260801", "2026-08-01 00:00:00")],
        )

        items = _watch(client, lane)
        assert [i["file_id"] for i in items] == ["playaaaaaaa1"]
        assert "playback" not in items[0]

    @pytest.mark.parametrize("lane", ["feed", "regular"])
    def test_watch_does_not_read_watch_history_spec_addon_091(
        self, client, media_import_db, lane
    ):
        import app.database as database

        _source(
            media_import_db,
            lane,
            f"{lane}chan",
            [("histaaaaaaa1", "20260801", "2026-08-01 00:00:00")],
        )
        statements: list[str] = []

        def _record(_conn, _cursor, statement, *_rest):
            statements.append(statement)

        event.listen(database.engine, "before_cursor_execute", _record)
        try:
            _watch(client, lane)
            _watch(client, lane, limit=1, after=_watch(client, lane)[0]["cursor"])
        finally:
            event.remove(database.engine, "before_cursor_execute", _record)

        assert statements
        assert not [s for s in statements if "watch_history" in s.lower()]


class TestKeysetPaging:
    @pytest.mark.parametrize("limit", [1, 2, 4])
    def test_feed_pages_return_every_row_once_in_unpaged_order_spec_addon_091_i1(
        self, client, media_import_db, limit
    ):
        _source(media_import_db, "feed", "feedchan", _FEED_ROWS)

        unpaged = [i["file_id"] for i in _watch(client, "feed", limit=100)]
        assert len(unpaged) == len(_FEED_ROWS)
        assert _page_through(client, "feed", limit) == unpaged

    @pytest.mark.parametrize("limit", [1, 2, 3])
    def test_regular_pages_through_the_capped_lane_spec_addon_091_i1(
        self, client, media_import_db, limit
    ):
        _source(
            media_import_db,
            "regular",
            "busy",
            [
                ("busyaaaaaaa1", "20260810", "2026-08-10 00:00:00"),
                ("busyaaaaaaa2", "20260809", "2026-08-09 00:00:00"),
                ("busyaaaaaaa3", "20260808", "2026-08-08 00:00:00"),
                ("busyaaaaaaa4", "20260807", "2026-08-07 00:00:00"),
            ],
        )
        _source(
            media_import_db,
            "regular",
            "tied",
            [
                ("tiedaaaaaaa1", "20260806", "2026-08-06 00:00:00"),
                ("tiedaaaaaaa2", "20260806", "2026-08-06 00:00:00"),
                ("tiedaaaaaaa3", "20260806", "2026-08-06 00:00:00"),
            ],
        )
        _source(
            media_import_db,
            "regular",
            "quiet",
            [("quietaaaaaa1", "20260801", "2026-08-01 00:00:00")],
        )

        unpaged = [i["file_id"] for i in _watch(client, "regular", limit=100)]
        assert unpaged == [
            "busyaaaaaaa1",
            "busyaaaaaaa2",
            "tiedaaaaaaa3",
            "tiedaaaaaaa2",
            "quietaaaaaa1",
        ]
        assert _page_through(client, "regular", limit) == unpaged

    def test_after_returns_only_rows_strictly_after_the_position_spec_addon_091(
        self, client, media_import_db
    ):
        _source(media_import_db, "feed", "feedchan", _FEED_ROWS)

        unpaged = _watch(client, "feed", limit=100)
        for index, item in enumerate(unpaged):
            rest = _watch(client, "feed", limit=100, after=item["cursor"])
            assert [i["file_id"] for i in rest] == [
                i["file_id"] for i in unpaged[index + 1 :]
            ]

    def test_after_then_applies_limit_spec_addon_091(self, client, media_import_db):
        _source(media_import_db, "feed", "feedchan", _FEED_ROWS)

        unpaged = [i["file_id"] for i in _watch(client, "feed", limit=100)]
        first = _watch(client, "feed", limit=2)
        second = _watch(client, "feed", limit=2, after=first[-1]["cursor"])
        assert [i["file_id"] for i in second] == unpaged[2:4]

    def test_after_the_last_row_is_an_empty_page_spec_addon_091(
        self, client, media_import_db
    ):
        _source(media_import_db, "feed", "feedchan", _FEED_ROWS)

        last = _watch(client, "feed", limit=100)[-1]
        assert _watch(client, "feed", after=last["cursor"]) == []

    def test_after_with_offset_zero_is_accepted_spec_addon_091_i3(
        self, client, media_import_db
    ):
        _source(media_import_db, "feed", "feedchan", _FEED_ROWS)

        unpaged = [i["file_id"] for i in _watch(client, "feed", limit=100)]
        first = _watch(client, "feed", limit=3)
        rest = _watch(client, "feed", limit=100, offset=0, after=first[-1]["cursor"])
        assert [i["file_id"] for i in rest] == unpaged[3:]


class TestAfterRejected:
    def test_after_with_a_nonzero_offset_is_422_spec_addon_091_i3(
        self, client, media_import_db
    ):
        _source(media_import_db, "feed", "feedchan", _FEED_ROWS)
        cursor = _watch(client, "feed", limit=1)[0]["cursor"]

        res = _get(client, "feed", after=cursor, offset=1)
        assert res.status_code == 422

    @pytest.mark.parametrize(
        "after",
        [
            "!!!not base64!!!",
            _encode(b"not json at all"),
        ],
        ids=["not-base64", "not-json"],
    )
    def test_an_after_that_does_not_decode_is_422_spec_addon_091_i3(
        self, client, media_import_db, after
    ):
        _source(media_import_db, "feed", "feedchan", _FEED_ROWS)

        res = _get(client, "feed", after=after)
        assert res.status_code == 422
