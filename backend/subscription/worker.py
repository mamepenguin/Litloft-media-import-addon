"""SubscriptionWorker — single-task queue consumer for all sync paths.

Centralizes initial / manual / retry / future cron sync triggers so
global concurrency stays at 1 (hako ``PQPo_oBI5jtK9fuYFpe-B``) and
Phase 3 cron can enqueue via the same entry as HTTP routes.

The worker holds an in-memory ``asyncio.Queue`` plus running/queued
sets guarded by a single mutex. Per hako ``z6wc1bI3g_WQ9_jS0xi69`` we
deliberately do NOT persist the queue: dedup at the ``.loft`` level
means a process restart loses at most "remember to retry", which the
user recovers with one manual sync.

Dedup contract: ``enqueue_sync`` returns ``False`` if the same
subscription_id is already running or queued. This collapses concurrent
retry + manual sync requests; the per-item retry that loses the race
will be re-attempted by the next full sync via dedup_lookup, so no work
is lost.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal

from app.services.ws import broadcast_from_thread

from .manager import SubscriptionManager, SubscriptionNotFound, subscription_manager

logger = logging.getLogger(__name__)


JobKind = Literal["initial", "manual", "retry", "cron"]


@dataclass(frozen=True)
class SyncJob:
    subscription_id: int
    kind: JobKind
    backfill: int | None = None
    item_id: str | None = None  # retry path only; None = full sync


class SubscriptionWorker:
    def __init__(self, manager: SubscriptionManager) -> None:
        self._manager = manager
        self._queue: asyncio.Queue[SyncJob] = asyncio.Queue()
        self._running_ids: set[int] = set()
        self._queued_ids: set[int] = set()
        self._mutex: asyncio.Lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._idle_event: asyncio.Event = asyncio.Event()
        self._idle_event.set()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._run(), name="subscription-worker"
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("worker stop saw unexpected exception")
        self._task = None

    async def enqueue_sync(
        self,
        subscription_id: int,
        *,
        kind: JobKind,
        backfill: int | None = None,
        item_id: str | None = None,
    ) -> bool:
        """Enqueue a sync job; returns True if accepted, False if dedup'd.

        ``put_nowait`` (synchronous, no yield point) is used so that
        adding to queued_ids, clearing idle_event, and putting the job
        all happen atomically under ``_mutex``. Awaiting ``queue.put``
        outside the lock would let the worker's idle check observe
        ``queue.empty() == True`` after we cleared the event, briefly
        marking the worker idle while a job is "in transit".
        """
        async with self._mutex:
            if (
                subscription_id in self._running_ids
                or subscription_id in self._queued_ids
            ):
                return False
            self._queued_ids.add(subscription_id)
            self._idle_event.clear()
            self._queue.put_nowait(
                SyncJob(subscription_id, kind, backfill, item_id)
            )
        return True

    @property
    def running_ids(self) -> frozenset[int]:
        return frozenset(self._running_ids)

    @property
    def queued_ids(self) -> frozenset[int]:
        return frozenset(self._queued_ids)

    async def wait_idle(self, timeout: float | None = None) -> None:
        """Block until queue drained AND no job currently running."""
        if timeout is None:
            await self._idle_event.wait()
        else:
            await asyncio.wait_for(self._idle_event.wait(), timeout)

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            async with self._mutex:
                self._queued_ids.discard(job.subscription_id)
                self._running_ids.add(job.subscription_id)
            try:
                await self._execute(job)
            except Exception:
                logger.exception("worker job execution failed: %s", job)
            finally:
                async with self._mutex:
                    self._running_ids.discard(job.subscription_id)
                    # All three sets must be empty for true idleness:
                    # ``queued_ids`` may still hold an id whose put is
                    # racing with our finally — checking ``queue.empty()``
                    # alone is insufficient.
                    if (
                        not self._running_ids
                        and not self._queued_ids
                        and self._queue.empty()
                    ):
                        self._idle_event.set()
                self._queue.task_done()

    async def _execute(self, job: SyncJob) -> None:
        loop = asyncio.get_running_loop()
        drive = await loop.run_in_executor(
            None, self._lookup_drive_sync, job.subscription_id
        )
        if drive is None:
            logger.info(
                "subscription %s vanished before sync, skipping",
                job.subscription_id,
            )
            return

        self._broadcast(
            "media_import.subscription.sync_started",
            subscription_id=job.subscription_id,
            drive=drive,
            kind=job.kind,
        )

        try:
            result = await loop.run_in_executor(
                None,
                self._manager._sync_blocking,
                job.subscription_id,
                job.backfill,
                job.item_id,
            )
        except SubscriptionNotFound:
            self._broadcast(
                "media_import.subscription.sync_completed",
                subscription_id=job.subscription_id,
                drive=drive,
                error="not_found",
            )
            return
        except Exception:
            logger.exception("sync_blocking failed: %s", job)
            self._broadcast(
                "media_import.subscription.sync_completed",
                subscription_id=job.subscription_id,
                drive=drive,
                error="exception",
            )
            return

        self._broadcast(
            "media_import.subscription.sync_completed",
            subscription_id=job.subscription_id,
            drive=drive,
            **result,
        )

    def _lookup_drive_sync(self, subscription_id: int) -> str | None:
        try:
            return self._manager._load_subscription(subscription_id)["drive"]
        except SubscriptionNotFound:
            return None

    def _broadcast(self, event: str, **payload) -> None:
        try:
            broadcast_from_thread(event, payload, drive=payload.get("drive"))
        except Exception:  # pragma: no cover — best-effort UI notify
            logger.exception("WS broadcast failed: %s", event)


# Module-level singleton; router and registration import this.
subscription_worker = SubscriptionWorker(subscription_manager)
