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
