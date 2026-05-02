"""Pure DB / FS-aware persistence helpers for subscriptions.

Module-level functions that own every SQL statement and on-disk
state mutation related to ``subscriptions`` / ``subscription_videos``
/ ``loft_metadata``. ``SubscriptionManager`` delegates to these so
business logic in ``manager.py`` stays focused on flow control.

No business decisions live here — every function is a thin DB
operation with the SQL inlined. Callers decide *when* and *why*.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.database import SessionLocal
from app.services.scanner import register_single_file


logger = logging.getLogger(__name__)


_ALLOWED_LOFT_URL_SCHEMES: tuple[str, ...] = ("http://", "https://")


# ---- ISO parsing & cron predicate ----------------------------------


def parse_iso(value: str | None) -> datetime | None:
    """``datetime.fromisoformat`` that swallows malformed input."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def cron_due(row: dict, now: datetime) -> bool:
    """Pure predicate for cron eligibility.

    Eligible iff the row is past any explicit ``cooldown_until`` AND
    has either never synced or exceeded its ``cooldown_minutes`` budget
    since the last sync.
    """
    cooldown_until = parse_iso(row.get("cooldown_until"))
    if cooldown_until is not None and cooldown_until > now:
        return False
    last_synced_at = parse_iso(row.get("last_synced_at"))
    if last_synced_at is None:
        return True
    cooldown_minutes = int(row.get("cooldown_minutes") or 0)
    elapsed_seconds = (now - last_synced_at).total_seconds()
    return elapsed_seconds >= cooldown_minutes * 60


# ---- Subscription row CRUD ----------------------------------------


def insert_subscription(
    *,
    provider: str,
    source_kind: str,
    source_ref: str,
    drive: str,
    folder_path: str,
    cooldown_minutes: int,
    include_no_transcript: bool,
) -> int:
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
                "provider": provider,
                "kind": source_kind,
                "ref": source_ref,
                "drive": drive,
                "folder": folder_path,
                "cd": cooldown_minutes,
                "inc": int(include_no_transcript),
                "created_at": now,
            },
        )
        sub_id = int(result.scalar_one())
        db.commit()
        return sub_id
    finally:
        db.close()


def delete_subscription(subscription_id: int) -> None:
    db = SessionLocal()
    try:
        db.execute(
            text("DELETE FROM subscriptions WHERE id = :id"),
            {"id": subscription_id},
        )
        db.commit()
    finally:
        db.close()


def update_settings(
    subscription_id: int,
    *,
    is_enabled: bool | None = None,
    cooldown_minutes: int | None = None,
    include_no_transcript: bool | None = None,
    folder_path: str | None = None,
    display_title: str | None = None,
    clear_cooldown_until: bool = False,
) -> None:
    """PATCH-style update for user-editable subscription fields.

    Each parameter is optional; None means "do not touch". The handler
    in router.py decides which fields the client sent.

    ``clear_cooldown_until=True`` resets the cron backoff state (used
    when ``cooldown_minutes`` changes — the previous backoff window is
    no longer the user's intent).
    """
    set_clauses: list[str] = []
    params: dict = {"id": subscription_id}
    if is_enabled is not None:
        set_clauses.append("is_enabled = :enabled")
        params["enabled"] = int(is_enabled)
    if cooldown_minutes is not None:
        set_clauses.append("cooldown_minutes = :cd")
        params["cd"] = cooldown_minutes
    if include_no_transcript is not None:
        set_clauses.append("include_no_transcript = :inc")
        params["inc"] = int(include_no_transcript)
    if folder_path is not None:
        set_clauses.append("folder_path = :folder")
        params["folder"] = folder_path
    if display_title is not None:
        set_clauses.append("display_title = :title")
        params["title"] = display_title
    if clear_cooldown_until:
        set_clauses.append("cooldown_until = NULL")
    if not set_clauses:
        return
    sql = (
        f"UPDATE subscriptions SET {', '.join(set_clauses)} "
        f"WHERE id = :id"
    )
    db = SessionLocal()
    try:
        db.execute(text(sql), params)
        db.commit()
    finally:
        db.close()


