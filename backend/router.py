"""Media Import addon router.

Endpoints:
- POST /api/addons/media_import/link             — URL → .loft generation
- GET  /api/addons/media_import/link/{file_id}/metadata
- POST /api/addons/media_import/link/{file_id}/refresh
- POST /api/addons/media_import/subscriptions
- GET  /api/addons/media_import/subscriptions?drive=X
- DELETE /api/addons/media_import/subscriptions/{id}
- POST /api/addons/media_import/subscriptions/{id}/sync
- GET  /api/addons/media_import/subscriptions/{id}/videos
- POST /api/addons/media_import/subscriptions/{id}/videos/{item_id}/retry

Slot:
- ``loft-metadata`` — channel/description/captions panel under the player.

On startup:
- Ensures the ``loft_metadata`` table exists (idempotent).
- Registers the youtube/vimeo/soundcloud providers in core's
  ``provider_registry``.
- Starts the metadata fetch worker.
"""
import asyncio
import logging
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import text

import app.config as config
from app.auth import check_drive_access, get_unlocked_groups
from app.database import SessionLocal

from .schemas import (
    ActivityEntry,
    LoftCreateRequest,
    LoftCreateResponse,
    LoftMetadataResponse,
    ResolveConflictRequest,
    ResolveConflictResponse,
    SubscriptionCreateRequest,
    SubscriptionEnqueueResponse,
    SubscriptionPatchRequest,
    SubscriptionRefreshMetadataResponse,
    SubscriptionResolveRequest,
    SubscriptionResolveResponse,
    SubscriptionResponse,
    SubscriptionSummaryResponse,
    SubscriptionVideoResponse,
)
from .subscription import db as subdb
from .subscription.registry import find_subscription_provider_by_url
from .service import (
    loft_manager,
    subscription_avatar_path,
    _backfill_provider_item_ids,
    _ensure_loft_table,
    _ensure_subscription_tables,
)
from .provider_registration import register_media_import_providers
from .subscription.manager import subscription_manager
from .subscription.registration import register_subscription_providers
from .subscription.scheduler import subscription_scheduler
from .subscription.worker import subscription_worker

logger = logging.getLogger(__name__)

ADDON_META = {
    "label": "Media Import",
    "icon": "link",
    "scope": "drive",
    "href": "/addons/media_import",
    "slots": {
        "loft-metadata": [
            {"id": "loft-metadata", "label": "Loft Metadata", "priority": 10},
        ],
    },
}

router = APIRouter(prefix="/api/addons/media_import", tags=["media_import"])


def _scoped_drive(
    x_lit_drive: str | None,
    unlocked_groups: list[str],
) -> str:
    """Resolve and authorise the drive scope from the X-Lit-Drive header.

    media_import is ``scope=drive``; addon_proxy enforces the header for
    external addons but in-process addons bypass that path, so each
    handler must validate the header itself. Returns the verified drive
    name. 400 when the header is missing (malformed client), 404 when
    the caller cannot see the drive (existence-hiding per
    design-decisions.md).
    """
    if not x_lit_drive:
        raise HTTPException(status_code=400, detail="Drive context required")
    drive = unquote(x_lit_drive)
    try:
        config.get_drive_path(drive)
    except ValueError:
        raise HTTPException(status_code=404, detail="Drive not found")
    check_drive_access(drive, unlocked_groups)
    return drive


def _require_body_drive_matches(scoped: str, body_drive: str) -> None:
    """Reject requests whose body asserts a different drive than the
    URL-scoped one. Cross-drive writes from a drive-scoped page are a
    boundary violation regardless of whether the caller has access to
    both drives.
    """
    if body_drive != scoped:
        raise HTTPException(
            status_code=400,
            detail="drive in body does not match X-Lit-Drive scope",
        )


async def on_startup() -> None:
    register_media_import_providers()
    register_subscription_providers()
    _ensure_loft_table()
    _ensure_subscription_tables()
    # Backfill must run after both registries and the schema are ready;
    # it depends on the youtube subscription provider being registered
    # to recognize legacy URLs.
    _backfill_provider_item_ids()
    await loft_manager.start_worker()
    await subscription_worker.start()
    await subscription_scheduler.start()


