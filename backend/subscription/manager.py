"""SubscriptionManager — drives sync for one subscription.

Responsibilities (Phase 2 Commit 3c):

- ``create``  — resolve URL via the registry, canonicalize the ref if
  the provider returns a non-canonical channel form, INSERT the
  ``subscriptions`` row.
- ``sync``    — under a per-id ``asyncio.Lock``, run one diff/import
  pass: ``provider.list_items`` → diff against ``subscription_videos``
  → for each new item, dedup against existing ``loft_metadata`` (same
  drive + folder) or fetch + create the .loft + transcript.
- ``delete``  — DELETE the row (CASCADE drops subscription_videos).

The manager intentionally keeps the .loft writing path narrow: it
mirrors ``LoftManager.create_loft_sync`` (single-URL import) so a video
that came in via either flow ends up in the same DB shape. Captions are
stored next to the .loft; that location is what
``LoftManager._retry_failed_captions`` already polls, so subscription
items inherit the standard caption-recovery behavior for free.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

import app.config as config
from app.database import SessionLocal
from app.services.scanner import register_single_file
from app.services.ws import broadcast_from_thread

from .registry import (
    REF_KIND_CHANNEL,
    REF_KIND_VIDEO,
    SubscriptionProvider,
    SubscriptionRef,
    find_subscription_provider_by_url,
    get_subscription_provider,
)


logger = logging.getLogger(__name__)


# ---- Public exceptions --------------------------------------------


class SubscriptionConflict(Exception):
    """A sync is already running for this subscription."""


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


# ---- Manager ------------------------------------------------------


class SubscriptionManager:
    def __init__(self) -> None:
        # In-flight set guarded by a single async mutex. Per-id Lock
        # would TOCTOU under concurrent requests (two callers can both
        # observe ``Lock.locked() == False`` before either acquires) and
        # also leak entries forever (hako 4GQX1-KucQ1lbCeLCylwe). The
        # check-and-add inside ``_inflight_mutex`` is atomic, and the
        # set is bounded by the number of concurrent syncs.
        self._inflight: set[int] = set()
        self._inflight_mutex: asyncio.Lock = asyncio.Lock()

    async def _claim_inflight(self, subscription_id: int) -> None:
        """Mark this id as in-flight or raise SubscriptionConflict."""
        async with self._inflight_mutex:
            if subscription_id in self._inflight:
                raise SubscriptionConflict(
                    f"sync already running for subscription {subscription_id}"
                )
            self._inflight.add(subscription_id)

    def _release_inflight(self, subscription_id: int) -> None:
        self._inflight.discard(subscription_id)

    def is_running(self, subscription_id: int) -> bool:
        """Return True iff a sync / retry currently holds the in-flight slot.

        Test-only escape hatch — production callers should observe the
        409 contract instead of polling this.
        """
        return subscription_id in self._inflight

    # ---- create ---------------------------------------------------

    def create(
        self,
        *,
        url: str,
        drive: str,
        folder_path: str = "",
        cooldown_minutes: int = 60,
        include_no_transcript: bool = False,
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

        now = datetime.now(UTC).isoformat()
        db = SessionLocal()
        try:
            result = db.execute(
                text(
                    "INSERT INTO subscriptions "
                    "(provider, source_kind, source_ref, drive, folder_path, "
                    " is_enabled, cooldown_minutes, include_no_transcript, "
                    " created_at) "
                    "VALUES (:provider, :kind, :ref, :drive, :folder, "
                    " 1, :cd, :inc, :created_at) "
                    "RETURNING id"
                ),
                {
                    "provider": provider.name,
                    "kind": ref.kind,
                    "ref": ref.ref,
                    "drive": drive,
                    "folder": folder_path,
                    "cd": cooldown_minutes,
                    "inc": int(include_no_transcript),
                    "created_at": now,
                },
            )
            sub_id = result.scalar_one()
            db.commit()
            return int(sub_id)
        finally:
            db.close()

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
        db = SessionLocal()
        try:
            db.execute(
                text("DELETE FROM subscriptions WHERE id = :id"),
                {"id": subscription_id},
            )
            db.commit()
        finally:
            db.close()

    # ---- sync -----------------------------------------------------

    async def sync(
        self, subscription_id: int, backfill: int | None = None
    ) -> dict:
        await self._claim_inflight(subscription_id)
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._sync_blocking, subscription_id, backfill
            )
        finally:
            self._release_inflight(subscription_id)

    def _sync_blocking(
        self, subscription_id: int, backfill: int | None
    ) -> dict:
        sub = self._load_subscription(subscription_id)
        provider = get_subscription_provider(sub["provider"])
        if provider is None:
            raise ValueError(f"Provider not registered: {sub['provider']}")
        ref = SubscriptionRef(
            kind=sub["source_kind"], ref=sub["source_ref"]
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
        return {
            "added": added,
            "reused": reused,
            "failed": failed,
            "total_new": len(new_ids),
        }

    # ---- retry single item ---------------------------------------

    async def retry_item(
        self, subscription_id: int, item_id: str
    ) -> dict:
        """Re-attempt one previously-failed item under the per-id Lock.

        Returns the same shape as ``sync`` so the route handler can hand
        it back uniformly. The row must already exist in
        ``subscription_videos`` (this is a retry, not a fresh discovery);
        callers that don't have one should run a full sync instead.
        """
        await self._claim_inflight(subscription_id)
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._retry_blocking, subscription_id, item_id
            )
        finally:
            self._release_inflight(subscription_id)

    def _retry_blocking(
        self, subscription_id: int, item_id: str
    ) -> dict:
        sub = self._load_subscription(subscription_id)
        provider = get_subscription_provider(sub["provider"])
        if provider is None:
            raise ValueError(f"Provider not registered: {sub['provider']}")

        # Confirm the row exists before doing any network work.
        db = SessionLocal()
        try:
            existing = db.execute(
                text(
                    "SELECT first_seen_at FROM subscription_videos "
                    "WHERE subscription_id = :sid AND item_id = :iid"
                ),
                {"sid": subscription_id, "iid": item_id},
            ).first()
        finally:
            db.close()
        if existing is None:
            raise SubscriptionNotFound(
                f"video not found: ({subscription_id}, {item_id})"
            )
        first_seen = existing[0]

        ref = SubscriptionRef(
            kind=sub["source_kind"], ref=sub["source_ref"]
        )
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

    # ---- DB helpers ----------------------------------------------

    def _load_subscription(self, subscription_id: int) -> dict:
        db = SessionLocal()
        try:
            row = db.execute(
                text(
                    "SELECT provider, source_kind, source_ref, drive, "
                    " folder_path, include_no_transcript "
                    "FROM subscriptions WHERE id = :id"
                ),
                {"id": subscription_id},
            ).mappings().first()
        finally:
            db.close()
        if row is None:
            raise SubscriptionNotFound(subscription_id)
        return dict(row)

    def _load_seen_item_ids(self, subscription_id: int) -> set[str]:
        db = SessionLocal()
        try:
            rows = db.execute(
                text(
                    "SELECT item_id FROM subscription_videos "
                    "WHERE subscription_id = :id"
                ),
                {"id": subscription_id},
            ).fetchall()
        finally:
            db.close()
        return {r[0] for r in rows}

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
        """``_record_video`` wrapper that swallows write errors.

        Called from the per-item loop after the .loft / DB rows are
        already committed. A subscription_videos write failure here
        would corrupt accounting, but the work itself is done — next
        sync re-discovers the upstream id and the dedup query reuses
        the existing file_id, so the recorded state self-heals. Log
        loudly instead of failing the batch.
        """
        try:
            self._record_video(
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
        now = datetime.now(UTC).isoformat()
        db = SessionLocal()
        try:
            db.execute(
                text(
                    "INSERT INTO subscription_videos "
                    "(subscription_id, item_id, status, error_kind, "
                    " file_id, first_seen_at, last_attempted_at) "
                    "VALUES (:sid, :iid, :status, :err, :fid, :first, :last) "
                    "ON CONFLICT(subscription_id, item_id) DO UPDATE SET "
                    " status = excluded.status, "
                    " error_kind = excluded.error_kind, "
                    " file_id = excluded.file_id, "
                    " last_attempted_at = excluded.last_attempted_at"
                ),
                {
                    "sid": subscription_id,
                    "iid": item_id,
                    "status": status,
                    "err": error_kind,
                    "fid": file_id,
                    "first": first_seen,
                    "last": now,
                },
            )
            db.commit()
        finally:
            db.close()

    def _touch_last_synced_at(self, subscription_id: int) -> None:
        db = SessionLocal()
        try:
            db.execute(
                text(
                    "UPDATE subscriptions SET last_synced_at = :ts "
                    "WHERE id = :id"
                ),
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "id": subscription_id,
                },
            )
            db.commit()
        finally:
            db.close()

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
        existing = _lookup_dedup(drive, folder_path, provider.name, item_id)
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
            file_id = _register_loft_in_db(drive, loft_path, provider, meta)
        except Exception:
            if loft_path.exists():
                loft_path.unlink()
            raise

        transcript_error: str | None = None
        if meta.has_captions or include_no_transcript:
            tr = provider.fetch_transcript(
                ref, item_id, language=meta.language
            )
            if tr.vtt_text is not None:
                _save_vtt(loft_path, tr.vtt_text)
                _update_caption_state(file_id, ok=True, error_kind=None)
            else:
                transcript_error = tr.error_kind
                _update_caption_state(
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


# ---- DB helpers (module-level for clarity) -----------------------


def _lookup_dedup(
    drive: str, folder_path: str, provider_name: str, item_id: str
) -> str | None:
    """Return file_id of an existing .loft for the same dedup tuple, or None.

    Per hako ``FSrqtHVrv9B8NW3n2vb22``, dedup key is
    (drive, folder_path, provider, provider_item_id). The query joins
    loft_metadata with files to honor the drive + folder filter and
    skips soft-deleted / missing files (those should not be reused —
    the user removed them on purpose).
    """
    db = SessionLocal()
    try:
        row = db.execute(
            text(
                "SELECT m.file_id FROM loft_metadata m "
                "JOIN files f ON f.id = m.file_id "
                "WHERE m.provider = :provider "
                "  AND m.provider_item_id = :iid "
                "  AND f.drive = :drive "
                "  AND f.folder_path = :folder "
                "  AND f.deleted_at IS NULL "
                "  AND f.missing_since IS NULL "
                "LIMIT 1"
            ),
            {
                "provider": provider_name,
                "iid": item_id,
                "drive": drive,
                "folder": folder_path,
            },
        ).first()
    finally:
        db.close()
    return row[0] if row else None


_ALLOWED_LOFT_URL_SCHEMES: tuple[str, ...] = ("http://", "https://")


def _register_loft_in_db(
    drive: str,
    loft_path: Path,
    provider: SubscriptionProvider,
    meta,
) -> str:
    """INSERT files + loft_metadata rows for the freshly written .loft."""
    # The ``SubscriptionProvider`` Protocol does not enforce that
    # ``canonical_url`` is HTTP(S). YouTubeProvider always builds
    # ``https://www.youtube.com/...``, but a future provider could
    # return ``javascript:...`` and downstream `<a href={url}>` would
    # then fire on click. Reject at the persistence boundary so any
    # bad data in DB is detectable.
    if not meta.canonical_url.startswith(_ALLOWED_LOFT_URL_SCHEMES):
        raise ValueError(
            f"refusing to persist non-HTTP canonical_url: {meta.canonical_url!r}"
        )
    now_iso = datetime.now(UTC).isoformat()
    db = SessionLocal()
    try:
        file_id = register_single_file(db, drive, loft_path)
        db.execute(
            text(
                "INSERT INTO loft_metadata "
                "(file_id, provider, provider_item_id, url, description, "
                " channel, published_at, language, has_captions, "
                " captions_downloaded, fetched_at) "
                "VALUES (:fid, :provider, :iid, :url, :desc, :channel, "
                " :pub, :lang, :hc, 0, :fetched)"
            ),
            {
                "fid": file_id,
                "provider": provider.name,
                "iid": meta.item_id,
                "url": meta.canonical_url,
                "desc": (meta.description or "")[:2000],
                "channel": meta.channel,
                "pub": meta.published_at,
                "lang": meta.language,
                "hc": int(bool(meta.has_captions)),
                "fetched": now_iso,
            },
        )
        db.commit()
        return file_id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _save_vtt(loft_path: Path, vtt_text: str) -> None:
    vtt_path = loft_path.with_suffix(".vtt")
    vtt_path.write_text(vtt_text, encoding="utf-8")


def _update_caption_state(
    file_id: str, *, ok: bool, error_kind: str | None
) -> None:
    db = SessionLocal()
    try:
        db.execute(
            text(
                "UPDATE loft_metadata "
                "SET captions_downloaded = :ok, caption_error_kind = :kind "
                "WHERE file_id = :fid"
            ),
            {"ok": int(ok), "kind": None if ok else error_kind, "fid": file_id},
        )
        db.commit()
    finally:
        db.close()


# Module-level singleton — router imports this for use in route handlers.
subscription_manager = SubscriptionManager()
