"""YouTubeProvider — the first SubscriptionProvider implementation.

Phase 2 scope: ``resolve_ref`` + ``build_loft_content`` (pure parsing /
construction) plus ``list_items`` / ``fetch_item`` / ``fetch_transcript``
(network-bound). The SubscriptionManager (Commit 3c) drives them.

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

import logging
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree as ET

from ..registry import (
    ERROR_NO_TRANSCRIPT,
    REF_KIND_CHANNEL,
    REF_KIND_PLAYLIST,
    REF_KIND_VIDEO,
    ItemHeader,
    ItemMetadata,
    SourceMetadata,
    SubscriptionRef,
    TranscriptResult,
)


logger = logging.getLogger(__name__)


# RSS feed cap: YouTube's Atom feed always returns up to 15 entries and
# never honors a count parameter, so any limit beyond 15 must fall back
# to yt-dlp.
_RSS_MAX_ITEMS = 15

# Atom + YouTube namespaces used by feeds/videos.xml.
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_YT_NS = "{http://www.youtube.com/xml/schemas/2015}"


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


class _YouTubeOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Block redirects that leave the YouTube host set.

    Defense-in-depth: ``_http_get_bytes`` is only called with URLs we
    construct ourselves from a regex-validated ``UC...`` channel id, so
    a primary-request SSRF is not possible today. But ``urllib.request``
    follows arbitrary 3xx Location headers including cross-host and (on
    some Python versions) cross-scheme redirects, so a hostile
    intermediary could redirect into an internal network. Restricting
    redirect targets to the YouTube host set closes that avenue without
    blocking legitimate www→m or canonical→cache hops.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target_host = (urlparse(newurl).hostname or "").lower()
        if target_host not in _YOUTUBE_HOSTS:
            raise urllib.error.HTTPError(
                newurl, code, f"redirect to non-YouTube host blocked: {target_host}",
                headers, fp,
            )
        return super().redirect_request(
            req, fp, code, msg, headers, newurl,
        )


_youtube_opener = urllib.request.build_opener(_YouTubeOnlyRedirectHandler())


def _http_get_bytes(url: str, timeout: float = 10.0) -> bytes:
    """Fetch ``url`` and return raw body. Module-level for test patching.

    Uses a custom OpenerDirector that rejects redirects to hosts outside
    ``_YOUTUBE_HOSTS`` (see ``_YouTubeOnlyRedirectHandler``).
    """
    with _youtube_opener.open(url, timeout=timeout) as resp:
        return resp.read()


def _yt_dlp_extract_flat(url: str, limit: int | None) -> list[dict]:
    """Enumerate items at ``url`` via yt-dlp ``extract_flat``.

    Returns the raw ``entries`` list; callers map each entry to
    ``ItemHeader``. Module-level for test patching.
    """
    import yt_dlp

    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }
    if limit is not None:
        opts["playlistend"] = limit
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        return []
    return list(info.get("entries") or [])


def _yt_dlp_source_info(url: str) -> dict:
    """Fetch the *envelope* dict for a channel/playlist URL.

    Used by ``fetch_source_metadata``. ``playlistend=1`` keeps yt-dlp
    from enumerating every item — we only need the top-level fields
    (title, thumbnails, channel, uploader). Module-level for tests to
    patch without spinning up yt-dlp.
    """
    import yt_dlp

    opts: dict = {
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
        logger.warning("yt-dlp source info failed for %s: %s", url, exc)
        return {}
    return info or {}


def _pick_avatar_url(thumbnails: list[dict] | None) -> str | None:
    """Choose a reasonably sized avatar/cover from yt-dlp's thumbnails.

    yt-dlp returns a list ordered roughly by quality. Prefer entries
    flagged as ``avatar_uncropped`` (channels) or ``maxresdefault``
    (playlists), falling back to the largest non-banner entry. Banner
    images are wide and unsuitable as a square avatar.
    """
    if not thumbnails:
        return None
    by_id = {t.get("id"): t for t in thumbnails if isinstance(t, dict)}
    for preferred in ("avatar_uncropped", "maxresdefault", "hqdefault"):
        t = by_id.get(preferred)
        if t and isinstance(t.get("url"), str):
            return t["url"]
    candidates = [
        t for t in thumbnails
        if isinstance(t, dict)
        and isinstance(t.get("url"), str)
        and t.get("id") not in {"banner_uncropped"}
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda t: (t.get("width") or 0) * (t.get("height") or 0),
        reverse=True,
    )
    return candidates[0]["url"]


def _fetch_metadata_sync_for_provider(url: str) -> dict:
    """Indirection so tests can patch metadata fetch at the provider module."""
    from ...service import _fetch_metadata_sync

    return _fetch_metadata_sync(url)


def _download_captions_sync_for_provider(
    url: str, output_stem: Path, language: str | None = None
) -> tuple[bool, str | None]:
    """Indirection so tests can patch caption download at the provider module."""
    from ...service import _download_captions_sync

    return _download_captions_sync(url, output_stem, language=language)


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

    def fetch_source_metadata(
        self, ref: SubscriptionRef
    ) -> SourceMetadata | None:
        """Resolve the channel/playlist title + avatar via yt-dlp.

        Channel: ``channel`` / ``uploader`` field plus the ``avatar``
        thumbnail. Playlist: ``title`` field plus the playlist cover
        (highest-resolution non-banner thumbnail).

        Single-video subs do not exist (subscriptions only target
        channel/playlist), so VIDEO refs raise.
        """
        if ref.kind == REF_KIND_CHANNEL:
            if not _CHANNEL_ID_RE.match(ref.ref):
                # Pre-canonicalization (handle/legacy): manager will
                # canonicalize first; refuse to touch the network on a
                # non-canonical ref to avoid double yt-dlp roundtrips.
                return None
            url = f"https://www.youtube.com/channel/{ref.ref}"
        elif ref.kind == REF_KIND_PLAYLIST:
            if not _PLAYLIST_ID_RE.match(ref.ref):
                return None
            url = f"https://www.youtube.com/playlist?list={ref.ref}"
        else:
            return None

        info = _yt_dlp_source_info(url)
        if not info:
            return None
        title = (
            info.get("channel")
            or info.get("uploader")
            or info.get("title")
        )
        avatar = _pick_avatar_url(info.get("thumbnails"))
        if not title and not avatar:
            return None
        return SourceMetadata(
            title=title if isinstance(title, str) else None,
            avatar_url=avatar,
        )

    # ---- network-bound -------------------------------------------

    def list_items(
        self, ref: SubscriptionRef, limit: int | None = None
    ) -> list[ItemHeader]:
        if ref.kind == REF_KIND_CHANNEL:
            if not _CHANNEL_ID_RE.match(ref.ref):
                raise ValueError(
                    f"YouTubeProvider.list_items requires a canonical "
                    f"UC... channel id; got {ref.ref!r}. SubscriptionManager "
                    f"must canonicalize handles before calling list_items."
                )
            if limit is None or limit <= _RSS_MAX_ITEMS:
                return self._list_channel_via_rss(ref.ref, limit)
            url = f"https://www.youtube.com/channel/{ref.ref}/videos"
            return self._yt_dlp_headers(url, limit)

        if ref.kind == REF_KIND_PLAYLIST:
            url = f"https://www.youtube.com/playlist?list={ref.ref}"
            return self._yt_dlp_headers(url, limit)

        raise ValueError(f"unsupported ref kind: {ref.kind!r}")

    def _list_channel_via_rss(
        self, channel_id: str, limit: int | None
    ) -> list[ItemHeader]:
        url = (
            "https://www.youtube.com/feeds/videos.xml"
            f"?channel_id={channel_id}"
        )
        body = _http_get_bytes(url)
        root = ET.fromstring(body)
        headers: list[ItemHeader] = []
        for entry in root.findall(f"{_ATOM_NS}entry"):
            vid_el = entry.find(f"{_YT_NS}videoId")
            if vid_el is None or not vid_el.text:
                continue
            title_el = entry.find(f"{_ATOM_NS}title")
            published_el = entry.find(f"{_ATOM_NS}published")
            headers.append(
                ItemHeader(
                    item_id=vid_el.text.strip(),
                    title=(title_el.text.strip() if title_el is not None and title_el.text else None),
                    published_at=(
                        published_el.text.strip()
                        if published_el is not None and published_el.text
                        else None
                    ),
                )
            )
            if limit is not None and len(headers) >= limit:
                break
        return headers

    def _yt_dlp_headers(
        self, url: str, limit: int | None
    ) -> list[ItemHeader]:
        entries = _yt_dlp_extract_flat(url, limit)
        return [
            ItemHeader(
                item_id=str(e.get("id")),
                title=e.get("title"),
                published_at=e.get("upload_date"),
            )
            for e in entries
            if e.get("id")
        ]

    def fetch_item(
        self, ref: SubscriptionRef, item_id: str
    ) -> ItemMetadata:
        # ``item_id`` reaches us from RSS / yt-dlp / the retry endpoint
        # path param. The first two are trusted upstream but never
        # re-validated; the path param is wholly user-controlled. A
        # crafted id like ``abc&list=PLevil`` would, when interpolated
        # into the URL, switch yt-dlp into playlist-extraction mode for
        # an attacker-chosen list. Fail fast at the boundary.
        if not _VIDEO_ID_RE.match(item_id):
            raise ValueError(f"invalid YouTube item_id: {item_id!r}")
        canonical_url = f"https://www.youtube.com/watch?v={item_id}"
        meta = _fetch_metadata_sync_for_provider(canonical_url)
        return ItemMetadata(
            item_id=item_id,
            canonical_url=canonical_url,
            # yt-dlp occasionally returns nothing (geo-blocked private listing
            # buried in a public playlist, etc.). Falling back to item_id keeps
            # the sync moving instead of aborting the whole batch.
            title=meta.get("title") or item_id,
            description=meta.get("description"),
            channel=meta.get("channel"),
            published_at=meta.get("published_at"),
            language=meta.get("language"),
            duration=meta.get("duration"),
            thumbnail_url=meta.get("thumbnail_url"),
            has_captions=bool(meta.get("has_captions")),
        )

    def fetch_transcript(
        self,
        ref: SubscriptionRef,
        item_id: str,
        language: str | None = None,
    ) -> TranscriptResult:
        if not _VIDEO_ID_RE.match(item_id):
            raise ValueError(f"invalid YouTube item_id: {item_id!r}")
        canonical_url = f"https://www.youtube.com/watch?v={item_id}"
        lang = language or "ja"
        with tempfile.TemporaryDirectory(prefix="ytsub_") as td:
            stem = Path(td) / item_id
            ok, error_kind = _download_captions_sync_for_provider(
                canonical_url, stem, language=lang
            )
            if ok:
                vtt_path = stem.parent / f"{stem.name}.vtt"
                try:
                    text_body = vtt_path.read_text(encoding="utf-8")
                except OSError:
                    return TranscriptResult(
                        error_kind=ERROR_NO_TRANSCRIPT
                    )
                return TranscriptResult(
                    vtt_text=text_body, language=lang
                )
            if error_kind is None:
                # _download_captions_sync returns (False, None) when yt-dlp
                # produced no .vtt despite no exception — the video has no
                # captions of any kind. Promote to NO_TRANSCRIPT so the
                # SubscriptionManager can keep the .loft and not retry.
                return TranscriptResult(error_kind=ERROR_NO_TRANSCRIPT)
            return TranscriptResult(error_kind=error_kind)