@router.post("/link", response_model=LoftCreateResponse)
async def create_loft(
    request: LoftCreateRequest,
    x_lit_drive: str | None = Header(default=None, alias="X-Lit-Drive"),
    unlocked_groups: list[str] = Depends(get_unlocked_groups),
) -> LoftCreateResponse:
    scoped = _scoped_drive(x_lit_drive, unlocked_groups)
    _require_body_drive_matches(scoped, request.drive)

    if not request.url.strip():
        raise HTTPException(status_code=422, detail="URL is required")

    loop = asyncio.get_running_loop()
    try:
        file_id, filename = await loop.run_in_executor(
            None,
            loft_manager.create_loft_sync,
            request.url,
            request.drive,
            request.folder_path,
        )
    except Exception:
        logger.exception("Failed to create loft ref: %s", request.url)
        raise HTTPException(status_code=500, detail="Failed to create link")

    await loft_manager.enqueue_fetch(file_id, request.url, request.drive)

    return LoftCreateResponse(file_id=file_id, filename=filename)


@router.get("/link/{file_id}/metadata", response_model=LoftMetadataResponse)
async def get_loft_metadata(file_id: str) -> LoftMetadataResponse:
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT * FROM loft_metadata WHERE file_id = :file_id"),
            {"file_id": file_id},
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Metadata not found")
        return LoftMetadataResponse(**row)
    finally:
        db.close()


@router.post("/link/{file_id}/refresh")
async def refresh_loft(file_id: str) -> dict:
    from app.models import File

    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT url FROM loft_metadata WHERE file_id = :file_id"),
            {"file_id": file_id},
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Metadata not found")
        url = row["url"]

        file_record = db.query(File).filter(File.id == file_id).first()
        if not file_record:
            raise HTTPException(status_code=404, detail="File not found")
        drive = file_record.drive
    finally:
        db.close()

    await loft_manager.enqueue_fetch(file_id, url, drive)
    return {"status": "queued"}


# ---- Subscriptions (Phase 2 Commit 4) -----------------------------


@router.post(
    "/subscriptions/resolve", response_model=SubscriptionResolveResponse
)
async def resolve_subscription_url(
    request: SubscriptionResolveRequest,
    x_lit_drive: str | None = Header(default=None, alias="X-Lit-Drive"),
    unlocked_groups: list[str] = Depends(get_unlocked_groups),
) -> SubscriptionResolveResponse:
    """Classify a pasted URL without persisting anything.

    Frontend uses this to decide whether to show the single-import flow
    (kind=video / kind=unknown → existing /link path) or the
    subscription creation flow (kind=channel / kind=playlist → backfill
    picker etc.). Pure parsing, no DB / network — but still drive-scoped
    because the frontend only ever calls this from
    /drive/{name}/addons/media_import and we want to match the
    access-control posture of the create/list routes the caller will
    fire next.
    """
    _scoped_drive(x_lit_drive, unlocked_groups)
    url = request.url.strip()
    if not url:
        return SubscriptionResolveResponse(kind="unknown")
    match = find_subscription_provider_by_url(url)
    if match is None:
        return SubscriptionResolveResponse(kind="unknown")
    provider, ref = match
    return SubscriptionResolveResponse(
        kind=ref.kind, provider=provider.name, ref=ref.ref
    )





def _row_to_subscription_response(
    row: dict, *, running: bool = False
) -> SubscriptionResponse:
    return SubscriptionResponse(
        id=row["id"],
        provider=row["provider"],
        source_kind=row["source_kind"],
        source_ref=row["source_ref"],
        drive=row["drive"],
        folder_path=row["folder_path"],
        title=row.get("title"),
        is_enabled=bool(row["is_enabled"]),
        cooldown_minutes=row["cooldown_minutes"],
        include_no_transcript=bool(row["include_no_transcript"]),
        last_synced_at=row.get("last_synced_at"),
        cooldown_until=row.get("cooldown_until"),
        created_at=row["created_at"],
        running=running,
        avatar_url=row.get("avatar_url"),
        display_title=row.get("display_title"),
    )


def _validate_folder_path(folder_path: str, drive: str) -> None:
    """Reject folder_path values that escape the drive root.

    Mirrors the check in ``manager._allocate_loft_path`` but at the
    HTTP boundary so a malicious PATCH cannot persist a poisoned path
    that the next sync would then trust. Empty string is the drive
    root, which is always valid.
    """
    from pathlib import Path

    if folder_path == "":
        return
    drive_path = config.get_drive_path(drive)
    drive_root = drive_path.resolve()
    target = (drive_path / folder_path).resolve()
    if not (target == drive_root or target.is_relative_to(drive_root)):
        raise HTTPException(
            status_code=400,
            detail="folder_path escapes drive root",
        )


