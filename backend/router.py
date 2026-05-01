"""Media Import addon router.

Endpoints:
- POST /api/addons/media_import/link             — URL → .loft generation
- GET  /api/addons/media_import/link/{file_id}/metadata
- POST /api/addons/media_import/link/{file_id}/refresh

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

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

import app.config as config
from app.database import SessionLocal

from .schemas import (
    LoftCreateRequest,
    LoftCreateResponse,
    LoftMetadataResponse,
)
from .service import loft_manager, _ensure_loft_table
from .provider_registration import register_media_import_providers

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
    _ensure_loft_table()
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
