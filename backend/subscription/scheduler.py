"""SubscriptionScheduler — periodic cron sweep over subscriptions.

Phase 3. The scheduler is intentionally thin: a sweep loop wakes up,
asks the manager for cron-eligible subscription_ids, and forwards each
to ``SubscriptionWorker.enqueue_sync(kind="cron")``. The worker keeps
its dedup contract (running / queued sets), so the scheduler can stay
state-less.

Production drives ``_run`` as a background task; tests drive
``tick_once`` directly with a fake clock to avoid sleeping on real
intervals (the same pattern used elsewhere in the repo, e.g. the
intelligence Ask eval harness).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from .manager import SubscriptionManager, subscription_manager
from .worker import SubscriptionWorker, subscription_worker

logger = logging.getLogger(__name__)


class SubscriptionScheduler:
    SWEEP_INTERVAL_SECONDS: float = 60.0
    STARTUP_GRACE_SECONDS: float = 30.0

    def __init__(
        self,
        manager: SubscriptionManager,
        worker: SubscriptionWorker,
    ) -> None:
        self._manager = manager
        self._worker = worker
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._run(), name="subscription-scheduler"
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception:  # pragma: no cover — defensive
            logger.exception("scheduler stop saw unexpected exception")
        self._task = None

    async def tick_once(self, *, now: datetime | None = None) -> int:
        """Run a single sweep; returns enqueued count.

        Public so tests can drive the scheduler with a fake ``now``
        without depending on the real sleep loop.
        """
        when = now or datetime.now(UTC)
        eligible_ids = self._manager.list_eligible_for_cron(when)
        enqueued = 0
        for sub_id in eligible_ids:
            accepted = await self._worker.enqueue_sync(
                sub_id, kind="cron"
            )
            if accepted:
                enqueued += 1
                logger.info(
                    "scheduler: subscription %d enqueued for cron sync",
                    sub_id,
                )
        return enqueued

    async def _run(self) -> None:
        try:
            await asyncio.sleep(self.STARTUP_GRACE_SECONDS)
            while True:
                try:
                    await self.tick_once()
                except Exception:
                    logger.exception("scheduler sweep failed")
                await asyncio.sleep(self.SWEEP_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            logger.info("scheduler loop cancelled")
            raise


# Module-level singleton; ``router.on_startup`` calls ``.start()``.
subscription_scheduler = SubscriptionScheduler(
    subscription_manager, subscription_worker
)
