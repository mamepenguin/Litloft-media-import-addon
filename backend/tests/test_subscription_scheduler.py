"""Tests for SubscriptionScheduler.

Sweep loop sleeping is deliberately untested: production drives it on a
60-second cadence, but the contract under test is ``tick_once`` (manager
lookup → worker enqueue → dedup honored). Driving the scheduler via the
public ``tick_once`` keeps these tests deterministic; the loop's sleep
behavior is verified by manual smoke testing.

The repo does not use pytest-asyncio; each scenario wraps in
``asyncio.run`` like the worker tests.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class _FakeManager:
    """Stub of SubscriptionManager exposing only the eligibility query."""

    eligible_by_now: dict[str, list[int]] = field(default_factory=dict)
    default_eligible: list[int] = field(default_factory=list)
    calls: list[datetime] = field(default_factory=list)

    def list_eligible_for_cron(self, now: datetime) -> list[int]:
        self.calls.append(now)
        key = now.isoformat()
        return list(self.eligible_by_now.get(key, self.default_eligible))


@dataclass
class _FakeWorker:
    """Stub of SubscriptionWorker tracking enqueue_sync calls."""

    accept: bool = True
    enqueued: list[tuple[int, str]] = field(default_factory=list)
    accept_per_id: dict[int, bool] = field(default_factory=dict)

    async def enqueue_sync(
        self,
        subscription_id: int,
        *,
        kind: str,
        backfill: int | None = None,
        item_id: str | None = None,
    ) -> bool:
        self.enqueued.append((subscription_id, kind))
        return self.accept_per_id.get(subscription_id, self.accept)


def _build_scheduler(manager, worker):
    from addons.media_import.subscription.scheduler import (
        SubscriptionScheduler,
    )

    return SubscriptionScheduler(manager, worker)


# ---- tick_once core behavior --------------------------------------


class TestTickOnce:
    def test_eligible_subscriptions_are_enqueued_with_cron_kind(self):
        mgr = _FakeManager(default_eligible=[1, 2, 3])
        wkr = _FakeWorker()
        sched = _build_scheduler(mgr, wkr)

        async def _scenario():
            return await sched.tick_once()

        enqueued = asyncio.run(_scenario())
        assert enqueued == 3
        assert wkr.enqueued == [(1, "cron"), (2, "cron"), (3, "cron")]

    def test_no_eligible_subscriptions_returns_zero(self):
        mgr = _FakeManager(default_eligible=[])
        wkr = _FakeWorker()
        sched = _build_scheduler(mgr, wkr)

        async def _scenario():
            return await sched.tick_once()

        assert asyncio.run(_scenario()) == 0
        assert wkr.enqueued == []

    def test_dedup_rejection_excluded_from_count(self):
        # Worker rejects subscription 2 (e.g., already running). The
        # scheduler must still issue the call but not count it.
        mgr = _FakeManager(default_eligible=[1, 2, 3])
        wkr = _FakeWorker(accept_per_id={1: True, 2: False, 3: True})
        sched = _build_scheduler(mgr, wkr)

        async def _scenario():
            return await sched.tick_once()

        enqueued = asyncio.run(_scenario())
        assert enqueued == 2
        # All three are still attempted — dedup is the worker's job.
        assert [sid for sid, _ in wkr.enqueued] == [1, 2, 3]

    def test_explicit_now_is_passed_to_manager(self):
        mgr = _FakeManager(default_eligible=[])
        wkr = _FakeWorker()
        sched = _build_scheduler(mgr, wkr)

        fake_now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

        async def _scenario():
            await sched.tick_once(now=fake_now)

        asyncio.run(_scenario())
        assert mgr.calls == [fake_now]


# ---- lifecycle ----------------------------------------------------


class TestLifecycle:
    def test_start_is_idempotent(self):
        mgr = _FakeManager(default_eligible=[])
        wkr = _FakeWorker()
        sched = _build_scheduler(mgr, wkr)

        async def _scenario():
            await sched.start()
            first_task = sched._task  # noqa: SLF001 — test introspection
            await sched.start()
            second_task = sched._task  # noqa: SLF001
            await sched.stop()
            return first_task is second_task

        assert asyncio.run(_scenario()) is True

    def test_stop_cancels_running_loop(self):
        mgr = _FakeManager(default_eligible=[])
        wkr = _FakeWorker()
        sched = _build_scheduler(mgr, wkr)

        async def _scenario():
            await sched.start()
            assert sched._task is not None  # noqa: SLF001
            await sched.stop()
            assert sched._task is None  # noqa: SLF001

        asyncio.run(_scenario())

    def test_stop_without_start_is_noop(self):
        mgr = _FakeManager(default_eligible=[])
        wkr = _FakeWorker()
        sched = _build_scheduler(mgr, wkr)

        async def _scenario():
            await sched.stop()  # must not raise

        asyncio.run(_scenario())
