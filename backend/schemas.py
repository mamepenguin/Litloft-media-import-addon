from typing import Literal

from pydantic import BaseModel, Field

SttMode = Literal["always", "missing_captions", "manual"]


class LoftCreateRequest(BaseModel):
    url: str
    drive: str
    folder_path: str = ""
    stt_mode: SttMode = "manual"


class LoftCreateResponse(BaseModel):
    file_id: str
    filename: str


class LoftFetchItem(BaseModel):
    file_id: str
    url: str
    drive: str
    status: str = "queued"  # queued | fetching | completed | error
    stt_mode: SttMode = "manual"


class LoftMetadataResponse(BaseModel):
    provider: str
    url: str
    description: str | None = None
    channel: str | None = None
    published_at: str | None = None
    language: str | None = None
    has_captions: bool = False
    captions_downloaded: bool = False
    caption_error_kind: str | None = None
    fetched_at: str | None = None
    fetch_error: str | None = None


# ---- Subscription schemas (Phase 2 Commit 4) -----------------------


DisplayMode = Literal["library", "feed", "regular"]
"""How prominently a subscription's new videos appear in Watch.

- ``library``: imported and indexed, never placed in a Watch lane.
- ``feed``: appears in the chronological recent lane.
- ``regular``: appears in the prominent regular-sources lane.

``library`` is the default everywhere — for new subscriptions and, via
the column DEFAULT, for every subscription that predates this field.
Importing a video has never implied an intent to watch it, so opting
*in* is the only way into Watch (spec
``2026-08-10-media-import-watch-surface.md`` §1 / §2.1).
"""


class SubscriptionCreateRequest(BaseModel):
    url: str
    drive: str
    folder_path: str = ""
    cooldown_minutes: int = 60
    include_no_transcript: bool = False
    display_mode: DisplayMode = "library"


class SubscriptionResponse(BaseModel):
    id: int
    provider: str
    source_kind: str
    source_ref: str
    drive: str
    folder_path: str
    title: str | None = None
    is_enabled: bool
    cooldown_minutes: int
    include_no_transcript: bool
    last_synced_at: str | None = None
    cooldown_until: str | None = None
    created_at: str
    running: bool = False  # derived from SubscriptionWorker.running_ids
    # Phase 4 additions; nullable so existing rows that haven't yet
    # backfilled metadata don't crash response serialisation.
    avatar_url: str | None = None
    display_title: str | None = None
    # Rows written before the column existed read back as the DDL
    # default, so this is never actually absent — typed non-optional to
    # keep the frontend from having to handle a third state.
    display_mode: DisplayMode = "library"


class SubscriptionPatchRequest(BaseModel):
    """Partial update for ``PATCH /subscriptions/{id}``.

    All fields optional — Pydantic's default-aware behavior lets the
    handler distinguish "client did not send this field" from "client
    sent null" without an extra wrapper. ``folder_path`` may be empty
    (= drive root) but rejecting None lets the handler use absence as
    a no-op signal.
    """

    is_enabled: bool | None = None
    cooldown_minutes: int | None = None
    include_no_transcript: bool | None = None
    folder_path: str | None = None
    display_title: str | None = None
    display_mode: DisplayMode | None = None


class SubscriptionSummaryResponse(BaseModel):
    """Aggregate health for the subscriptions dashboard header."""

    total: int
    paused: int
    syncing: int
    healthy: int
    attention: int  # subscriptions with >0 failed videos
    imported_count: int
    failed_count: int


class SubscriptionRefreshMetadataResponse(BaseModel):
    """Outcome of POST /subscriptions/{id}/refresh-metadata.

    ``updated`` is True when the provider returned non-None metadata
    (avatar / display_title at least one set). False indicates the
    upstream had nothing fresh to give and DB was left untouched.
    """

    updated: bool
    avatar_url: str | None = None
    display_title: str | None = None


