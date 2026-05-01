"""Media Import service: URL → .loft generation, metadata + caption fetching.

Owns the ``loft_metadata`` table (idempotent CREATE on startup; legacy
``hvlink_metadata`` rename preserved for installs that predate the rename).
The table physically lives in core's SQLite DB because its FK references
``files(id)``; physical separation is deferred (Phase 2 will revisit if
the addon adds its own subscription / import_jobs tables).
"""
import asyncio
import json
import logging
import re
import unicodedata
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import app.config as config
from app.database import SessionLocal
from app.models import File
from app.services.hash import compute_file_hash
from app.services.provider_registry import detect_provider
from app.services.scanner import register_single_file
from sqlalchemy import text

from app.services.ws import broadcast_from_thread, manager
from app.services import event_hooks

from .schemas import LoftFetchItem

logger = logging.getLogger(__name__)

_LOFT_MIME = "application/vnd.litloft.loft+json"


def _sanitize_filename(title: str) -> str:
    """Remove characters that are invalid in filenames."""
    title = re.sub(r'[<>:"/\\|?*]', "_", title)
    title = title.strip(". ")
    return title[:200] if title else "untitled"


def _extract_title_sync(url: str) -> str:
    """Synchronously extract video title via yt-dlp (no download)."""
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        info = ydl.extract_info(url, download=False)
        if info and info.get("title"):
            return info["title"]
    return url.split("/")[-1]


def _fetch_metadata_sync(url: str) -> dict:
    """Synchronously fetch full metadata via yt-dlp (no download)."""
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            return {}
        return {
            "title": info.get("title"),
            "duration": info.get("duration"),
            "description": info.get("description"),
            "channel": info.get("uploader") or info.get("channel"),
            "published_at": info.get("upload_date"),
            "language": info.get("language"),
            "thumbnail_url": info.get("thumbnail"),
            "has_captions": bool(info.get("subtitles") or info.get("automatic_captions")),
        }


_RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "429",
    "Too Many Requests",
    "rate-limit",
    "Sign in to confirm",
)

_PERMANENT_MARKERS: tuple[str, ...] = (
    "Private video",
    "Video unavailable",
    "This video has been removed",
    "age-restricted",
    "members-only",
    "Premieres in",
    "not available in your country",
)


def _classify_caption_error(message: str | None) -> str | None:
    """Classify a yt-dlp error message into a coarse failure kind.

    Returns ``'rate_limited'`` for transient throttling errors,
    ``'permanent'`` for unrecoverable errors (private/removed/age-gated),
    and ``None`` for unknown / generic / network failures.

    The classification is purely string-based: yt-dlp surfaces upstream
    YouTube messages verbatim and the exception type does not carry
    structured fields we can rely on.
    """
    if not message:
        return None
    # Permanent markers are checked first because an "age-restricted"
    # upstream error also contains "Sign in to confirm" — without this
    # ordering it would misclassify as a rate-limit.
    for marker in _PERMANENT_MARKERS:
        if marker in message:
            return "permanent"
    for marker in _RATE_LIMIT_MARKERS:
        if marker in message:
            return "rate_limited"
    return None


def _download_captions_sync(
    url: str, output_stem: Path, language: str | None = None,
) -> tuple[bool, str | None]:
    """Download captions as VTT file adjacent to the .loft file.

    Returns ``(ok, error_kind)`` where ``error_kind`` is the classified
    failure reason (see :func:`_classify_caption_error`) or ``None``.
    """
    import yt_dlp

    lang = language or "ja"
    # Titles may contain dots (e.g. "Ver.3.0.2"); pathlib's suffix/stem
    # would split on the wrong boundary, so operate on the raw name.
    parent = output_stem.parent
    stem_name = output_stem.name
    vtt_path = parent / f"{stem_name}.vtt"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writeautomaticsub": True,
        "writesubtitles": True,
        "subtitlesformat": "vtt",
        "subtitleslangs": [lang],
        # %(ext)s avoids splitext on the template, which would mistake
        # an embedded dot for the extension.
        "outtmpl": str(parent / f"{stem_name}.%(ext)s"),
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        return False, _classify_caption_error(str(exc))

    candidates = sorted(parent.glob(f"{stem_name}.*.vtt"))
    if not candidates:
        return False, None

    best = candidates[0]
    if best.name != vtt_path.name:
        best.rename(vtt_path)
        for c in candidates[1:]:
            if c.exists() and c != vtt_path:
                c.unlink()

    if vtt_path.exists():
        _dedup_rolling_vtt(vtt_path)
        return True, None
    return False, None


