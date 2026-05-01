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
    ERROR_PATH_CONFLICT,
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
    """Resolve a unique ``Title.loft`` path under ``drive/folder``."""
    output_dir = drive_path / folder_path if folder_path else drive_path
    output_dir.mkdir(parents=True, exist_ok=True)
    safe = _sanitize_filename(title)
    candidate = output_dir / f"{safe}.loft"
    counter = 1
    while candidate.exists():
        candidate = output_dir / f"{safe} ({counter}).loft"
        counter += 1
    return candidate


# ---- Manager ------------------------------------------------------


class SubscriptionManager:
    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock_for(self, subscription_id: int) -> asyncio.Lock:
        return self._locks.setdefault(subscription_id, asyncio.Lock())

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
        # ``UC...`` ref so RSS / API URLs can be built later.
        if ref.ref.startswith("@"):
            url = f"https://www.youtube.com/{ref.ref}"
        else:
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
        lock = self._lock_for(subscription_id)
        if lock.locked():
            raise SubscriptionConflict(
                f"sync already running for subscription {subscription_id}"
            )
        async with lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._sync_blocking, subscription_id, backfill
            )

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
            except _PathConflict:
                self._record_video(
                    subscription_id, item_id, status="failed",
                    file_id=None, first_seen=first_seen,
                    error_kind=ERROR_PATH_CONFLICT,
                )
                failed += 1
                continue
            except Exception:
                logger.exception(
                    "Subscription %s: failed to import %s",
                    subscription_id, item_id,
                )
                self._record_video(
                    subscription_id, item_id, status="failed",
                    file_id=None, first_seen=first_seen,
                )
                failed += 1
                continue

            self._record_video(
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
                import time

                time.sleep(provider.inter_item_delay_seconds)

        self._touch_last_synced_at(subscription_id)
        return {
            "added": added,
            "reused": reused,
            "failed": failed,
            "total_new": len(new_ids),
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


class _PathConflict(Exception):
    """Raised when a target .loft already exists with conflicting content.

    Reserved for the FS-overlap edge case described in hako
    ``FSrqtHVrv9B8NW3n2vb22``. Phase 2 currently lets ``_allocate_loft_path``
    side-step this by appending ``(n)`` — the exception is wired but only
    raised by future tightening (not in 3c).
    """


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


def _register_loft_in_db(
    drive: str,
    loft_path: Path,
    provider: SubscriptionProvider,
    meta,
) -> str:
    """INSERT files + loft_metadata rows for the freshly written .loft."""
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
