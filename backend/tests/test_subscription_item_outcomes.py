"""Acceptance tests for subscription item outcomes, the retry set, the
rate-limit stop and stored listing titles.

SPEC-ADDON-027 (unavailable items), SPEC-ADDON-028 (automatic retry and the
rate-limit stop), SPEC-ADDON-029 (stored titles and the title lookup).

The provider is faked at its protocol boundary. ``fetch_item`` replays a
per-item script of outcomes; the typed exceptions it raises are the
provider's classification, so the manager is exercised without yt-dlp.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import text

from addons.media_import.subscription.registry import (
    ERROR_NO_TRANSCRIPT,
    REF_KIND_CHANNEL,
    ItemHeader,
    ItemMetadata,
    SubscriptionRef,
    TranscriptResult,
    _reset_subscription_registry_for_tests,
    register_subscription_provider,
)

_UC = "UCabcdefghijklmnopqrstuv"
_DELAY = 0.3


def _unavailable(msg: str = "members-only") -> Exception:
    from addons.media_import.subscription.registry import ItemUnavailable

    return ItemUnavailable(msg)


def _not_started(msg: str = "This live event will begin in 3 days") -> Exception:
    from addons.media_import.subscription.registry import ItemNotStarted

    return ItemNotStarted(msg)


def _rate_limited(msg: str = "try again later") -> Exception:
    from addons.media_import.subscription.registry import ItemRateLimited

    return ItemRateLimited(msg)


def _other(msg: str = "Unable to extract initial player response") -> Exception:
    return RuntimeError(msg)


OK = "ok"


@dataclass
class _ScriptedProvider:
    name: str = "fakeyt"
    inter_item_delay_seconds: float = 0.0
    headers: list[ItemHeader] = field(default_factory=list)
    script: dict[str, list[Any]] = field(default_factory=dict)
    lookup_titles: dict[str, Any] = field(default_factory=dict)
    broken_loft_items: set[str] = field(default_factory=set)
    fetch_calls: list[tuple[str, float]] = field(default_factory=list)
    lookup_calls: list[tuple[str, float]] = field(default_factory=list)

    def resolve_ref(self, url: str) -> SubscriptionRef | None:
        if "fake/channel/" in url:
            return SubscriptionRef(kind=REF_KIND_CHANNEL, ref=url.rsplit("/", 1)[-1])
        return None

    def list_items(self, ref, limit=None):
        out = list(self.headers)
        return out if limit is None else out[:limit]

    def fetch_item(self, ref, item_id):
        self.fetch_calls.append((item_id, time.monotonic()))
        steps = self.script.get(item_id)
        step = steps.pop(0) if steps else OK
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, ItemMetadata):
            return step
        return ItemMetadata(
            item_id=item_id,
            canonical_url=f"https://fake/v/{item_id}",
            title=f"Imported {item_id}",
        )

    def fetch_transcript(self, ref, item_id, language=None):
        return TranscriptResult(error_kind=ERROR_NO_TRANSCRIPT)

    def build_loft_content(self, item):
        if item.item_id in self.broken_loft_items:
            raise RuntimeError("loft content could not be built")
        return {"provider": self.name, "url": item.canonical_url}

    def fetch_source_metadata(self, ref):
        return None

    # The spec names a title lookup and an item URL on the provider but not
    # their signatures; the last positional argument is taken as the item id.
    def fetch_item_title(self, *args):
        item_id = args[-1]
        self.lookup_calls.append((item_id, time.monotonic()))
        answer = self.lookup_titles.get(item_id)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def item_url(self, *args):
        return f"https://fake/watch/{args[-1]}"

    def fetched(self) -> list[str]:
        return [iid for iid, _ in self.fetch_calls]

    def looked_up(self) -> list[str]:
        return [iid for iid, _ in self.lookup_calls]


@pytest.fixture()
def provider() -> _ScriptedProvider:
    _reset_subscription_registry_for_tests()
    p = _ScriptedProvider()
    register_subscription_provider(p)
    yield p
    _reset_subscription_registry_for_tests()


@pytest.fixture()
def mgr(media_import_db, drive_path, provider):
    from addons.media_import.subscription.manager import SubscriptionManager

    return SubscriptionManager()


@pytest.fixture()
def sub_id(mgr) -> int:
    return mgr.create(url=f"https://fake/channel/{_UC}", drive="d", folder_path="")


def _seed_row(
    session_factory,
    sub_id: int,
    item_id: str,
    *,
    status: str = "failed",
    error_kind: str | None = "retry_pending",
    fail_count: int = 0,
    title: str | None = None,
    published_at: str | None = None,
    first_seen_at: str = "2026-05-01T00:00:00",
    last_attempted_at: str | None = "2026-05-02T00:00:00",
    file_id: str | None = None,
) -> None:
    db = session_factory()
    try:
        db.execute(
            text(
                "INSERT INTO subscription_videos "
                "(subscription_id, item_id, status, error_kind, file_id, "
                " first_seen_at, last_attempted_at, fail_count, title, "
                " published_at) "
                "VALUES (:sid, :iid, :status, :ek, :fid, :fs, :la, :fc, "
                " :title, :pub)"
            ),
            {
                "sid": sub_id, "iid": item_id, "status": status,
                "ek": error_kind, "fid": file_id, "fs": first_seen_at,
                "la": last_attempted_at, "fc": fail_count, "title": title,
                "pub": published_at,
            },
        )
        db.commit()
    finally:
        db.close()


def _rows(session_factory, sub_id: int) -> dict[str, dict]:
    db = session_factory()
    try:
        rows = db.execute(
            text(
                "SELECT item_id, status, error_kind, fail_count, title, "
                " published_at, first_seen_at, last_attempted_at, file_id "
                "FROM subscription_videos WHERE subscription_id = :sid"
            ),
            {"sid": sub_id},
        ).mappings().all()
    finally:
        db.close()
    return {r["item_id"]: dict(r) for r in rows}


def _seed_existing_loft(session_factory, drive_path, item_id: str) -> None:
    loft = drive_path / f"Existing {item_id}.loft"
    loft.write_text(
        json.dumps({"provider": "fakeyt", "url": f"https://fake/v/{item_id}"}),
        encoding="utf-8",
    )
    db = session_factory()
    try:
        db.execute(
            text(
                "INSERT INTO files (id, filename, title, description, drive, "
                " folder_path, file_path, file_size, file_type, mime_type, "
                " created_at, updated_at) "
                "VALUES (:fid, :fn, :t, '', 'd', '', :fn, 100, 'other', "
                " 'application/x-loft', '2026-04-01T00:00:00', "
                " '2026-04-01T00:00:00')"
            ),
            {"fid": f"pre_{item_id}"[:12], "fn": loft.name, "t": loft.stem},
        )
        db.execute(
            text(
                "INSERT INTO loft_metadata (file_id, provider, provider_item_id, url) "
                "VALUES (:fid, 'fakeyt', :iid, :url)"
            ),
            {
                "fid": f"pre_{item_id}"[:12], "iid": item_id,
                "url": f"https://fake/v/{item_id}",
            },
        )
        db.commit()
    finally:
        db.close()


def _file_count(session_factory) -> int:
    db = session_factory()
    try:
        return db.execute(text("SELECT COUNT(*) FROM files")).scalar()
    finally:
        db.close()


# ---- step 6: outcomes on a new item --------------------------------


class TestNewItemOutcomes:
    """SPEC-ADDON-027, SPEC-ADDON-028: one attempt, one recorded outcome."""

    @pytest.mark.parametrize(
        "make_exc, error_kind",
        [
            (_unavailable, "permanent"),
            (_not_started, "upcoming"),
            (_other, "retry_pending"),
        ],
        ids=["unavailable", "not_started", "other_failure"],
    )
    def test_spec_addon_027_028_failure_outcome_recorded_on_new_row(
        self, mgr, sub_id, provider, media_import_db, make_exc, error_kind
    ) -> None:
        provider.headers = [ItemHeader(item_id="n1", title="N1")]
        provider.script = {"n1": [make_exc()]}

        result = mgr._sync_blocking(sub_id)

        row = _rows(media_import_db, sub_id)["n1"]
        assert row["status"] == "failed"
        assert row["error_kind"] == error_kind
        assert row["fail_count"] == (1 if error_kind == "retry_pending" else 0)
        assert row["last_attempted_at"] is not None
        assert row["file_id"] is None
        assert result["failed"] == 1
        assert result["added"] == 0
        assert result["total_new"] == 1
        assert result["rate_limited"] is False
        assert _file_count(media_import_db) == 0

    def test_spec_addon_028_imported_outcome_resets_nothing_and_counts(
        self, mgr, sub_id, provider, media_import_db, drive_path
    ) -> None:
        provider.headers = [ItemHeader(item_id="n1", title="N1")]

        result = mgr._sync_blocking(sub_id)

        row = _rows(media_import_db, sub_id)["n1"]
        assert row["status"] == "imported"
        assert row["file_id"] is not None
        assert row["fail_count"] == 0
        assert (drive_path / "Imported n1.loft").exists()
        assert result["added"] == 1
        assert result["failed"] == 0
        assert result["retried"] == 0
        assert result["rate_limited"] is False

    def test_spec_addon_028_failure_after_fetch_is_other_failure(
        self, mgr, sub_id, provider, media_import_db, drive_path
    ) -> None:
        provider.headers = [ItemHeader(item_id="n1", title="N1")]
        provider.broken_loft_items = {"n1"}

        result = mgr._sync_blocking(sub_id)

        row = _rows(media_import_db, sub_id)["n1"]
        assert row["status"] == "failed"
        assert row["error_kind"] == "retry_pending"
        assert row["fail_count"] == 1
        assert result["failed"] == 1
        assert not list(drive_path.glob("*.loft"))


# ---- section 3: state machine across syncs --------------------------


class TestThreeInARow:
    """SPEC-ADDON-028 / I3: Needs attention exactly on the third other
    failure in a row."""

    def test_spec_addon_028_third_consecutive_failure_becomes_fetch_failed(
        self, mgr, sub_id, provider, media_import_db
    ) -> None:
        provider.headers = [ItemHeader(item_id="n1", title="N1")]
        provider.script = {"n1": [_other(), _other(), _other()]}

        seen = []
        for _ in range(3):
            mgr._sync_blocking(sub_id)
            row = _rows(media_import_db, sub_id)["n1"]
            seen.append((row["error_kind"], row["fail_count"]))

        assert seen == [
            ("retry_pending", 1),
            ("retry_pending", 2),
            ("fetch_failed", 3),
        ]

    def test_spec_addon_028_needs_attention_row_is_not_retried_automatically(
        self, mgr, sub_id, provider, media_import_db
    ) -> None:
        _seed_row(media_import_db, sub_id, "att", error_kind="fetch_failed", fail_count=3)
        provider.headers = [ItemHeader(item_id="att", title="Att")]

        result = mgr._sync_blocking(sub_id)

        assert provider.fetched() == []
        assert result["retried"] == 0
        row = _rows(media_import_db, sub_id)["att"]
        assert (row["error_kind"], row["fail_count"]) == ("fetch_failed", 3)

    @pytest.mark.parametrize(
        "step, expected",
        [
            (_unavailable, ("failed", "permanent")),
            (_not_started, ("failed", "upcoming")),
            (OK, ("imported", None)),
        ],
        ids=["unavailable", "not_started", "imported"],
    )
    def test_spec_addon_028_non_other_outcome_resets_fail_count(
        self, mgr, sub_id, provider, media_import_db, step, expected
    ) -> None:
        _seed_row(media_import_db, sub_id, "r1", error_kind="retry_pending", fail_count=2)
        provider.script = {"r1": [step() if callable(step) else step, _other()]}

        mgr._sync_blocking(sub_id)
        row = _rows(media_import_db, sub_id)["r1"]
        assert row["fail_count"] == 0
        assert row["status"] == expected[0]
        if expected[0] == "failed":
            assert row["error_kind"] == expected[1]

        if expected[0] == "failed" and expected[1] == "upcoming":
            # From upcoming the count restarts: one other failure gives 1.
            mgr._sync_blocking(sub_id)
            row = _rows(media_import_db, sub_id)["r1"]
            assert (row["error_kind"], row["fail_count"]) == ("retry_pending", 1)

    def test_spec_addon_027_permanent_row_is_not_attempted_by_a_later_sync(
        self, mgr, sub_id, provider, media_import_db
    ) -> None:
        _seed_row(
            media_import_db, sub_id, "gone", error_kind="permanent",
            last_attempted_at="2026-05-02T00:00:00",
        )
        provider.headers = [ItemHeader(item_id="gone", title="Gone")]

        result = mgr._sync_blocking(sub_id)

        assert provider.fetched() == []
        assert result["retried"] == 0
        row = _rows(media_import_db, sub_id)["gone"]
        assert row["error_kind"] == "permanent"
        assert row["last_attempted_at"] == "2026-05-02T00:00:00"

    def test_spec_addon_028_skipped_row_is_not_attempted(
        self, mgr, sub_id, provider, media_import_db
    ) -> None:
        _seed_row(media_import_db, sub_id, "skip", error_kind="dismissed")
        result = mgr._sync_blocking(sub_id)
        assert provider.fetched() == []
        assert result["retried"] == 0
        assert _rows(media_import_db, sub_id)["skip"]["error_kind"] == "dismissed"


# ---- steps 2, 4, 5: the retry set ----------------------------------


class TestRetrySet:
    """SPEC-ADDON-028 / I4."""

    def test_spec_addon_028_new_items_first_then_retry_set_in_first_seen_rowid_order(
        self, mgr, sub_id, provider, media_import_db
    ) -> None:
        # Inserted b before a with the same first_seen_at: rowid breaks the tie.
        _seed_row(media_import_db, sub_id, "r_b", first_seen_at="2026-05-01T00:00:00")
        _seed_row(
            media_import_db, sub_id, "r_a", error_kind="upcoming",
            first_seen_at="2026-05-01T00:00:00",
        )
        _seed_row(media_import_db, sub_id, "r_c", first_seen_at="2026-04-30T00:00:00")
        # r_a is still listed; r_b and r_c have dropped off the listing.
        provider.headers = [
            ItemHeader(item_id="n1", title="N1"),
            ItemHeader(item_id="r_a", title="A"),
            ItemHeader(item_id="n2", title="N2"),
        ]
        provider.script = {"n1": [_other(), OK]}

        result = mgr._sync_blocking(sub_id)

        assert provider.fetched() == ["n1", "n2", "r_c", "r_b", "r_a"]
        assert result["retried"] == 3
        assert result["total_new"] == 2
        assert result["added"] == 4
        assert result["failed"] == 1
        rows = _rows(media_import_db, sub_id)
        # Created during this sync and failed: attempted once, left pending.
        assert (rows["n1"]["error_kind"], rows["n1"]["fail_count"]) == ("retry_pending", 1)
        for iid in ("n2", "r_a", "r_b", "r_c"):
            assert rows[iid]["status"] == "imported"
            assert rows[iid]["fail_count"] == 0

    def test_spec_addon_028_retry_set_counts_failed_retries_too(
        self, mgr, sub_id, provider, media_import_db
    ) -> None:
        _seed_row(media_import_db, sub_id, "r1", fail_count=1)
        _seed_row(media_import_db, sub_id, "r2", error_kind="upcoming")
        provider.script = {"r1": [_other()], "r2": [_not_started()]}

        result = mgr._sync_blocking(sub_id)

        assert result["retried"] == 2
        assert result["failed"] == 2
        assert result["total_new"] == 0
        rows = _rows(media_import_db, sub_id)
        assert (rows["r1"]["error_kind"], rows["r1"]["fail_count"]) == ("retry_pending", 2)
        assert (rows["r2"]["error_kind"], rows["r2"]["fail_count"]) == ("upcoming", 0)

    def test_spec_addon_028_retry_set_reuses_existing_loft_without_fetch(
        self, mgr, sub_id, provider, media_import_db, drive_path
    ) -> None:
        _seed_row(media_import_db, sub_id, "r1", fail_count=1)
        _seed_existing_loft(media_import_db, drive_path, "r1")

        result = mgr._sync_blocking(sub_id)

        assert provider.fetched() == []
        assert result["reused"] == 1
        assert result["added"] == 0
        assert result["retried"] == 1
        assert _file_count(media_import_db) == 1
        row = _rows(media_import_db, sub_id)["r1"]
        assert row["status"] == "imported"
        assert row["file_id"] == "pre_r1"
        assert row["fail_count"] == 0


# ---- step 7: rate-limit stop --------------------------------------


class TestRateLimitStop:
    """SPEC-ADDON-028 / I5."""

    def test_spec_addon_028_rate_limit_stops_sync_and_leaves_rows_for_next(
        self, mgr, sub_id, provider, media_import_db
    ) -> None:
        from datetime import UTC, datetime, timedelta

        _seed_row(
            media_import_db, sub_id, "r1", fail_count=1,
            last_attempted_at="2026-05-02T00:00:00",
        )
        _seed_row(
            media_import_db, sub_id, "r2", error_kind="upcoming",
            last_attempted_at="2026-05-03T00:00:00",
        )
        mgr._set_cooldown_until(sub_id, datetime.now(UTC) + timedelta(hours=1))
        provider.headers = [
            ItemHeader(item_id="n1", title="N1", published_at="2026-10-01"),
            ItemHeader(item_id="n2", title="N2", published_at="20261002"),
            ItemHeader(item_id="n3", title="  ", published_at=None),
        ]
        provider.script = {"n2": [_rate_limited()]}

        result = mgr._sync_blocking(sub_id)

        assert provider.fetched() == ["n1", "n2"]
        assert provider.looked_up() == []
        assert result["rate_limited"] is True
        assert result["retried"] == 0
        assert result["added"] == 1
        assert result["failed"] == 0
        assert result["total_new"] == 3

        rows = _rows(media_import_db, sub_id)
        sync_first_seen = rows["n1"]["first_seen_at"]
        for iid, title, pub in (("n2", "N2", "20261002"), ("n3", None, None)):
            r = rows[iid]
            assert r["status"] == "failed"
            assert r["error_kind"] == "retry_pending"
            assert r["fail_count"] == 0
            assert r["last_attempted_at"] is None
            assert r["first_seen_at"] == sync_first_seen
            assert r["title"] == title
            assert r["published_at"] == pub
        assert (rows["r1"]["error_kind"], rows["r1"]["fail_count"],
                rows["r1"]["last_attempted_at"]) == ("retry_pending", 1, "2026-05-02T00:00:00")
        assert (rows["r2"]["error_kind"], rows["r2"]["fail_count"],
                rows["r2"]["last_attempted_at"]) == ("upcoming", 0, "2026-05-03T00:00:00")

        db = media_import_db()
        try:
            sub = db.execute(
                text("SELECT last_synced_at, cooldown_until FROM subscriptions WHERE id = :id"),
                {"id": sub_id},
            ).mappings().first()
        finally:
            db.close()
        assert sub["last_synced_at"] is not None
        assert sub["cooldown_until"] is None

    def test_spec_addon_028_rate_limited_retry_row_is_unchanged(
        self, mgr, sub_id, provider, media_import_db
    ) -> None:
        _seed_row(
            media_import_db, sub_id, "r1", fail_count=2,
            last_attempted_at="2026-05-02T00:00:00",
        )
        _seed_row(media_import_db, sub_id, "r2", fail_count=0,
                  first_seen_at="2026-06-01T00:00:00")
        provider.script = {"r1": [_rate_limited()]}

        result = mgr._sync_blocking(sub_id)

        assert provider.fetched() == ["r1"]
        assert result["rate_limited"] is True
        assert result["retried"] == 0
        row = _rows(media_import_db, sub_id)["r1"]
        assert (row["status"], row["error_kind"], row["fail_count"],
                row["last_attempted_at"]) == (
            "failed", "retry_pending", 2, "2026-05-02T00:00:00",
        )

    def test_spec_addon_028_rate_limit_stop_is_logged_at_warning(
        self, mgr, sub_id, provider, caplog
    ) -> None:
        provider.headers = [
            ItemHeader(item_id="n1", title="N1"),
            ItemHeader(item_id="n2", title="N2"),
        ]
        provider.script = {"n1": [_rate_limited()]}

        with caplog.at_level(logging.WARNING):
            mgr._sync_blocking(sub_id)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("n1" in r.getMessage() for r in warnings)


# ---- step 8: inter-item delay -------------------------------------


class TestInterItemDelay:
    """SPEC-ADDON-028 / I4: the delay follows every network attempt."""

    def test_spec_addon_028_delay_after_every_attempt_but_not_after_reuse_or_last(
        self, mgr, sub_id, provider, media_import_db, drive_path
    ) -> None:
        provider.inter_item_delay_seconds = _DELAY
        _seed_existing_loft(media_import_db, drive_path, "n2")
        provider.headers = [
            ItemHeader(item_id="n1", title=""),
            ItemHeader(item_id="n2", title="N2"),
            ItemHeader(item_id="n3", title="N3"),
            ItemHeader(item_id="n4", title="N4"),
        ]
        provider.script = {"n1": [_other()], "n3": [_unavailable()]}
        provider.lookup_titles = {"n1": "Looked up"}

        mgr._sync_blocking(sub_id)
        end = time.monotonic()

        times = dict(provider.fetch_calls)
        assert list(times) == ["n1", "n3", "n4"]
        # One sleep after n1, none after the reuse of n2.
        assert _DELAY <= times["n3"] - times["n1"] < 2 * _DELAY
        # A failed attempt (n3) is followed by the delay too.
        assert times["n4"] - times["n3"] >= _DELAY
        # Nothing is slept after the last attempt.
        assert end - times["n4"] < _DELAY
        # The title lookup runs before the delay that follows its attempt.
        lookup_t = dict(provider.lookup_calls)["n1"]
        assert lookup_t - times["n1"] < _DELAY

    def test_spec_addon_028_no_delay_after_rate_limited_attempt(
        self, mgr, sub_id, provider
    ) -> None:
        provider.inter_item_delay_seconds = _DELAY
        provider.headers = [
            ItemHeader(item_id="n1", title="N1"),
            ItemHeader(item_id="n2", title="N2"),
        ]
        provider.script = {"n1": [_rate_limited()]}

        mgr._sync_blocking(sub_id)
        end = time.monotonic()

        assert end - dict(provider.fetch_calls)["n1"] < _DELAY


# ---- steps 3, 4, 9: titles ----------------------------------------


class TestStoredTitles:
    """SPEC-ADDON-029 / I7."""

    def test_spec_addon_029_new_row_stores_header_title_and_date_as_given(
        self, mgr, sub_id, provider, media_import_db
    ) -> None:
        provider.headers = [
            ItemHeader(item_id="n1", title="Members stream", published_at="20260415"),
            ItemHeader(item_id="n2", title="Feed item",
                       published_at="2026-04-30T10:00:00+00:00"),
        ]
        provider.script = {"n1": [_unavailable()], "n2": [_other()]}

        mgr._sync_blocking(sub_id)

        rows = _rows(media_import_db, sub_id)
        assert (rows["n1"]["title"], rows["n1"]["published_at"]) == (
            "Members stream", "20260415",
        )
        assert (rows["n2"]["title"], rows["n2"]["published_at"]) == (
            "Feed item", "2026-04-30T10:00:00+00:00",
        )
        # A stored title means no lookup is made.
        assert provider.looked_up() == []

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    def test_spec_addon_029_blank_header_title_is_stored_as_null(
        self, mgr, sub_id, provider, media_import_db, blank
    ) -> None:
        provider.headers = [ItemHeader(item_id="n1", title=blank)]
        provider.script = {"n1": [_unavailable()]}

        mgr._sync_blocking(sub_id)

        assert _rows(media_import_db, sub_id)["n1"]["title"] is None

    def test_spec_addon_029_listing_fills_null_columns_only(
        self, mgr, sub_id, provider, media_import_db
    ) -> None:
        _seed_row(media_import_db, sub_id, "imp", status="imported", error_kind=None,
                  title=None, published_at=None, last_attempted_at="2026-05-02T00:00:00")
        _seed_row(media_import_db, sub_id, "kept", error_kind="permanent",
                  title="Original", published_at=None)
        _seed_row(media_import_db, sub_id, "dated", error_kind="permanent",
                  title=None, published_at="2026-01-01")
        _seed_row(media_import_db, sub_id, "blank", error_kind="permanent",
                  title=None, published_at=None)
        provider.headers = [
            ItemHeader(item_id="imp", title="Imp title", published_at="20260501"),
            ItemHeader(item_id="kept", title="Renamed later", published_at="20260502"),
            ItemHeader(item_id="dated", title="Dated title", published_at="20260503"),
            ItemHeader(item_id="blank", title="  ", published_at=None),
        ]

        mgr._sync_blocking(sub_id)

        rows = _rows(media_import_db, sub_id)
        assert (rows["imp"]["title"], rows["imp"]["published_at"]) == ("Imp title", "20260501")
        assert rows["imp"]["last_attempted_at"] == "2026-05-02T00:00:00"
        assert (rows["kept"]["title"], rows["kept"]["published_at"]) == ("Original", "20260502")
        assert (rows["dated"]["title"], rows["dated"]["published_at"]) == (
            "Dated title", "2026-01-01",
        )
        assert rows["blank"]["title"] is None
        assert rows["kept"]["last_attempted_at"] == "2026-05-02T00:00:00"


class TestTitleLookup:
    """SPEC-ADDON-029 step 9 / I7."""

    @pytest.mark.parametrize(
        "make_exc", [_unavailable, _not_started, _other],
        ids=["unavailable", "not_started", "other_failure"],
    )
    def test_spec_addon_029_failed_attempt_with_null_title_is_looked_up(
        self, mgr, sub_id, provider, media_import_db, make_exc
    ) -> None:
        provider.headers = [ItemHeader(item_id="n1", title=None)]
        provider.script = {"n1": [make_exc()]}
        provider.lookup_titles = {"n1": "From oEmbed"}

        mgr._sync_blocking(sub_id)

        assert provider.looked_up() == ["n1"]
        assert _rows(media_import_db, sub_id)["n1"]["title"] == "From oEmbed"

    @pytest.mark.parametrize(
        "answer",
        [None, "", "   ", RuntimeError("oEmbed timed out")],
        ids=["none", "empty", "whitespace", "raises"],
    )
    def test_spec_addon_029_lookup_without_answer_changes_nothing_else(
        self, mgr, sub_id, provider, media_import_db, answer
    ) -> None:
        provider.headers = [
            ItemHeader(item_id="n1", title=None),
            ItemHeader(item_id="n2", title="N2"),
        ]
        provider.script = {"n1": [_unavailable(), _other()]}
        provider.lookup_titles = {"n1": answer}

        result = mgr._sync_blocking(sub_id)

        rows = _rows(media_import_db, sub_id)
        assert rows["n1"]["title"] is None
        assert (rows["n1"]["status"], rows["n1"]["error_kind"]) == ("failed", "permanent")
        assert rows["n2"]["status"] == "imported"
        assert result["added"] == 1
        assert result["failed"] == 1

        # Still NULL, so the next failed attempt asks again.
        mgr._sync_blocking(sub_id, item_id="n1")
        assert provider.looked_up() == ["n1", "n1"]

    def test_spec_addon_029_no_lookup_after_import_or_when_title_present(
        self, mgr, sub_id, provider, media_import_db
    ) -> None:
        provider.headers = [
            ItemHeader(item_id="ok", title=None),
            ItemHeader(item_id="titled", title="Has title"),
        ]
        provider.script = {"titled": [_other()]}
        provider.lookup_titles = {"ok": "X", "titled": "Y"}

        mgr._sync_blocking(sub_id)

        assert provider.looked_up() == []
        assert _rows(media_import_db, sub_id)["titled"]["title"] == "Has title"

    def test_spec_addon_029_stored_title_never_replaced_by_lookup(
        self, mgr, sub_id, provider, media_import_db
    ) -> None:
        _seed_row(media_import_db, sub_id, "r1", title="Stored", fail_count=1)
        provider.script = {"r1": [_other()]}
        provider.lookup_titles = {"r1": "Other"}

        mgr._sync_blocking(sub_id)

        assert provider.looked_up() == []
        assert _rows(media_import_db, sub_id)["r1"]["title"] == "Stored"


# ---- step 10: manual Retry ----------------------------------------


class TestManualRetry:
    """SPEC-ADDON-027 Q1, SPEC-ADDON-028 step 10."""

    @pytest.mark.parametrize(
        "step, expected",
        [
            (OK, {"added": 1, "reused": 0, "failed": 0, "total_new": 1,
                  "retried": 1, "rate_limited": False}),
            (_unavailable, {"added": 0, "reused": 0, "failed": 1, "total_new": 0,
                            "retried": 1, "rate_limited": False}),
            (_not_started, {"added": 0, "reused": 0, "failed": 1, "total_new": 0,
                            "retried": 1, "rate_limited": False}),
            (_other, {"added": 0, "reused": 0, "failed": 1, "total_new": 0,
                      "retried": 1, "rate_limited": False}),
            (_rate_limited, {"added": 0, "reused": 0, "failed": 0, "total_new": 0,
                             "retried": 0, "rate_limited": True}),
        ],
        ids=["imported", "unavailable", "not_started", "other_failure", "rate_limited"],
    )
    def test_spec_addon_028_single_item_result_per_outcome(
        self, mgr, sub_id, provider, media_import_db, step, expected
    ) -> None:
        _seed_row(media_import_db, sub_id, "v", error_kind="fetch_failed",
                  fail_count=3, title="V")
        provider.script = {"v": [step() if callable(step) else step]}

        assert mgr._sync_blocking(sub_id, item_id="v") == expected

    def test_spec_addon_028_single_item_result_for_dedup_reuse(
        self, mgr, sub_id, provider, media_import_db, drive_path
    ) -> None:
        _seed_row(media_import_db, sub_id, "v", error_kind="fetch_failed", fail_count=3)
        _seed_existing_loft(media_import_db, drive_path, "v")

        assert mgr._sync_blocking(sub_id, item_id="v") == {
            "added": 0, "reused": 1, "failed": 0, "total_new": 1,
            "retried": 1, "rate_limited": False,
        }

    @pytest.mark.parametrize(
        "error_kind, fail_count, expected",
        [
            ("fetch_failed", 3, ("fetch_failed", 4)),
            ("fetch_failed", 2, ("fetch_failed", 3)),
            ("fetch_failed", 0, ("retry_pending", 1)),
            ("path_conflict", 0, ("retry_pending", 1)),
            ("rate_limited", 1, ("retry_pending", 2)),
            (None, 0, ("retry_pending", 1)),
            ("permanent", 0, ("retry_pending", 1)),
            ("retry_pending", 1, ("retry_pending", 2)),
            ("upcoming", 0, ("retry_pending", 1)),
        ],
    )
    def test_spec_addon_028_manual_retry_other_failure_counts_up(
        self, mgr, sub_id, provider, media_import_db, error_kind, fail_count, expected
    ) -> None:
        _seed_row(media_import_db, sub_id, "v", error_kind=error_kind,
                  fail_count=fail_count, title="V")
        provider.script = {"v": [_other()]}

        mgr._sync_blocking(sub_id, item_id="v")

        row = _rows(media_import_db, sub_id)["v"]
        assert row["status"] == "failed"
        assert (row["error_kind"], row["fail_count"]) == expected

    @pytest.mark.parametrize(
        "step, expected",
        [
            (OK, ("imported", 0)),
            (_unavailable, ("permanent", 0)),
            (_not_started, ("upcoming", 0)),
        ],
        ids=["imported", "unavailable", "not_started"],
    )
    def test_spec_addon_027_manual_retry_of_permanent_row_records_outcome(
        self, mgr, sub_id, provider, media_import_db, drive_path, step, expected
    ) -> None:
        _seed_row(media_import_db, sub_id, "v", error_kind="permanent", title="V")
        provider.script = {"v": [step() if callable(step) else step]}

        mgr._sync_blocking(sub_id, item_id="v")

        row = _rows(media_import_db, sub_id)["v"]
        if expected[0] == "imported":
            assert row["status"] == "imported"
            assert (drive_path / "Imported v.loft").exists()
        else:
            assert (row["status"], row["error_kind"]) == ("failed", expected[0])
        assert row["fail_count"] == expected[1]
        assert row["last_attempted_at"] != "2026-05-02T00:00:00"

    def test_spec_addon_028_rate_limited_manual_retry_changes_nothing(
        self, mgr, sub_id, provider, media_import_db
    ) -> None:
        _seed_row(media_import_db, sub_id, "v", error_kind="fetch_failed",
                  fail_count=3, title=None, last_attempted_at="2026-05-02T00:00:00")
        provider.script = {"v": [_rate_limited()]}
        provider.lookup_titles = {"v": "Should not be asked"}

        mgr._sync_blocking(sub_id, item_id="v")

        row = _rows(media_import_db, sub_id)["v"]
        assert (row["status"], row["error_kind"], row["fail_count"],
                row["last_attempted_at"], row["title"]) == (
            "failed", "fetch_failed", 3, "2026-05-02T00:00:00", None,
        )
        assert provider.looked_up() == []


class TestLogging:
    """SPEC-ADDON-027 section 8."""

    def test_spec_addon_027_classified_failure_logged_at_warning(
        self, mgr, sub_id, provider, caplog
    ) -> None:
        provider.headers = [ItemHeader(item_id="memb1", title="M")]
        provider.script = {"memb1": [_unavailable()]}

        with caplog.at_level(logging.WARNING):
            mgr._sync_blocking(sub_id)

        assert any(
            r.levelno == logging.WARNING and "memb1" in r.getMessage()
            and str(sub_id) in r.getMessage()
            for r in caplog.records
        )