def _dedup_rolling_vtt(vtt_path: Path) -> None:
    """Remove duplicates from YouTube's rolling-subtitle VTT in place.

    YouTube auto-captions use a scroll style where each cue carries the
    tail of the previous cue, plus snapshot cues of <=10 ms that are pure
    duplicates.  Rewrites the file keeping only new text per cue.
    """
    import re

    content = vtt_path.read_text(encoding="utf-8", errors="replace")
    timestamp_re = re.compile(
        r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})"
    )
    tag_re = re.compile(r"<[^>]+>")

    def _parse_ts(ts: str) -> float:
        h, m, rest = ts.split(":")
        s, ms = rest.split(".")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

    cues: list[dict] = []
    header_lines: list[str] = []
    current_start = ""
    current_end = ""
    current_lines: list[str] = []
    in_cue = False
    in_header = True

    for line in content.splitlines():
        stripped = line.strip()
        m = timestamp_re.match(stripped)
        if m:
            if in_cue and current_lines:
                cues.append({
                    "start": current_start,
                    "end": current_end,
                    "lines": list(current_lines),
                })
            current_start = m.group(1)
            current_end = m.group(2)
            current_lines = []
            in_cue = True
            in_header = False
            continue
        if not stripped:
            if in_cue and current_lines:
                cues.append({
                    "start": current_start,
                    "end": current_end,
                    "lines": list(current_lines),
                })
                current_lines = []
                in_cue = False
            if in_header:
                header_lines.append("")
            continue
        if in_header:
            header_lines.append(stripped)
            continue
        if in_cue:
            cleaned = tag_re.sub("", stripped)
            if cleaned.strip():
                current_lines.append(cleaned.strip())

    if in_cue and current_lines:
        cues.append({
            "start": current_start,
            "end": current_end,
            "lines": list(current_lines),
        })

    if not cues:
        return

    filtered = [
        c for c in cues
        if _parse_ts(c["end"]) - _parse_ts(c["start"]) > 0.015
    ]
    if not filtered:
        return

    deduped: list[dict] = []
    prev_text = ""
    for cue in filtered:
        text = " ".join(cue["lines"])
        if prev_text and text.startswith(prev_text):
            new_text = text[len(prev_text):].strip()
        else:
            prev_lines = prev_text.split(" ") if prev_text else []
            cur_lines = cue["lines"]
            overlap = 0
            for k in range(1, min(len(prev_lines), len(cur_lines)) + 1):
                if prev_lines[-k:] == cur_lines[:k]:
                    overlap = k
            new_lines = cur_lines[overlap:] if overlap else cur_lines
            new_text = " ".join(new_lines).strip()
        if new_text:
            deduped.append({**cue, "lines": [new_text]})
        prev_text = text

    out = list(header_lines)
    out.append("")
    for i, cue in enumerate(deduped, 1):
        out.append(str(i))
        out.append(f"{cue['start']} --> {cue['end']}")
        out.extend(cue["lines"])
        out.append("")

    vtt_path.write_text("\n".join(out), encoding="utf-8")


def _download_thumbnail_sync(thumbnail_url: str, dest: Path) -> bool:
    """Download thumbnail JPEG to the thumbnails directory."""
    from urllib.parse import urlparse

    parsed = urlparse(thumbnail_url)
    if parsed.scheme not in ("http", "https"):
        logger.warning("Rejected non-HTTP thumbnail URL: %s", thumbnail_url)
        return False

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(thumbnail_url, str(dest))
        return True
    except Exception:
        logger.warning("Failed to download thumbnail: %s", thumbnail_url)
        return False


