from pydantic import BaseModel


class LoftCreateRequest(BaseModel):
    url: str
    drive: str
    folder_path: str = ""


class LoftCreateResponse(BaseModel):
    file_id: str
    filename: str


class LoftFetchItem(BaseModel):
    file_id: str
    url: str
    drive: str
    status: str = "queued"  # queued | fetching | completed | error


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


class SubscriptionCreateRequest(BaseModel):
    url: str
    drive: str
    folder_path: str = ""
    cooldown_minutes: int = 60
    include_no_transcript: bool = False


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


class SubscriptionVideoResponse(BaseModel):
    subscription_id: int
    item_id: str
    status: str
    error_kind: str | None = None
    file_id: str | None = None
    first_seen_at: str
    last_attempted_at: str | None = None


class SubscriptionSyncResponse(BaseModel):
    added: int
    reused: int
    failed: int
    total_new: int
