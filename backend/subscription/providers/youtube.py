"""YouTubeProvider — the first SubscriptionProvider implementation.

Phase 2 scope: ``resolve_ref`` + ``build_loft_content`` (pure functions
exercised by the registry and backfill paths). The remaining methods
(``list_items``, ``fetch_item``, ``fetch_transcript``) are stubbed to
NotImplementedError and will be filled in Commit 3, where the
SubscriptionManager arrives. Splitting like this keeps Commit 2 a
pure-refactor diff testable without HTTP mocks.

URL forms recognized:

- Single video:
    https://www.youtube.com/watch?v=VIDEO_ID
    https://m.youtube.com/watch?v=VIDEO_ID
    https://youtu.be/VIDEO_ID
    https://www.youtube.com/embed/VIDEO_ID
    https://www.youtube.com/shorts/VIDEO_ID
- Channel (id):
    https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx
- Channel (handle, resolved later):
    https://www.youtube.com/@handle
    https://www.youtube.com/c/customname  (legacy custom URL)
    https://www.youtube.com/user/legacyname  (legacy username)
- Playlist:
    https://www.youtube.com/playlist?list=PLxxxx
    https://www.youtube.com/watch?v=VIDEO_ID&list=PLxxxx  → playlist wins

Channel handles / custom names / usernames cannot be resolved to a stable
``UC...`` channel_id without a network roundtrip. ``resolve_ref`` keeps
the raw handle in ``ref`` and tags ``kind=channel``; the SubscriptionManager
(Commit 3) will canonicalize on subscription creation via yt-dlp before
inserting the row, so the DB always stores ``UC...`` form.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from ..registry import (
    REF_KIND_CHANNEL,
    REF_KIND_PLAYLIST,
    REF_KIND_VIDEO,
    ItemHeader,
    ItemMetadata,
    SubscriptionRef,
    TranscriptResult,
)


_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_PLAYLIST_ID_RE = re.compile(r"^(PL|UU|FL|RD|OL|LL)[A-Za-z0-9_-]+$")
_HANDLE_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")

_YOUTUBE_HOSTS = frozenset(
    [
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
    ]
)


def _video_id_from_short(path: str) -> str | None:
    """``/{video_id}`` on the youtu.be host."""
    seg = path.strip("/").split("/", 1)[0]
    return seg if _VIDEO_ID_RE.match(seg) else None


def _video_id_from_long(parsed) -> str | None:
    """``/watch?v=VIDEO_ID`` / ``/embed/VIDEO_ID`` / ``/shorts/VIDEO_ID``."""
    path = parsed.path
    if path == "/watch":
        v = parse_qs(parsed.query).get("v", [None])[0]
        return v if v and _VIDEO_ID_RE.match(v) else None
    for prefix in ("/embed/", "/shorts/", "/v/", "/live/"):
        if path.startswith(prefix):
            seg = path[len(prefix):].split("/", 1)[0]
            return seg if _VIDEO_ID_RE.match(seg) else None
    return None


def _channel_ref(parsed) -> tuple[str, str] | None:
    """Return ``(kind_value, ref)`` for a channel-like URL.

    ``ref`` is either a ``UC...`` id (canonical) or a raw handle/legacy
    name (``@handle``, ``c/name``, ``user/name``). Canonicalization to
    ``UC...`` happens on subscription creation.
    """
    path = parsed.path
    parts = path.strip("/").split("/")
    if not parts:
        return None
    head = parts[0]
    if head == "channel" and len(parts) >= 2 and _CHANNEL_ID_RE.match(parts[1]):
        return "id", parts[1]
    if head.startswith("@"):
        handle = head[1:]
        return ("handle", f"@{handle}") if _HANDLE_RE.match(handle) else None
    if head in ("c", "user") and len(parts) >= 2:
        return f"legacy_{head}", f"{head}/{parts[1]}"
    return None


def _playlist_id(parsed) -> str | None:
    if parsed.path not in ("/playlist", "/watch"):
        return None
    pid = parse_qs(parsed.query).get("list", [None])[0]
    return pid if pid and _PLAYLIST_ID_RE.match(pid) else None


class YouTubeProvider:
    """SubscriptionProvider for YouTube channels, playlists, and videos."""

    name = "youtube"
    inter_item_delay_seconds = 3.0

    def resolve_ref(self, url: str) -> SubscriptionRef | None:
        try:
            parsed = urlparse(url)
        except Exception:  # pragma: no cover — urlparse is forgiving
            return None
        host = parsed.hostname or ""
        if host not in _YOUTUBE_HOSTS:
            return None

        # Playlist takes precedence over the bare watch?v= form: a
        # ``/watch?v=...&list=...`` URL is more useful as a subscription
        # source than as a single video.
        pid = _playlist_id(parsed)
        if pid:
            return SubscriptionRef(kind=REF_KIND_PLAYLIST, ref=pid)

        if host == "youtu.be":
            vid = _video_id_from_short(parsed.path)
            if vid:
                return SubscriptionRef(kind=REF_KIND_VIDEO, ref=vid)
            return None

        vid = _video_id_from_long(parsed)
        if vid:
            return SubscriptionRef(kind=REF_KIND_VIDEO, ref=vid)

        ch = _channel_ref(parsed)
        if ch:
            # ``ref`` is "UC..." for canonical, "@handle" / "c/name" /
            # "user/name" for legacy forms; SubscriptionManager
            # canonicalizes before persisting.
            return SubscriptionRef(kind=REF_KIND_CHANNEL, ref=ch[1])

        return None

    def build_loft_content(self, item: ItemMetadata) -> dict:
        """Compose the JSON body of a .loft file pointing at this item.

        Schema: ``{provider, url}`` (locked by Phase 0; do not extend
        without updating LoftPlayer dispatch).
        """
        return {
            "provider": self.name,
            "url": item.canonical_url,
        }

    # ---- network-bound; Commit 3 fills these ----

    def list_items(
        self, ref: SubscriptionRef, limit: int | None = None
    ) -> list[ItemHeader]:
        raise NotImplementedError("list_items lands in Commit 3")

    def fetch_item(
        self, ref: SubscriptionRef, item_id: str
    ) -> ItemMetadata:
        raise NotImplementedError("fetch_item lands in Commit 3")

    def fetch_transcript(
        self,
        ref: SubscriptionRef,
        item_id: str,
        language: str | None = None,
    ) -> TranscriptResult:
        raise NotImplementedError("fetch_transcript lands in Commit 3")