def summary_for_drive(drive: str) -> dict:
    """Aggregate counts for the dashboard header.

    One scan per call; subscription_videos is joined via a subquery so
    the SQL stays one round trip even on a moderately large drive. The
    syncing count is filled in by the router from worker.running_ids
    (it is in-memory state, not persisted).
    """
    db = SessionLocal()
    try:
        sub_rows = db.execute(
            text(
                "SELECT id, is_enabled FROM subscriptions WHERE drive = :drive"
            ),
            {"drive": drive},
        ).mappings().all()
        total = len(sub_rows)
        paused = sum(1 for r in sub_rows if not r["is_enabled"])

        if total == 0:
            return {
                "total": 0, "paused": 0,
                "imported_count": 0, "failed_count": 0,
                "attention_subscription_ids": [],
            }

        sub_ids = [r["id"] for r in sub_rows]
        # Build an inline IN list — bind parameters per id keep SQL injection out.
        placeholders = ", ".join(f":id{i}" for i in range(len(sub_ids)))
        bind_params = {f"id{i}": sid for i, sid in enumerate(sub_ids)}

        counts = db.execute(
            text(
                "SELECT "
                " SUM(CASE WHEN status = 'imported' THEN 1 ELSE 0 END) AS imp, "
                " SUM(CASE WHEN status = 'failed' AND COALESCE(error_kind, '') NOT IN ('dismissed') THEN 1 ELSE 0 END) AS fail "
                f"FROM subscription_videos WHERE subscription_id IN ({placeholders})"
            ),
            bind_params,
        ).mappings().first()

        attention = db.execute(
            text(
                "SELECT DISTINCT subscription_id FROM subscription_videos "
                f"WHERE subscription_id IN ({placeholders}) "
                " AND status = 'failed' "
                " AND COALESCE(error_kind, '') NOT IN ('dismissed')"
            ),
            bind_params,
        ).fetchall()
    finally:
        db.close()

    return {
        "total": total,
        "paused": paused,
        "imported_count": int(counts["imp"] or 0) if counts else 0,
        "failed_count": int(counts["fail"] or 0) if counts else 0,
        "attention_subscription_ids": [r[0] for r in attention],
    }


def list_activity(drive: str, limit: int) -> list[dict]:
    """Unified import-activity feed for a drive.

    Joins ``files`` to ``loft_metadata`` (only loft files appear; the
    addon's responsibility ends at .loft creation) and LEFT JOINs to
    ``subscription_videos`` so single imports surface alongside
    subscription imports. The optional join to ``subscriptions``
    populates the ``subscription_title`` field for the UI badge.

    Excludes soft-deleted / missing files because those are not the
    user's "recent" — restoring brings them back into view via the
    same query.
    """
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT "
                " f.id AS file_id, "
                " f.filename AS filename, "
                " f.thumbnail_path AS thumbnail_path, "
                " f.created_at AS created_at, "
                " m.channel AS channel, "
                " m.published_at AS published_at, "
                " sv.subscription_id AS subscription_id, "
                " s.display_title AS subscription_display_title, "
                " s.source_ref AS subscription_source_ref "
                "FROM files f "
                "JOIN loft_metadata m ON m.file_id = f.id "
                "LEFT JOIN subscription_videos sv "
                "  ON sv.file_id = f.id "
                "LEFT JOIN subscriptions s "
                "  ON s.id = sv.subscription_id "
                "WHERE f.drive = :drive "
                "  AND f.deleted_at IS NULL "
                "  AND f.missing_since IS NULL "
                "ORDER BY f.created_at DESC "
                "LIMIT :limit"
            ),
            {"drive": drive, "limit": limit},
        ).mappings().all()
    finally:
        db.close()
    return [dict(r) for r in rows]


def reset_video_for_retry(
    subscription_id: int, item_id: str
) -> bool:
    """Clear ``error_kind`` so the row can re-enter the import flow.

    Used by ``resolve-conflict?action in (rename, overwrite)``: after
    the user acknowledges the conflict, we wipe the failure marker
    and let the worker re-attempt. Returns False when the row does
    not exist so the route can 404.
    """
    db = SessionLocal()
    try:
        result = db.execute(
            text(
                "UPDATE subscription_videos "
                "SET error_kind = NULL, status = 'failed' "
                "WHERE subscription_id = :sid AND item_id = :iid"
            ),
            {"sid": subscription_id, "iid": item_id},
        )
        db.commit()
        return result.rowcount > 0
    finally:
        db.close()


def mark_video_dismissed(
    subscription_id: int, item_id: str
) -> bool:
    """Set ``error_kind='dismissed'`` so retry buttons disappear."""
    db = SessionLocal()
    try:
        result = db.execute(
            text(
                "UPDATE subscription_videos "
                "SET error_kind = 'dismissed', status = 'failed' "
                "WHERE subscription_id = :sid AND item_id = :iid"
            ),
            {"sid": subscription_id, "iid": item_id},
        )
        db.commit()
        return result.rowcount > 0
    finally:
        db.close()


def update_source_metadata(
    subscription_id: int,
    *,
    avatar_url: str | None,
    display_title: str | None,
) -> None:
    """Update ``avatar_url`` / ``display_title`` for a subscription.

    None values leave the existing column untouched (COALESCE) so a
    second-pass refresh that resolves only one of the two fields does
    not erase the other. To explicitly clear a field, callers pass an
    empty string and convert at the call site, or call a future
    dedicated helper — neither path is needed today.
    """
    db = SessionLocal()
    try:
        db.execute(
            text(
                "UPDATE subscriptions SET "
                " avatar_url = COALESCE(:avatar, avatar_url), "
                " display_title = COALESCE(:title, display_title) "
                "WHERE id = :id"
            ),
            {
                "avatar": avatar_url,
                "title": display_title,
                "id": subscription_id,
            },
        )
        db.commit()
    finally:
        db.close()