class LoftManager:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._items: dict[str, LoftFetchItem] = {}

    @property
    def pending_items(self) -> list[LoftFetchItem]:
        return [
            item for item in self._items.values()
            if item.status in ("queued", "fetching")
        ]

    async def start_worker(self) -> None:
        asyncio.create_task(self._worker())
        asyncio.create_task(self._retry_failed_captions())
        logger.info("Loft fetch worker started")

    def create_loft_sync(
        self, url: str, drive: str, folder_path: str
    ) -> tuple[str, str]:
        """Create .loft file and register in DB. Returns (file_id, filename).

        Runs in executor (blocking). Title is fetched synchronously.
        """
        title = _extract_title_sync(url)
        safe_title = _sanitize_filename(title)
        provider = detect_provider(url)

        drive_path = config.get_drive_path(drive)
        output_dir = drive_path / folder_path if folder_path else drive_path
        output_dir.mkdir(parents=True, exist_ok=True)

        loft_content = json.dumps(
            {"provider": provider, "url": url},
            ensure_ascii=False,
            indent=2,
        )

        filename = f"{safe_title}.loft"
        file_path = output_dir / filename

        counter = 1
        while file_path.exists():
            filename = f"{safe_title} ({counter}).loft"
            file_path = output_dir / filename
            counter += 1

        file_path.write_text(loft_content, encoding="utf-8")

        db = SessionLocal()
        try:
            relative_path = unicodedata.normalize(
                "NFC", str(file_path.relative_to(drive_path))
            )
            existing = (
                db.query(File)
                .filter(File.drive == drive, File.file_path == relative_path)
                .first()
            )
            if existing:
                if existing.deleted_at is not None:
                    existing.deleted_at = None
                if existing.missing_since is not None:
                    existing.missing_since = None
                existing.file_size = file_path.stat().st_size
                existing.file_hash = compute_file_hash(file_path)
                db.commit()
                return existing.id, filename

            file_id = register_single_file(db, drive, file_path)
            db.commit()
            return file_id, filename
        except Exception:
            db.rollback()
            if file_path.exists():
                file_path.unlink()
            raise
        finally:
            db.close()

    async def enqueue_fetch(self, file_id: str, url: str, drive: str) -> None:
        """Queue a metadata fetch for a newly created .loft."""
        item = LoftFetchItem(
            file_id=file_id,
            url=url,
            drive=drive,
            status="queued",
        )
        self._items[file_id] = item
        await self._queue.put(file_id)

    async def _worker(self) -> None:
        while True:
            file_id = await self._queue.get()
            item = self._items.get(file_id)
            if not item or item.status != "queued":
                self._queue.task_done()
                continue
            try:
                item.status = "fetching"
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._fetch_and_update, item)
                item.status = "completed"
            except Exception:
                logger.exception("Loft fetch failed: %s", item.url)
                item.status = "error"
            finally:
                self._queue.task_done()

    async def _retry_failed_captions(self) -> None:
        """Retry caption downloads for loft refs that failed previously.

        Runs once at startup after a short delay, then exits.
        """
        await asyncio.sleep(30)

        db = SessionLocal()
        try:
            rows = db.execute(
                text(
                    "SELECT h.file_id, h.url, f.drive "
                    "FROM loft_metadata h "
                    "JOIN files f ON f.id = h.file_id "
                    "WHERE h.has_captions = TRUE "
                    "AND h.captions_downloaded = FALSE "
                    "AND (h.caption_error_kind IS NULL "
                    "     OR h.caption_error_kind != 'permanent') "
                    "AND f.deleted_at IS NULL "
                    "AND f.missing_since IS NULL"
                )
            ).fetchall()
        finally:
            db.close()

        if not rows:
            return

        logger.info("Retrying caption download for %d loft ref(s)", len(rows))
        for file_id, url, drive in rows:
            try:
                await self.enqueue_fetch(file_id, url, drive)
                await asyncio.sleep(5)
            except Exception:
                logger.warning("Failed to queue caption retry for %s", file_id)

    def _fetch_and_update(self, item: LoftFetchItem) -> None:
        meta = _fetch_metadata_sync(item.url)
        if not meta:
            return

        db = SessionLocal()
        try:
            file_record = db.query(File).filter(File.id == item.file_id).first()
            if not file_record:
                logger.warning("File not found for loft fetch: %s", item.file_id)
                return

            if meta.get("duration"):
                file_record.duration = meta["duration"]

            if meta.get("thumbnail_url"):
                nfc_stem = Path(
                    unicodedata.normalize("NFC", file_record.filename)
                ).stem
                thumb_rel = (
                    f"{file_record.drive}/{file_record.folder_path}/{nfc_stem}.jpg"
                    if file_record.folder_path
                    else f"{file_record.drive}/{nfc_stem}.jpg"
                )
                thumb_full = config.THUMBNAILS_DIR / thumb_rel
                if _download_thumbnail_sync(meta["thumbnail_url"], thumb_full):
                    file_record.thumbnail_path = thumb_rel

            db.commit()

            provider = detect_provider(item.url)
            now_iso = datetime.now(UTC).isoformat()
            db.execute(
                text(
                    """
                    INSERT INTO loft_metadata
                        (file_id, provider, url, description, channel,
                         published_at, language, has_captions, fetched_at, fetch_error)
                    VALUES (:file_id, :provider, :url, :description, :channel,
                            :published_at, :language, :has_captions, :fetched_at, NULL)
                    ON CONFLICT(file_id) DO UPDATE SET
                        description = :description,
                        channel = :channel,
                        published_at = :published_at,
                        language = :language,
                        has_captions = :has_captions,
                        fetched_at = :fetched_at,
                        fetch_error = NULL
                    """
                ),
                {
                    "file_id": item.file_id,
                    "provider": provider,
                    "url": item.url,
                    "description": (meta.get("description") or "")[:2000],
                    "channel": meta.get("channel"),
                    "published_at": meta.get("published_at"),
                    "language": meta.get("language"),
                    "has_captions": meta.get("has_captions", False),
                    "fetched_at": now_iso,
                },
            )
            db.commit()

            captions_ok = False
            caption_error_kind: str | None = None
            if meta.get("has_captions"):
                try:
                    drive_path = config.get_drive_path(file_record.drive)
                    loft_path = drive_path / file_record.file_path
                    output_stem = loft_path.with_suffix("")
                    captions_ok, caption_error_kind = _download_captions_sync(
                        item.url, output_stem, language=meta.get("language"),
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to download captions for %s: %s", item.url, exc
                    )
                    caption_error_kind = _classify_caption_error(str(exc))

            # Reset ``caption_error_kind`` on success so a recovered video
            # (permanent → rate_limited → ok) does not retain stale failure
            # metadata.
            db.execute(
                text(
                    "UPDATE loft_metadata "
                    "SET captions_downloaded = :ok, caption_error_kind = :kind "
                    "WHERE file_id = :fid"
                ),
                {
                    "ok": captions_ok,
                    "kind": None if captions_ok else caption_error_kind,
                    "fid": item.file_id,
                },
            )
            db.commit()

            if captions_ok:
                event_hooks.emit_sync(
                    "scan.complete", {"drive": file_record.drive}
                )

            broadcast_from_thread("files.updated", {
                "file_id": item.file_id,
                "drive": file_record.drive,
            }, drive=file_record.drive)

        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def _ensure_loft_table() -> None:
    """Create loft_metadata table if it doesn't exist.

    Migrates legacy hvlink_metadata table via RENAME when present so
    existing installs keep their fetched metadata after the .hvlink →
    .loft rename. RENAME is atomic in SQLite; the follow-up CREATE IF
    NOT EXISTS covers the fresh-install case.
    """
    db = SessionLocal()
    try:
        legacy_exists = db.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='hvlink_metadata'"
            )
        ).first()
        new_exists = db.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='loft_metadata'"
            )
        ).first()
        if legacy_exists and not new_exists:
            db.execute(text("ALTER TABLE hvlink_metadata RENAME TO loft_metadata"))
            db.commit()
            logger.info("Migrated hvlink_metadata → loft_metadata")

        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS loft_metadata (
                    file_id TEXT PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    provider_item_id TEXT,
                    url TEXT NOT NULL,
                    description TEXT,
                    channel TEXT,
                    published_at TEXT,
                    language TEXT,
                    has_captions BOOLEAN DEFAULT FALSE,
                    captions_downloaded BOOLEAN DEFAULT FALSE,
                    caption_error_kind TEXT,
                    fetched_at TEXT,
                    fetch_error TEXT
                )
                """
            )
        )
        try:
            db.execute(
                text(
                    "ALTER TABLE loft_metadata ADD COLUMN "
                    "captions_downloaded BOOLEAN DEFAULT FALSE"
                )
            )
        except Exception:
            pass
        try:
            db.execute(
                text(
                    "ALTER TABLE loft_metadata ADD COLUMN "
                    "caption_error_kind TEXT"
                )
            )
        except Exception:
            pass
        try:
            db.execute(
                text(
                    "ALTER TABLE loft_metadata ADD COLUMN "
                    "provider_item_id TEXT"
                )
            )
        except Exception:
            pass
        db.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_loft_metadata_dedup "
                "ON loft_metadata(provider, provider_item_id)"
            )
        )
        db.commit()
        logger.info("loft_metadata table ensured")
    finally:
        db.close()


def _backfill_provider_item_ids() -> int:
    """Populate ``provider_item_id`` on rows that pre-date Phase 2.

    Scans rows where the column is NULL, dispatches each ``url`` through
    the SubscriptionProvider registry, and writes the resolved item_id
    when the URL points at a single video. Channel / playlist / unknown
    URLs are intentionally left NULL — they wouldn't have a per-video
    id anyway, and the dedup index treats NULLs as non-matching which
    is the correct behavior.

    Idempotent: rows already filled are skipped via the WHERE clause.
    Returns the number of rows updated, for logging / tests.
    """
    from .subscription.registry import (
        REF_KIND_VIDEO,
        find_subscription_provider_by_url,
    )

    updated = 0
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT file_id, url FROM loft_metadata "
                "WHERE provider_item_id IS NULL"
            )
        ).mappings().all()
        for row in rows:
            match = find_subscription_provider_by_url(row["url"])
            if match is None:
                continue
            _, ref = match
            if ref.kind != REF_KIND_VIDEO:
                # Channel / playlist URLs don't yield a per-video id;
                # the .loft is for a video, so this row's url being
                # non-video is unusual but harmless. Leave NULL.
                continue
            db.execute(
                text(
                    "UPDATE loft_metadata SET provider_item_id = :iid "
                    "WHERE file_id = :fid"
                ),
                {"iid": ref.ref, "fid": row["file_id"]},
            )
            updated += 1
        if updated:
            db.commit()
            logger.info("backfilled provider_item_id on %d rows", updated)
    finally:
        db.close()
    return updated


def _ensure_subscription_tables() -> None:
    """Create ``subscriptions`` and ``subscription_videos`` if missing.

    Phase 2 introduces both tables as a single migration unit. Unlike
    ``_ensure_loft_table`` there is no legacy table to migrate from —
    this is brand-new in Phase 2 — so the body is just two
    ``CREATE TABLE IF NOT EXISTS`` statements. ``status`` and
    ``error_kind`` are deliberately stored as plain TEXT (no CHECK
    constraint) so the SubscriptionManager can extend the vocabulary
    without a follow-up migration; the registry module owns the
    canonical value list.

    The ``file_id`` FK uses ``ON DELETE SET NULL`` so a user-deleted
    .loft preserves the subscription_videos row (history of seen items),
    while the ``subscription_id`` FK uses ``ON DELETE CASCADE`` so
    deleting a subscription drops every per-item row in one shot.
    """
    db = SessionLocal()
    try:
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    drive TEXT NOT NULL,
                    folder_path TEXT NOT NULL DEFAULT '',
                    title TEXT,
                    is_enabled BOOLEAN NOT NULL DEFAULT 1,
                    cooldown_minutes INTEGER NOT NULL DEFAULT 60,
                    include_no_transcript BOOLEAN NOT NULL DEFAULT 0,
                    last_synced_at TEXT,
                    cooldown_until TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(provider, source_kind, source_ref, drive, folder_path)
                )
                """
            )
        )
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS subscription_videos (
                    subscription_id INTEGER NOT NULL
                        REFERENCES subscriptions(id) ON DELETE CASCADE,
                    item_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_kind TEXT,
                    file_id TEXT REFERENCES files(id) ON DELETE SET NULL,
                    first_seen_at TEXT NOT NULL,
                    last_attempted_at TEXT,
                    PRIMARY KEY(subscription_id, item_id)
                )
                """
            )
        )
        db.commit()
        logger.info("subscription tables ensured")
    finally:
        db.close()


loft_manager = LoftManager()