def _load_subscription_row(subscription_id: int) -> dict | None:
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT * FROM subscriptions WHERE id = :id"),
            {"id": subscription_id},
        ).mappings().first()
    finally:
        db.close()
    return dict(row) if row else None


@router.post(
    "/subscriptions", response_model=SubscriptionResponse
)
async def create_subscription(
    request: SubscriptionCreateRequest,
    x_lit_drive: str | None = Header(default=None, alias="X-Lit-Drive"),
    unlocked_groups: list[str] = Depends(get_unlocked_groups),
) -> SubscriptionResponse:
    if not request.url.strip():
        raise HTTPException(status_code=422, detail="URL is required")
    scoped = _scoped_drive(x_lit_drive, unlocked_groups)
    _require_body_drive_matches(scoped, request.drive)

    loop = asyncio.get_running_loop()
    try:
        sub_id = await loop.run_in_executor(
            None,
            lambda: subscription_manager.create(
                url=request.url,
                drive=request.drive,
                folder_path=request.folder_path,
                cooldown_minutes=request.cooldown_minutes,
                include_no_transcript=request.include_no_transcript,
            ),
        )
    except ValueError as exc:
        # URL not recognized / points at a single video.
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        logger.exception("Failed to create subscription: %s", request.url)
        raise HTTPException(
            status_code=500, detail="Failed to create subscription"
        )

    row = _load_subscription_row(sub_id)
    assert row is not None
    running = sub_id in subscription_worker.running_ids
    return _row_to_subscription_response(row, running=running)


@router.get(
    "/subscriptions", response_model=list[SubscriptionResponse]
)
async def list_subscriptions(
    drive: str = Query(..., description="Drive name (required)"),
    x_lit_drive: str | None = Header(default=None, alias="X-Lit-Drive"),
    unlocked_groups: list[str] = Depends(get_unlocked_groups),
) -> list[SubscriptionResponse]:
    scoped = _scoped_drive(x_lit_drive, unlocked_groups)
    if drive != scoped:
        raise HTTPException(
            status_code=400,
            detail="drive query does not match X-Lit-Drive scope",
        )
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT * FROM subscriptions WHERE drive = :drive "
                "ORDER BY created_at DESC"
            ),
            {"drive": drive},
        ).mappings().all()
    finally:
        db.close()
    running = subscription_worker.running_ids
    return [
        _row_to_subscription_response(dict(r), running=r["id"] in running)
        for r in rows
    ]


def _load_owned_subscription(
    subscription_id: int,
    scoped_drive: str,
    unlocked_groups: list[str],
) -> dict:
    """Load a subscription row or 404 if absent / inaccessible.

    Centralizes the per-id auth check so every subscription endpoint
    enforces the same drive-boundary rule. Always returns 404 (never
    403) — per design-decisions.md, locked drives must hide existence.
    Subscriptions on a drive other than the scoped one are 404'd too:
    the URL the caller is on is the boundary, not their wider access.
    """
    row = _load_subscription_row(subscription_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if row["drive"] != scoped_drive:
        raise HTTPException(status_code=404, detail="Subscription not found")
    check_drive_access(row["drive"], unlocked_groups)
    return row


@router.get(
    "/subscriptions/summary", response_model=SubscriptionSummaryResponse
)
async def subscriptions_summary(
    drive: str = Query(..., description="Drive name (required)"),
    x_lit_drive: str | None = Header(default=None, alias="X-Lit-Drive"),
    unlocked_groups: list[str] = Depends(get_unlocked_groups),
) -> SubscriptionSummaryResponse:
    """Aggregate health for the dashboard header strip.

    Counts come from the DB; ``syncing`` is overlaid from
    ``SubscriptionWorker.running_ids`` because the worker queue is
    in-memory and a state column is intentionally not persisted
    (hako z6wc1bI3g_WQ9_jS0xi69).
    """
    scoped = _scoped_drive(x_lit_drive, unlocked_groups)
    if drive != scoped:
        raise HTTPException(
            status_code=400,
            detail="drive query does not match X-Lit-Drive scope",
        )
    snap = subdb.summary_for_drive(drive)
    running = subscription_worker.running_ids
    # Restrict the running set to subscriptions on this drive — the
    # worker tracks ids globally, but the summary speaks for one drive.
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT id FROM subscriptions WHERE drive = :drive"
            ),
            {"drive": drive},
        ).fetchall()
    finally:
        db.close()
    drive_subs = {r[0] for r in rows}
    syncing_count = sum(1 for sid in running if sid in drive_subs)

    attention = len(snap["attention_subscription_ids"])
    healthy = max(0, snap["total"] - snap["paused"] - attention)
    return SubscriptionSummaryResponse(
        total=snap["total"],
        paused=snap["paused"],
        syncing=syncing_count,
        healthy=healthy,
        attention=attention,
        imported_count=snap["imported_count"],
        failed_count=snap["failed_count"],
    )