class SubscriptionVideoResponse(BaseModel):
    subscription_id: int
    item_id: str
    status: str
    error_kind: str | None = None
    file_id: str | None = None
    first_seen_at: str
    last_attempted_at: str | None = None
    # Display metadata sourced from the linked .loft (when imported) or
    # from loft_metadata via provider_item_id (when a .loft was created
    # then later soft-deleted). All optional — failed items that never
    # produced a .loft fall back to a generic UI placeholder.
    title: str | None = None
    thumbnail_path: str | None = None
    channel: str | None = None
    published_at: str | None = None


class SubscriptionBackfillRequest(BaseModel):
    """Body for POST /subscriptions/{id}/backfill.

    ``count`` is the number of *additional* upstream items to fetch
    beyond what is already known. The endpoint adds this to the current
    seen-item count so that ``provider.list_items(limit=n_seen+count)``
    naturally skips already-imported items via the seen-set diff.
    """

    count: int = Field(15, ge=1, le=200)


class SubscriptionEnqueueResponse(BaseModel):
    """Response shape for enqueue triggers (POST sync / retry).

    Sync runs on a background worker, so the HTTP response only confirms
    whether the job entered the queue. Completion notifications travel
    over WebSocket (``subscription.sync_completed``).
    """

    status: Literal["queued", "already_queued"]


class SubscriptionResolveRequest(BaseModel):
    url: str


class SubscriptionResolveResponse(BaseModel):
    kind: str  # "video" / "channel" / "playlist" / "feed" / "unknown"
    provider: str | None = None
    ref: str | None = None


# ---- Phase C-2: Activity / resolve-conflict ------------------------


class ActivityEntry(BaseModel):
    """One row in the unified import-activity feed.

    Both single imports (POST /link) and subscription imports surface
    here — the frontend uses ``source`` to render a badge instead of
    splitting into two lists. Sourced from
    ``files JOIN loft_metadata LEFT JOIN subscription_videos`` so the
    timeline is naturally ordered by ``files.created_at``.
    """

    file_id: str
    filename: str
    thumbnail_path: str | None = None
    channel: str | None = None
    published_at: str | None = None
    created_at: str
    source: Literal["single", "subscription"]
    subscription_id: int | None = None
    subscription_title: str | None = None


class ResolveConflictRequest(BaseModel):
    """User-issued resolution for a path_conflict subscription_videos row.

    Three actions:

    - ``skip``: stop trying. Row's error_kind becomes ``'dismissed'`` so
      the retry button is suppressed (user-issued ignore vs. provider
      ``permanent`` failure).
    - ``rename``: enqueue a fresh import attempt. The manager's
      ``_allocate_loft_path`` auto-appends a ``(N)`` suffix on collision
      so the new .loft lands under a unique name.
    - ``overwrite``: same retry path. Distinguished today only for the
      audit trail / future strict-conflict mode; auto-rename means both
      reach the same outcome.
    """

    action: Literal["skip", "rename", "overwrite"]


class ResolveConflictResponse(BaseModel):
    status: Literal["dismissed", "requeued"]


# ---- Watch surface -------------------------------------------------


WatchLane = Literal["continue", "regular", "feed"]
"""Which slice of the library a Watch request is asking for.

One lane per request so each paginates on its own; the page fires
three. Requesting them together would force a single limit across
lanes that grow at completely different rates.
"""


PlaybackState = Literal["not_started", "in_progress", "completed"]
"""Informational only — never a queue position.

Derived from core ``WatchHistory``; Media Import keeps no watched flag
of its own. A view-only ``0 / 0`` row is ``not_started``: opening a
file's detail page is not watching it (spec §4.1).
"""


class WatchPlayback(BaseModel):
    position: float
    duration: float
    state: PlaybackState


class WatchItem(BaseModel):
    """One video in a Watch lane.

    ``playback`` is None when there is no history row for this viewer,
    and also when reading playback state failed — Watch degrades to
    "no badge", never to "no video" (spec §7).
    """

    file_id: str
    filename: str
    title: str | None = None
    thumbnail_path: str | None = None
    channel: str | None = None
    published_at: str | None = None
    created_at: str
    # Media length from the core file record (yt-dlp at import time),
    # not from playback state.
    duration: float | None = None
    url: str
    subscription_id: int | None = None
    subscription_title: str | None = None
    playback: WatchPlayback | None = None
