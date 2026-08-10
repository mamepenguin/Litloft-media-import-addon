"""SubscriptionManager — drives sync for one subscription.

Responsibilities:

- ``create``         — resolve URL via the registry, canonicalize the
  ref if the provider returns a non-canonical channel form, INSERT the
  ``subscriptions`` row.
- ``_sync_blocking`` — synchronous executor called by SubscriptionWorker
  (see ``subscription/worker.py``). Runs either a full diff/import pass
  (``item_id is None``) or a single-item retry. Concurrency is the
  worker's responsibility; this method assumes serial invocation.
- ``delete``         — DELETE the row (CASCADE drops subscription_videos).

The manager intentionally keeps the .loft writing path narrow: it
mirrors ``LoftManager.create_loft_sync`` (single-URL import) so a video
that came in via either flow ends up in the same DB shape. Captions are
stored next to the .loft; that location is what
``LoftManager._retry_failed_captions`` already polls, so subscription
items inherit the standard caption-recovery behavior for free.

DB persistence lives in :mod:`subscription.db`. Manager methods stay
as thin wrappers so existing tests that call ``mgr._set_cooldown_until``
etc. keep working.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import app.config as config
from app.services.ws import broadcast_from_thread

from ..service import (
    _save_loft_thumbnail,
    _save_subscription_avatar,
    _youtube_thumbnail_fallbacks,
)
from . import db as subdb
from .registry import (
    REF_KIND_CHANNEL,
    REF_KIND_VIDEO,
    SourceMetadata,
    SubscriptionProvider,
    SubscriptionRef,
    find_subscription_provider_by_url,
    get_subscription_provider,
)


logger = logging.getLogger(__name__)


# ---- Public exceptions --------------------------------------------


class SubscriptionNotFound(Exception):
    """No subscription row matches the given id."""


# ---- yt-dlp indirection (module-level for test patching) ---------


def _resolve_channel_id_via_yt_dlp(url: str) -> str | None:
    """Return the canonical ``UC...`` id for a YouTube channel URL.

    Wrapped at module-level so tests can patch the network call without
    spinning up yt-dlp.
    """
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "playlistend": 1,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # pragma: no cover — exercised via integration
        logger.warning("yt-dlp failed to canonicalize %s: %s", url, exc)
        return None
    if not info:
        return None
    cid = info.get("channel_id") or info.get("uploader_id")
    return cid if isinstance(cid, str) and cid.startswith("UC") else None


# ---- File path helpers --------------------------------------------


def _sanitize_filename(title: str) -> str:
    """Mirror service.py — kept local to avoid a cross-module import cycle."""
    import re

    title = re.sub(r'[<>:"/\\|?*]', "_", title)
    title = title.strip(". ")
    return title[:200] if title else "untitled"


def _allocate_loft_path(
    drive_path: Path, folder_path: str, title: str
) -> Path:
    """Resolve a unique ``Title.loft`` path under ``drive/folder``.

    ``folder_path`` is user input from ``SubscriptionCreateRequest``.
    ``pathlib`` ``/`` does not interpret ``..`` segments, so we resolve
    to an absolute path and verify it lives under ``drive_path`` before
    creating any directories or writing files. Without this check a
    crafted ``folder_path`` like ``../../etc`` would let mkdir + write
    escape the drive root irreversibly (see hako PKehLyI3eRqO3Vl6Fv5iy).
    """
    drive_root = drive_path.resolve()
    target = (drive_path / folder_path).resolve() if folder_path else drive_root
    if not (target == drive_root or target.is_relative_to(drive_root)):
        raise ValueError(
            f"folder_path escapes drive root: {folder_path!r}"
        )
    target.mkdir(parents=True, exist_ok=True)
    safe = _sanitize_filename(title)
    candidate = target / f"{safe}.loft"
    counter = 1
    while candidate.exists():
        candidate = target / f"{safe} ({counter}).loft"
        counter += 1
    return candidate


def _save_vtt(loft_path: Path, vtt_text: str) -> None:
    vtt_path = loft_path.with_suffix(".vtt")
    vtt_path.write_text(vtt_text, encoding="utf-8")


# ---- Manager ------------------------------------------------------


class SubscriptionManager:
    """Owns DB writes for one subscription.

    Concurrency / serialization is the worker's responsibility (see
    ``subscription/worker.py``); this class is a stateless helper from
    the worker's perspective. Direct callers in tests invoke
    ``_sync_blocking`` synchronously.
    """

    # Cron backoff ladder. Inferred state-less from the existing
    # (last_synced_at, cooldown_until) pair, so no consecutive_failures
    # column is needed (hako z6wc1bI3g_WQ9_jS0xi69).
    BACKOFF_LADDER_MINUTES: tuple[int, ...] = (60, 240, 1440)

    # ---- create ---------------------------------------------------

    def create(
        self,
        *,
        url: str,
        drive: str,
        folder_path: str = "",
        cooldown_minutes: int = 60,
        include_no_transcript: bool = False,
        display_mode: str = "library",
    ) -> int:
        match = find_subscription_provider_by_url(url)
        if match is None:
            raise ValueError(f"No subscription provider matched URL: {url!r}")
        provider, ref = match
        if ref.kind == REF_KIND_VIDEO:
            raise ValueError(
                "URL points at a single video, not a subscription source"
            )
        if ref.kind == REF_KIND_CHANNEL and not ref.ref.startswith("UC"):
            ref = self._canonicalize_channel(ref)

        sub_id = subdb.insert_subscription(
            provider=provider.name,
            source_kind=ref.kind,
            source_ref=ref.ref,
            drive=drive,
            folder_path=folder_path,
            cooldown_minutes=cooldown_minutes,
            include_no_transcript=include_no_transcript,
            display_mode=display_mode,
        )

        # Best-effort source-metadata pull (avatar + display title).
        # Failures here must not abort subscription creation — the row
        # is committed and the UI can refresh metadata later via the
        # dedicated route.
        try:
            self._refresh_source_metadata(sub_id, provider, ref)
        except Exception:
            logger.exception(
                "Failed to fetch source metadata at create-time for "
                "subscription=%s",
                sub_id,
            )

        return sub_id

    def _refresh_source_metadata(
        self,
        subscription_id: int,
        provider: SubscriptionProvider,
        ref: SubscriptionRef,
    ) -> SourceMetadata | None:
        """Fetch source metadata via the provider and persist it.

        Avatar download is wired through the shared helper so any future
        provider gains the same on-disk layout for free (hako
        ``IpF19kUI3OKoY_ps7iKg1``: contract drift defense).
        """
        meta = provider.fetch_source_metadata(ref)
        if meta is None:
            return None
        if meta.avatar_url:
            _save_subscription_avatar(subscription_id, meta.avatar_url)
        subdb.update_source_metadata(
            subscription_id,
            avatar_url=meta.avatar_url,
            display_title=meta.title,
        )
        return meta

    def refresh_source_metadata(self, subscription_id: int) -> bool:
        """Public entry point for manual ``refresh-metadata`` UI button.

        Returns True when the provider returned non-None metadata
        (i.e. at least one of avatar / title was updated). Callers in
        the router translate this to 200 / 204 / 404.
        """
        sub = self._load_subscription(subscription_id)
        provider = get_subscription_provider(sub["provider"])
        if provider is None:
            raise ValueError(f"Provider not registered: {sub['provider']}")
        ref = SubscriptionRef(
            kind=sub["source_kind"], ref=sub["source_ref"]
        )
        return self._refresh_source_metadata(
            subscription_id, provider, ref
        ) is not None

    def _canonicalize_channel(
        self, ref: SubscriptionRef
    ) -> SubscriptionRef:
        # Accepts ``@handle`` / ``c/foo`` / ``user/foo`` and returns a
        # ``UC...`` ref so RSS / API URLs can be built later. All three
        # legacy forms append directly to the YouTube root — yt-dlp's
        # extract_info handles each shape uniformly and returns the
        # canonical channel_id.
        url = f"https://www.youtube.com/{ref.ref}"
        canonical = _resolve_channel_id_via_yt_dlp(url)
        if not canonical:
            raise ValueError(
                f"Failed to canonicalize channel ref: {ref.ref!r}"
            )
        return SubscriptionRef(kind=REF_KIND_CHANNEL, ref=canonical)

    # ---- delete ---------------------------------------------------

    def delete(self, subscription_id: int) -> None:
        subdb.delete_subscription(subscription_id)

    # ---- sync (single entry, called from worker) -----------------

    def _sync_blocking(
        self,
        subscription_id: int,
        backfill: int | None = None,
        item_id: str | None = None,
    ) -> dict:
        """Synchronous sync executor — called by SubscriptionWorker.

        ``item_id is None`` runs a full diff-and-import pass. ``item_id``
        non-None runs a single-item retry: the row must already exist in
        ``subscription_videos`` (it is a retry, not a fresh discovery)
        and we return a one-item result for parity with full sync.
        """
        sub = self._load_subscription(subscription_id)
        provider = get_subscription_provider(sub["provider"])
        if provider is None:
            raise ValueError(f"Provider not registered: {sub['provider']}")
        ref = SubscriptionRef(
            kind=sub["source_kind"], ref=sub["source_ref"]
        )

        if item_id is not None:
            return self._sync_single_item(
                subscription_id, sub, provider, ref, item_id
            )

        return self._sync_diff(
            subscription_id, sub, provider, ref, backfill
        )

    def _sync_diff(
        self, subscription_id, sub, provider, ref, backfill
    ) -> dict:
        # Opportunistic backfill for installs that pre-date Phase 4:
        # the row exists with NULL avatar/display_title because the
        # create-time fetch happened before the column existed. One-shot
        # network call gated on missing data, so cron syncs of fully
        # populated subscriptions don't pay the cost.
        if not sub.get("display_title") and not sub.get("avatar_url"):
            try:
                self._refresh_source_metadata(subscription_id, provider, ref)
            except Exception:
                logger.exception(
                    "Opportunistic source-metadata fetch failed for "
                    "subscription=%s",
                    subscription_id,
                )

        headers = provider.list_items(ref, limit=backfill)
        upstream_ids = [h.item_id for h in headers]

        seen = self._load_seen_item_ids(subscription_id)
        new_ids = [iid for iid in upstream_ids if iid not in seen]

        added = reused = failed = 0
        first_seen = datetime.now(UTC).isoformat()
        for idx, item_id in enumerate(new_ids):
            try:
                outcome = self._import_one_item(
                    provider=provider,
                    ref=ref,
                    item_id=item_id,
                    drive=sub["drive"],
                    folder_path=sub["folder_path"],
                    include_no_transcript=bool(sub["include_no_transcript"]),
                )
            except Exception:
                logger.exception(
                    "Subscription %s: failed to import %s",
                    subscription_id, item_id,
                )
                self._record_video_safe(
                    subscription_id, item_id, status="failed",
                    file_id=None, first_seen=first_seen,
                    error_kind="fetch_failed",
                )
                failed += 1
                continue

            self._record_video_safe(
                subscription_id, item_id, status="imported",
                file_id=outcome.file_id, first_seen=first_seen,
                error_kind=outcome.transcript_error,
            )
            if outcome.reused:
                reused += 1
            else:
                added += 1

            # Throttle only between freshly-fetched items; reused items
            # didn't hit the network.
            if (
                not outcome.reused
                and idx < len(new_ids) - 1
                and provider.inter_item_delay_seconds > 0
            ):
                time.sleep(provider.inter_item_delay_seconds)

        self._touch_last_synced_at(subscription_id)
        # Successful full sync resets any pending cron backoff. Single-
        # item retry path (``_sync_single_item``) deliberately does not
        # touch this column — retries are user-initiated and shouldn't
        # interfere with cron's backoff state machine.
        self._clear_cooldown_until(subscription_id)
        return {
            "added": added,
            "reused": reused,
            "failed": failed,
            "total_new": len(new_ids),
        }

    def _sync_single_item(
        self, subscription_id, sub, provider, ref, item_id
    ) -> dict:
        # Confirm the row exists before any network work.
        existing = subdb.load_video_row(subscription_id, item_id)
        if existing is None:
            raise SubscriptionNotFound(
                f"video not found: ({subscription_id}, {item_id})"
            )
        first_seen = existing["first_seen_at"]

        try:
            outcome = self._import_one_item(
                provider=provider,
                ref=ref,
                item_id=item_id,
                drive=sub["drive"],
                folder_path=sub["folder_path"],
                include_no_transcript=bool(sub["include_no_transcript"]),
            )
        except Exception:
            logger.exception(
                "Retry failed for subscription=%s item=%s",
                subscription_id, item_id,
            )
            self._record_video_safe(
                subscription_id, item_id, status="failed",
                file_id=None, first_seen=first_seen,
                error_kind="fetch_failed",
            )
            return {"added": 0, "reused": 0, "failed": 1, "total_new": 0}

        self._record_video_safe(
            subscription_id, item_id, status="imported",
            file_id=outcome.file_id, first_seen=first_seen,
            error_kind=outcome.transcript_error,
        )
        return {
            "added": 0 if outcome.reused else 1,
            "reused": 1 if outcome.reused else 0,
            "failed": 0,
            "total_new": 1,
        }

    # ---- DB helpers (thin delegations to subscription.db) --------

    def _load_subscription(self, subscription_id: int) -> dict:
        row = subdb.load_subscription(subscription_id)
        if row is None:
            raise SubscriptionNotFound(subscription_id)
        return row

    def _load_seen_item_ids(self, subscription_id: int) -> set[str]:
        return subdb.load_seen_item_ids(subscription_id)

    def _record_video_safe(
        self,
        subscription_id: int,
        item_id: str,
        *,
        status: str,
        file_id: str | None,
        first_seen: str,
        error_kind: str | None = None,
    ) -> None:
        """``upsert_video_row`` wrapper that swallows write errors.

        Called from the per-item loop after the .loft / DB rows are
        already committed. A subscription_videos write failure here
        would corrupt accounting, but the work itself is done — next
        sync re-discovers the upstream id and the dedup query reuses
        the existing file_id, so the recorded state self-heals. Log
        loudly instead of failing the batch.
        """
        try:
            subdb.upsert_video_row(
                subscription_id, item_id,
                status=status, file_id=file_id,
                first_seen=first_seen, error_kind=error_kind,
            )
        except Exception:
            logger.exception(
                "Failed to write subscription_videos row "
                "(subscription=%s item=%s status=%s); next sync will "
                "self-heal via dedup",
                subscription_id, item_id, status,
            )

    def _record_video(
        self,
        subscription_id: int,
        item_id: str,
        *,
        status: str,
        file_id: str | None,
        first_seen: str,
        error_kind: str | None = None,
    ) -> None:
        subdb.upsert_video_row(
            subscription_id, item_id,
            status=status, file_id=file_id,
            first_seen=first_seen, error_kind=error_kind,
        )

    def _touch_last_synced_at(self, subscription_id: int) -> None:
        subdb.touch_last_synced_at(subscription_id)

    # ---- Cron eligibility & backoff ------------------------------

    def list_eligible_for_cron(self, now: datetime) -> list[int]:
        """Return subscription_ids whose cron sync should fire at ``now``.

        Eligibility predicate (all must hold):
        - ``is_enabled = 1``
        - ``last_synced_at`` is NULL, or
          ``now - last_synced_at >= cooldown_minutes``
        - ``cooldown_until`` is NULL or already in the past
        """
        return subdb.load_eligible_cron_rows(now, subdb.cron_due)

    def _set_cooldown_until(
        self, subscription_id: int, until: datetime
    ) -> None:
        subdb.set_cooldown_until(subscription_id, until)

    def _clear_cooldown_until(self, subscription_id: int) -> None:
        subdb.clear_cooldown_until(subscription_id)

    def _next_backoff_minutes(self, subscription_id: int) -> int:
        """Pick the next ladder rung from existing timestamps.

        State-less inference: the gap between the *current*
        ``cooldown_until`` and ``last_synced_at`` represents the rung
        applied at the previous failure. The next rung is the smallest
        ladder value strictly larger than that gap; the ladder caps at
        the last entry. With no prior cooldown recorded the first rung
        applies.
        """
        state = subdb.load_cooldown_state(subscription_id)
        if state is None:
            raise SubscriptionNotFound(subscription_id)
        ls, cu = state
        if cu is None or ls is None:
            return self.BACKOFF_LADDER_MINUTES[0]

        prev_minutes = max(1, int((cu - ls).total_seconds() // 60))
        for rung in self.BACKOFF_LADDER_MINUTES:
            if prev_minutes < rung:
                return rung
        return self.BACKOFF_LADDER_MINUTES[-1]

    # ---- Per-item import -----------------------------------------

    def _import_one_item(
        self,
        *,
        provider: SubscriptionProvider,
        ref: SubscriptionRef,
        item_id: str,
        drive: str,
        folder_path: str,
        include_no_transcript: bool,
    ) -> "_ImportOutcome":
        existing = subdb.lookup_dedup(
            drive, folder_path, provider.name, item_id
        )
        if existing is not None:
            return _ImportOutcome(
                file_id=existing, reused=True, transcript_error=None
            )

        meta = provider.fetch_item(ref, item_id)
        drive_path = config.get_drive_path(drive)
        loft_path = _allocate_loft_path(drive_path, folder_path, meta.title)
        body = provider.build_loft_content(meta)
        loft_path.write_text(
            json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        try:
            file_id = subdb.register_loft(drive, loft_path, provider.name, meta)
        except Exception:
            if loft_path.exists():
                loft_path.unlink()
            raise

        # Mirror the /link pipeline so subscription-imported .loft files
        # get the same thumbnail UX as single-link imports. Pass
        # ``loft_path.name`` rather than ``meta.title`` because
        # _allocate_loft_path may have appended a "(1)" suffix or run
        # _sanitize_filename — the thumb_rel must match the on-disk
        # filename for the core thumbnail endpoint to find it.
        # YouTube's localized-captions thumbnail (``/vi_lc/.../*_en-US.jpg``)
        # 404s for most videos, so wire up the canonical
        # ``i.ytimg.com/vi/<id>/...`` chain as a fallback.
        thumbnail_fallbacks = (
            _youtube_thumbnail_fallbacks(item_id)
            if provider.name == "youtube"
            else []
        )
        _save_loft_thumbnail(
            file_id=file_id,
            drive=drive,
            folder_path=folder_path,
            filename=loft_path.name,
            thumbnail_url=meta.thumbnail_url,
            fallback_urls=thumbnail_fallbacks,
        )

        transcript_error: str | None = None
        if meta.has_captions or include_no_transcript:
            tr = provider.fetch_transcript(
                ref, item_id, language=meta.language
            )
            if tr.vtt_text is not None:
                _save_vtt(loft_path, tr.vtt_text)
                subdb.update_caption_state(file_id, ok=True, error_kind=None)
            else:
                transcript_error = tr.error_kind
                subdb.update_caption_state(
                    file_id, ok=False, error_kind=transcript_error
                )

        try:
            broadcast_from_thread(
                "files.updated",
                {"file_id": file_id, "drive": drive},
                drive=drive,
            )
        except Exception:  # pragma: no cover — best-effort UI notify
            pass

        return _ImportOutcome(
            file_id=file_id, reused=False, transcript_error=transcript_error
        )


# ---- _ImportOutcome -----------------------------------------------


class _ImportOutcome:
    __slots__ = ("file_id", "reused", "transcript_error")

    def __init__(
        self, *, file_id: str, reused: bool, transcript_error: str | None
    ) -> None:
        self.file_id = file_id
        self.reused = reused
        self.transcript_error = transcript_error


# Module-level singleton — router imports this for use in route handlers.
subscription_manager = SubscriptionManager()