@router.patch(
    "/subscriptions/{subscription_id}", response_model=SubscriptionResponse
)
async def patch_subscription(
    subscription_id: int,
    patch: SubscriptionPatchRequest,
    x_lit_drive: str | None = Header(default=None, alias="X-Lit-Drive"),
    unlocked_groups: list[str] = Depends(get_unlocked_groups),
) -> SubscriptionResponse:
    """Update subscription settings (Pause / cooldown / folder / etc.).

    cooldown_minutes change clears ``cooldown_until`` so the cron loop
    re-evaluates eligibility against the new schedule on the next sweep
    (rather than waiting out a backoff that no longer reflects user
    intent). is_enabled / include_no_transcript / display_title /
    folder_path edits leave cooldown_until alone.
    """
    scoped = _scoped_drive(x_lit_drive, unlocked_groups)
    sub = _load_owned_subscription(
        subscription_id, scoped, unlocked_groups
    )

    folder = patch.folder_path
    if folder is not None:
        try:
            _validate_folder_path(folder, sub["drive"])
        except HTTPException:
            raise

    if patch.cooldown_minutes is not None and patch.cooldown_minutes < 1:
        raise HTTPException(
            status_code=422,
            detail="cooldown_minutes must be >= 1",
        )

    subdb.update_settings(
        subscription_id,
        is_enabled=patch.is_enabled,
        cooldown_minutes=patch.cooldown_minutes,
        include_no_transcript=patch.include_no_transcript,
        folder_path=folder,
        display_title=patch.display_title,
        clear_cooldown_until=patch.cooldown_minutes is not None,
    )

    row = _load_subscription_row(subscription_id)
    assert row is not None
    running = subscription_id in subscription_worker.running_ids
    return _row_to_subscription_response(row, running=running)


@router.get("/subscriptions/{subscription_id}/avatar")
async def get_subscription_avatar(
    subscription_id: int,
    x_lit_drive: str | None = Header(default=None, alias="X-Lit-Drive"),
    unlocked_groups: list[str] = Depends(get_unlocked_groups),
) -> FileResponse:
    """Serve the cached avatar JPEG for a subscription.

    The file is downloaded by ``_save_subscription_avatar`` at
    create / refresh time and stored under
    ``data/media_import_avatars/<sub_id>.jpg``. 404 when missing so
    the UI falls back to a generic avatar — this is the same
    "absence reads as None" semantic the rest of the addon uses.
    """
    scoped = _scoped_drive(x_lit_drive, unlocked_groups)
    _load_owned_subscription(subscription_id, scoped, unlocked_groups)
    path = subscription_avatar_path(subscription_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Avatar not available")
    return FileResponse(path, media_type="image/jpeg")


@router.post(
    "/subscriptions/{subscription_id}/refresh-metadata",
    response_model=SubscriptionRefreshMetadataResponse,
)
async def refresh_subscription_metadata(
    subscription_id: int,
    x_lit_drive: str | None = Header(default=None, alias="X-Lit-Drive"),
    unlocked_groups: list[str] = Depends(get_unlocked_groups),
) -> SubscriptionRefreshMetadataResponse:
    """Force a re-fetch of avatar / display_title via the provider.

    Goes through the SubscriptionManager which handles the avatar
    download via the shared helper (contract drift defense from hako
    ``IpF19kUI3OKoY_ps7iKg1``). yt-dlp can take a few seconds; the
    handler awaits it on the executor so the HTTP response carries
    the updated metadata back to the caller in one round trip.
    """
    scoped = _scoped_drive(x_lit_drive, unlocked_groups)
    _load_owned_subscription(subscription_id, scoped, unlocked_groups)

    loop = asyncio.get_running_loop()
    try:
        updated = await loop.run_in_executor(
            None,
            subscription_manager.refresh_source_metadata,
            subscription_id,
        )
    except Exception:
        logger.exception(
            "Failed to refresh source metadata for subscription=%s",
            subscription_id,
        )
        raise HTTPException(
            status_code=500, detail="Failed to refresh metadata"
        )

    row = _load_subscription_row(subscription_id)
    assert row is not None
    return SubscriptionRefreshMetadataResponse(
        updated=updated,
        avatar_url=row.get("avatar_url"),
        display_title=row.get("display_title"),
    )


@router.delete("/subscriptions/{subscription_id}")
async def delete_subscription(
    subscription_id: int,
    x_lit_drive: str | None = Header(default=None, alias="X-Lit-Drive"),
    unlocked_groups: list[str] = Depends(get_unlocked_groups),
) -> dict:
    scoped = _scoped_drive(x_lit_drive, unlocked_groups)
    _load_owned_subscription(subscription_id, scoped, unlocked_groups)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, subscription_manager.delete, subscription_id
    )
    return {"status": "deleted"}