def load_subscription(subscription_id: int) -> dict | None:
    db = SessionLocal()
    try:
        row = db.execute(
            text(
                "SELECT provider, source_kind, source_ref, drive, "
                " folder_path, include_no_transcript, avatar_url, "
                " display_title "
                "FROM subscriptions WHERE id = :id"
            ),
            {"id": subscription_id},
        ).mappings().first()
    finally:
        db.close()
    return dict(row) if row else None


def load_eligible_cron_rows(
    now: datetime, predicate
) -> list[int]:
    """Return subscription_ids whose cron sweep should fire.

    Filtering happens in Python because ``last_synced_at`` /
    ``cooldown_until`` are stored as ISO-8601 with timezone offsets,
    which SQLite's ``datetime()`` modifier strips — making string
    comparison in SQL unreliable. Subscription cardinality is small
    enough that a full scan is cheaper than the workaround.
    """
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT id, cooldown_minutes, last_synced_at, "
                " cooldown_until "
                "FROM subscriptions "
                "WHERE is_enabled = 1 "
                "ORDER BY id"
            )
        ).mappings().all()
    finally:
        db.close()
    return [row["id"] for row in rows if predicate(row, now)]


def touch_last_synced_at(subscription_id: int) -> None:
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


def set_cooldown_until(subscription_id: int, until: datetime) -> None:
    db = SessionLocal()
    try:
        db.execute(
            text(
                "UPDATE subscriptions SET cooldown_until = :u "
                "WHERE id = :id"
            ),
            {
                "u": until.astimezone(UTC).isoformat(),
                "id": subscription_id,
            },
        )
        db.commit()
    finally:
        db.close()


def clear_cooldown_until(subscription_id: int) -> None:
    db = SessionLocal()
    try:
        db.execute(
            text(
                "UPDATE subscriptions SET cooldown_until = NULL "
                "WHERE id = :id"
            ),
            {"id": subscription_id},
        )
        db.commit()
    finally:
        db.close()


def load_cooldown_state(
    subscription_id: int,
) -> tuple[datetime | None, datetime | None] | None:
    """Return ``(last_synced_at, cooldown_until)`` for backoff inference.

    None signals the subscription row does not exist; either timestamp
    may itself be None.
    """
    db = SessionLocal()
    try:
        row = db.execute(
            text(
                "SELECT last_synced_at, cooldown_until "
                "FROM subscriptions WHERE id = :id"
            ),
            {"id": subscription_id},
        ).mappings().first()
    finally:
        db.close()
    if row is None:
        return None
    return parse_iso(row.get("last_synced_at")), parse_iso(
        row.get("cooldown_until")
    )


# ---- subscription_videos -----------------------------------------


def load_seen_item_ids(subscription_id: int) -> set[str]:
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


def load_video_row(
    subscription_id: int, item_id: str
) -> dict | None:
    db = SessionLocal()
    try:
        row = db.execute(
            text(
                "SELECT first_seen_at, status, error_kind, file_id "
                "FROM subscription_videos "
                "WHERE subscription_id = :sid AND item_id = :iid"
            ),
            {"sid": subscription_id, "iid": item_id},
        ).mappings().first()
    finally:
        db.close()
    return dict(row) if row else None


def upsert_video_row(
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


# ---- loft_metadata + dedup ---------------------------------------


def lookup_dedup(
    drive: str,
    folder_path: str,
    provider_name: str,
    item_id: str,
) -> str | None:
    """Return ``file_id`` of an existing .loft for the same dedup tuple.

    Per hako ``FSrqtHVrv9B8NW3n2vb22``, the dedup key is
    ``(drive, folder_path, provider, provider_item_id)``. Soft-deleted
    or missing files are intentionally excluded — the user removed
    them on purpose and they should not be silently reused.
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


def register_loft(
    drive: str,
    loft_path: Path,
    provider_name: str,
    meta: Any,
) -> str:
    """Insert ``files`` + ``loft_metadata`` rows for a freshly written .loft.

    ``meta`` is a ``registry.ItemMetadata`` (kept Any to avoid an import
    cycle with ``manager.py``). ``canonical_url`` is rejected unless it
    starts with HTTP(S) — downstream ``<a href={url}>`` would fire on
    click for a ``javascript:`` payload, so guard at the persistence
    boundary.
    """
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
                "provider": provider_name,
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


def update_caption_state(
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
            {
                "ok": int(ok),
                "kind": None if ok else error_kind,
                "fid": file_id,
            },
        )
        db.commit()
    finally:
        db.close()
