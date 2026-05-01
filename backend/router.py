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

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

import app.config as config
from app.database import SessionLocal

from .schemas import (
    LoftCreateRequest,
    LoftCreateResponse,
    LoftMetadataResponse,
    SubscriptionCreateRequest,
    SubscriptionResolveRequest,
    SubscriptionResolveResponse,
    SubscriptionResponse,
    SubscriptionSyncResponse,
    SubscriptionVideoResponse,
)
from .subscription.registry import find_subscription_provider_by_url
from .service import (
    loft_manager,
    _backfill_provider_item_ids,
    _ensure_loft_table,
    _ensure_subscription_tables,
)
from .provider_registration import register_media_import_providers
from .subscription.manager import (
    SubscriptionConflict,
    SubscriptionNotFound,
    subscription_manager,
)
from .subscription.registration import register_subscription_providers

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


@router.post("/link", response_model=LoftCreateResponse)
async def create_loft(request: LoftCreateRequest) -> LoftCreateResponse:
    try:
        config.get_drive_path(request.drive)
    except ValueError:
        raise HTTPException(status_code=404, detail="Drive not found")

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
) -> SubscriptionResolveResponse:
    """Classify a pasted URL without persisting anything.

    Frontend uses this to decide whether to show the single-import flow
    (kind=video / kind=unknown → existing /link path) or the
    subscription creation flow (kind=channel / kind=playlist → backfill
    picker etc.). Pure parsing, no DB / network.
    """
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





def _row_to_subscription_response(row: dict) -> SubscriptionResponse:
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
) -> SubscriptionResponse:
    if not request.url.strip():
        raise HTTPException(status_code=422, detail="URL is required")
    try:
        config.get_drive_path(request.drive)
    except ValueError:
        raise HTTPException(status_code=404, detail="Drive not found")

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
    return _row_to_subscription_response(row)


@router.get(
    "/subscriptions", response_model=list[SubscriptionResponse]
)
async def list_subscriptions(
    drive: str = Query(..., description="Drive name (required)"),
) -> list[SubscriptionResponse]:
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
    return [_row_to_subscription_response(dict(r)) for r in rows]


@router.delete("/subscriptions/{subscription_id}")
async def delete_subscription(subscription_id: int) -> dict:
    if _load_subscription_row(subscription_id) is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, subscription_manager.delete, subscription_id
    )
    return {"status": "deleted"}


@router.post(
    "/subscriptions/{subscription_id}/sync",
    response_model=SubscriptionSyncResponse,
)
async def sync_subscription(
    subscription_id: int,
    backfill: int | None = Query(
        None, ge=1, description="Limit upstream items considered (default: all)"
    ),
) -> SubscriptionSyncResponse:
    if _load_subscription_row(subscription_id) is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    try:
        result = await subscription_manager.sync(
            subscription_id, backfill=backfill
        )
    except SubscriptionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except SubscriptionNotFound:
        # Race: row deleted between the check and the sync. Treat as 404.
        raise HTTPException(status_code=404, detail="Subscription not found")
    return SubscriptionSyncResponse(**result)


@router.get(
    "/subscriptions/{subscription_id}/videos",
    response_model=list[SubscriptionVideoResponse],
)
async def list_subscription_videos(
    subscription_id: int,
) -> list[SubscriptionVideoResponse]:
    if _load_subscription_row(subscription_id) is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT subscription_id, item_id, status, error_kind, "
                " file_id, first_seen_at, last_attempted_at "
                "FROM subscription_videos "
                "WHERE subscription_id = :sid "
                "ORDER BY first_seen_at DESC"
            ),
            {"sid": subscription_id},
        ).mappings().all()
    finally:
        db.close()
    return [SubscriptionVideoResponse(**dict(r)) for r in rows]


@router.post(
    "/subscriptions/{subscription_id}/videos/{item_id}/retry",
    response_model=SubscriptionSyncResponse,
)
async def retry_subscription_video(
    subscription_id: int, item_id: str
) -> SubscriptionSyncResponse:
    if _load_subscription_row(subscription_id) is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    try:
        result = await subscription_manager.retry_item(
            subscription_id, item_id
        )
    except SubscriptionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except SubscriptionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return SubscriptionSyncResponse(**result)