@router.post(
    "/subscriptions/{subscription_id}/sync",
    response_model=SubscriptionEnqueueResponse,
)
async def sync_subscription(
    subscription_id: int,
    backfill: int | None = Query(
        None, ge=1, description="Limit upstream items considered (default: all)"
    ),
    x_lit_drive: str | None = Header(default=None, alias="X-Lit-Drive"),
    unlocked_groups: list[str] = Depends(get_unlocked_groups),
) -> SubscriptionEnqueueResponse:
    scoped = _scoped_drive(x_lit_drive, unlocked_groups)
    _load_owned_subscription(subscription_id, scoped, unlocked_groups)
    queued = await subscription_worker.enqueue_sync(
        subscription_id, kind="manual", backfill=backfill
    )
    return SubscriptionEnqueueResponse(
        status="queued" if queued else "already_queued"
    )


@router.get(
    "/subscriptions/{subscription_id}/videos",
    response_model=list[SubscriptionVideoResponse],
)
async def list_subscription_videos(
    subscription_id: int,
    x_lit_drive: str | None = Header(default=None, alias="X-Lit-Drive"),
    unlocked_groups: list[str] = Depends(get_unlocked_groups),
) -> list[SubscriptionVideoResponse]:
    scoped = _scoped_drive(x_lit_drive, unlocked_groups)
    _load_owned_subscription(subscription_id, scoped, unlocked_groups)
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT sv.subscription_id, sv.item_id, sv.status, "
                " sv.error_kind, sv.file_id, sv.first_seen_at, "
                " sv.last_attempted_at, "
                " f.filename AS filename, "
                " f.thumbnail_path AS thumbnail_path, "
                " m.channel AS channel, "
                " m.published_at AS published_at "
                "FROM subscription_videos sv "
                "LEFT JOIN subscriptions s ON s.id = sv.subscription_id "
                "LEFT JOIN files f "
                "  ON f.id = sv.file_id "
                "  AND f.deleted_at IS NULL "
                "  AND f.missing_since IS NULL "
                "LEFT JOIN loft_metadata m "
                "  ON m.provider = s.provider "
                "  AND m.provider_item_id = sv.item_id "
                "WHERE sv.subscription_id = :sid "
                "ORDER BY sv.first_seen_at DESC"
            ),
            {"sid": subscription_id},
        ).mappings().all()
    finally:
        db.close()
    return [
        SubscriptionVideoResponse(
            subscription_id=r["subscription_id"],
            item_id=r["item_id"],
            status=r["status"],
            error_kind=r["error_kind"],
            file_id=r["file_id"],
            first_seen_at=r["first_seen_at"],
            last_attempted_at=r["last_attempted_at"],
            title=_title_from_filename(r.get("filename")),
            thumbnail_path=r.get("thumbnail_path"),
            channel=r.get("channel"),
            published_at=r.get("published_at"),
        )
        for r in rows
    ]


def _title_from_filename(filename: str | None) -> str | None:
    """Strip the trailing ``.loft`` extension for display.

    .loft files use ``<sanitized_title>.loft`` as canonical naming
    (see service.py allocator). The bare title is more readable than
    the raw filename in the UI item list.
    """
    if not filename:
        return None
    if filename.endswith(".loft"):
        return filename[: -len(".loft")]
    return filename


