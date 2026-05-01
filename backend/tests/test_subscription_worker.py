"""Tests for SubscriptionWorker (Phase 2 fix: queue + global serialization).

These tests stub the manager seam so we can assert worker mechanics
(enqueue, dedup, draining, WS broadcast, error isolation) without
running the real .loft writing pipeline. End-to-end manager behavior
is covered by test_subscription_manager.py.

The repo does not use pytest-asyncio; each scenario is wrapped in
``asyncio.run`` for an isolated event loop.
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest


@dataclass
class _FakeManager:
    """Stub of SubscriptionManager that records calls to _sync_blocking."""

    drives: dict[int, str] = field(default_factory=dict)
    calls: list[tuple[int, int | None, str | None]] = field(default_factory=list)
    sleep_seconds: float = 0.0
    raise_on: int | None = None  # subscription_id that should raise
    return_value: dict = field(
        default_factory=lambda: {
            "added": 1, "reused": 0, "failed": 0, "total_new": 1,
        }
    )
    on_call: Callable[[int], None] | None = None

    def _load_subscription(self, subscription_id: int) -> dict:
        from addons.media_import.subscription.manager import (
            SubscriptionNotFound,
        )

        if subscription_id not in self.drives:
            raise SubscriptionNotFound(subscription_id)
        return {"drive": self.drives[subscription_id]}

    def _sync_blocking(
        self,
        subscription_id: int,
        backfill: int | None = None,
        item_id: str | None = None,
    ) -> dict:
        self.calls.append((subscription_id, backfill, item_id))
        if self.on_call is not None:
            self.on_call(subscription_id)
        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)
        if self.raise_on == subscription_id:
            raise RuntimeError(f"forced failure for {subscription_id}")
        return dict(self.return_value)


@pytest.fixture()
def _broadcasts(monkeypatch):
    """Capture broadcast_from_thread calls on the worker module."""
    from addons.media_import.subscription import worker as worker_mod

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        worker_mod,
        "broadcast_from_thread",
        lambda event, payload, drive=None: captured.append((event, payload)),
    )
    return captured


def _build_worker(manager):
    from addons.media_import.subscription.worker import SubscriptionWorker

    return SubscriptionWorker(manager)


async def _drain(w, *, timeout: float = 3.0) -> None:
    """Wait until queue empty and no job running."""
    await w.wait_idle(timeout=timeout)


# ---- enqueue / dedup ----------------------------------------------


class TestEnqueueDedup:
    def test_first_enqueue_returns_true(self, _broadcasts):
        mgr = _FakeManager(drives={1: "d"})
        w = _build_worker(mgr)

        async def _scenario():
            await w.start()
            try:
                accepted = await w.enqueue_sync(1, kind="manual")
                await _drain(w)
            finally:
                await w.stop()
            return accepted

        assert asyncio.run(_scenario()) is True
        assert mgr.calls == [(1, None, None)]

    def test_duplicate_id_while_running_returns_false(self, _broadcasts):
        gate = threading.Event()
        mgr = _FakeManager(
            drives={1: "d"},
            on_call=lambda _sid: gate.wait(timeout=2.0),
        )
        w = _build_worker(mgr)

        async def _scenario():
            await w.start()
            try:
                first = await w.enqueue_sync(1, kind="manual")
                # Wait for worker to pick up the job.
                for _ in range(100):
                    if 1 in w.running_ids:
                        break
                    await asyncio.sleep(0.01)
                running_during = 1 in w.running_ids
                second = await w.enqueue_sync(1, kind="manual")
                gate.set()
                await _drain(w)
                return first, running_during, second
            finally:
                gate.set()
                await w.stop()

        first, running_during, second = asyncio.run(_scenario())
        assert first is True
        assert running_during is True
        assert second is False
        assert mgr.calls == [(1, None, None)]  # only once

    def test_duplicate_id_while_queued_returns_false(self, _broadcasts):
        gate1 = threading.Event()
        mgr = _FakeManager(
            drives={1: "d", 2: "d"},
            on_call=lambda sid: gate1.wait(timeout=2.0) if sid == 1 else None,
        )
        w = _build_worker(mgr)

        async def _scenario():
            await w.start()
            try:
                await w.enqueue_sync(1, kind="manual")
                for _ in range(100):
                    if 1 in w.running_ids:
                        break
                    await asyncio.sleep(0.01)
                # 2 is queued (1 is running, blocking the worker).
                first2 = await w.enqueue_sync(2, kind="manual")
                second2 = await w.enqueue_sync(2, kind="manual")
                gate1.set()
                await _drain(w)
                return first2, second2
            finally:
                gate1.set()
                await w.stop()

        first2, second2 = asyncio.run(_scenario())
        assert first2 is True
        assert second2 is False
        sids = [c[0] for c in mgr.calls]
        assert sids.count(1) == 1
        assert sids.count(2) == 1


# ---- serialization (global concurrency = 1) -----------------------


class TestSerialization:
    def test_two_subscriptions_run_serially(self, _broadcasts):
        in_flight = 0
        max_in_flight = 0
        lock = threading.Lock()

        def _track(_sid):
            nonlocal in_flight, max_in_flight
            with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            time.sleep(0.05)
            with lock:
                in_flight -= 1

        mgr = _FakeManager(drives={1: "d", 2: "d"}, on_call=_track)
        w = _build_worker(mgr)

        async def _scenario():
            await w.start()
            try:
                await w.enqueue_sync(1, kind="manual")
                await w.enqueue_sync(2, kind="manual")
                await _drain(w)
            finally:
                await w.stop()

        asyncio.run(_scenario())
        assert max_in_flight == 1
        assert {c[0] for c in mgr.calls} == {1, 2}


# ---- retry kind passes item_id ------------------------------------


class TestRetryKind:
    def test_item_id_forwarded_to_sync_blocking(self, _broadcasts):
        mgr = _FakeManager(drives={1: "d"})
        w = _build_worker(mgr)

        async def _scenario():
            await w.start()
            try:
                accepted = await w.enqueue_sync(
                    1, kind="retry", item_id="vid_xyz"
                )
                await _drain(w)
            finally:
                await w.stop()
            return accepted

        assert asyncio.run(_scenario()) is True
        assert mgr.calls == [(1, None, "vid_xyz")]


# ---- backfill forwarded -------------------------------------------


class TestBackfillForwarded:
    def test_backfill_passed_through(self, _broadcasts):
        mgr = _FakeManager(drives={1: "d"})
        w = _build_worker(mgr)

        async def _scenario():
            await w.start()
            try:
                await w.enqueue_sync(1, kind="initial", backfill=15)
                await _drain(w)
            finally:
                await w.stop()

        asyncio.run(_scenario())
        assert mgr.calls == [(1, 15, None)]


# ---- error isolation ----------------------------------------------


class TestErrorIsolation:
    def test_exception_does_not_kill_loop(self, _broadcasts):
        mgr = _FakeManager(drives={1: "d", 2: "d"}, raise_on=1)
        w = _build_worker(mgr)

        async def _scenario():
            await w.start()
            try:
                await w.enqueue_sync(1, kind="manual")
                await w.enqueue_sync(2, kind="manual")
                await _drain(w)
            finally:
                await w.stop()

        asyncio.run(_scenario())
        sids = [c[0] for c in mgr.calls]
        assert 1 in sids
        assert 2 in sids


# ---- WS broadcast -------------------------------------------------


class TestBroadcasts:
    def test_started_and_completed_emitted_with_drive(self, _broadcasts):
        mgr = _FakeManager(drives={42: "mydrive"})
        w = _build_worker(mgr)

        async def _scenario():
            await w.start()
            try:
                await w.enqueue_sync(42, kind="manual", backfill=15)
                await _drain(w)
            finally:
                await w.stop()

        asyncio.run(_scenario())
        events = [
            (e, p["subscription_id"], p.get("drive")) for e, p in _broadcasts
        ]
        assert ("media_import.subscription.sync_started", 42, "mydrive") in events
        assert ("media_import.subscription.sync_completed", 42, "mydrive") in events
        completed = [
            p for e, p in _broadcasts
            if e == "media_import.subscription.sync_completed"
        ][0]
        assert completed["added"] == 1
        assert completed["total_new"] == 1
        assert "error" not in completed

    def test_completed_carries_error_marker_on_exception(self, _broadcasts):
        mgr = _FakeManager(drives={1: "d"}, raise_on=1)
        w = _build_worker(mgr)

        async def _scenario():
            await w.start()
            try:
                await w.enqueue_sync(1, kind="manual")
                await _drain(w)
            finally:
                await w.stop()

        asyncio.run(_scenario())
        completed = [
            p for e, p in _broadcasts
            if e == "media_import.subscription.sync_completed"
        ][0]
        assert completed.get("error") == "exception"

    def test_vanished_subscription_skipped_quietly(self, _broadcasts):
        mgr = _FakeManager(drives={})
        w = _build_worker(mgr)

        async def _scenario():
            await w.start()
            try:
                await w.enqueue_sync(99, kind="manual")
                await _drain(w)
            finally:
                await w.stop()

        asyncio.run(_scenario())
        assert [e for e, _ in _broadcasts] == []
        assert mgr.calls == []


# ---- running_ids API for HTTP layer -------------------------------


class TestRunningIdsApi:
    def test_running_ids_reflects_in_flight(self, _broadcasts):
        gate = threading.Event()
        mgr = _FakeManager(
            drives={7: "d"},
            on_call=lambda _sid: gate.wait(timeout=2.0),
        )
        w = _build_worker(mgr)

        async def _scenario():
            await w.start()
            try:
                empty_before = w.running_ids
                await w.enqueue_sync(7, kind="manual")
                for _ in range(100):
                    if 7 in w.running_ids:
                        break
                    await asyncio.sleep(0.01)
                during = w.running_ids
                gate.set()
                await _drain(w)
                empty_after = w.running_ids
                return empty_before, during, empty_after
            finally:
                gate.set()
                await w.stop()

        empty_before, during, empty_after = asyncio.run(_scenario())
        assert empty_before == frozenset()
        assert during == frozenset({7})
        assert empty_after == frozenset()