@router.post(
    "/subscriptions/{subscription_id}/videos/{item_id}/retry",
    response_model=SubscriptionEnqueueResponse,
)
async def retry_subscription_video(
    subscription_id: int,
    item_id: str,
    x_lit_drive: str | None = Header(default=None, alias="X-Lit-Drive"),
    unlocked_groups: list[str] = Depends(get_unlocked_groups),
) -> SubscriptionEnqueueResponse:
    scoped = _scoped_drive(x_lit_drive, unlocked_groups)
    _load_owned_subscription(subscription_id, scoped, unlocked_groups)
    # Eager existence check: the worker's _sync_blocking would also raise
    # SubscriptionNotFound for an unknown item, but by then the route has
    # already returned 200 and the frontend has no per-click failure
    # signal. Cheap query that preserves the synchronous 404 contract.
    db = SessionLocal()
    try:
        exists = db.execute(
            text(
                "SELECT 1 FROM subscription_videos "
                "WHERE subscription_id = :sid AND item_id = :iid"
            ),
            {"sid": subscription_id, "iid": item_id},
        ).first()
    finally:
        db.close()
    if not exists:
        raise HTTPException(status_code=404, detail="video not found")
    queued = await subscription_worker.enqueue_sync(
        subscription_id, kind="retry", item_id=item_id
    )
    return SubscriptionEnqueueResponse(
        status="queued" if queued else "already_queued"
    )


# ---- Phase C-2: Activity feed + path-conflict resolution -----------


@router.get("/activity", response_model=list[ActivityEntry])
async def list_activity(
    drive: str = Query(..., description="Drive name (required)"),
    limit: int = Query(50, ge=1, le=200),
    x_lit_drive: str | None = Header(default=None, alias="X-Lit-Drive"),
    unlocked_groups: list[str] = Depends(get_unlocked_groups),
) -> list[ActivityEntry]:
    """Unified import-activity feed for the dashboard's bottom section.

    One JOIN, drive-scoped, missing/trash files excluded — see
    ``subdb.list_activity`` for the SQL. ``source`` is derived from
    the LEFT JOIN to subscription_videos: if the row is present, the
    file came in via a subscription, otherwise via the single /link
    flow.
    """
    scoped = _scoped_drive(x_lit_drive, unlocked_groups)
    if drive != scoped:
        raise HTTPException(
            status_code=400,
            detail="drive query does not match X-Lit-Drive scope",
        )
    rows = subdb.list_activity(drive, limit)
    out: list[ActivityEntry] = []
    for r in rows:
        sub_id = r.get("subscription_id")
        title = (
            r.get("subscription_display_title")
            or r.get("subscription_source_ref")
            if sub_id is not None
            else None
        )
        out.append(
            ActivityEntry(
                file_id=r["file_id"],
                filename=r["filename"],
                thumbnail_path=r.get("thumbnail_path"),
                channel=r.get("channel"),
                published_at=r.get("published_at"),
                created_at=str(r["created_at"]),
                source="subscription" if sub_id is not None else "single",
                subscription_id=sub_id,
                subscription_title=title,
            )
        )
    return out


@router.post(
    "/subscriptions/{subscription_id}/videos/{item_id}/resolve-conflict",
    response_model=ResolveConflictResponse,
)
async def resolve_video_conflict(
    subscription_id: int,
    item_id: str,
    request: ResolveConflictRequest,
    x_lit_drive: str | None = Header(default=None, alias="X-Lit-Drive"),
    unlocked_groups: list[str] = Depends(get_unlocked_groups),
) -> ResolveConflictResponse:
    """User-issued resolution for a path_conflict row.

    Three actions: ``skip`` marks the row dismissed (retry button
    suppressed); ``rename`` and ``overwrite`` clear the error and
    re-enqueue the item. Today the manager's ``_allocate_loft_path``
    auto-renames so both retry actions converge on the same outcome
    — the distinction is preserved for forward compatibility with a
    future strict-collision mode.
    """
    scoped = _scoped_drive(x_lit_drive, unlocked_groups)
    _load_owned_subscription(subscription_id, scoped, unlocked_groups)

    if request.action == "skip":
        ok = subdb.mark_video_dismissed(subscription_id, item_id)
        if not ok:
            raise HTTPException(status_code=404, detail="video not found")
        return ResolveConflictResponse(status="dismissed")

    # rename / overwrite both clear error_kind and re-queue.
    ok = subdb.reset_video_for_retry(subscription_id, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="video not found")
    await subscription_worker.enqueue_sync(
        subscription_id, kind="retry", item_id=item_id
    )
    return ResolveConflictResponse(status="requeued")
