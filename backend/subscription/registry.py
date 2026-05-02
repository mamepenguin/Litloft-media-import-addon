"""Subscription provider abstraction and registry.

A ``SubscriptionProvider`` is the seam that lets Media Import target
multiple upstream sources (YouTube, podcast feeds, Vimeo, ...) with one
job loop, DB schema, and UI. Phase 2 ships a single implementation
(YouTubeProvider) but every consumer routes through this contract — so
adding podcast / Vimeo later is a class addition, not a refactor.

The registry intentionally stays inside the media_import addon (not in
core) for Phase 2: third-party provider support is a Phase 4 concern
once the API has been exercised by N=2+ providers internally
(see hako ``ycNJKe9EJyiNuo9AvmK2F``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


# ---- Value objects -------------------------------------------------

# ``kind`` values that ``resolve_ref`` may return.
REF_KIND_VIDEO = "video"
REF_KIND_CHANNEL = "channel"
REF_KIND_PLAYLIST = "playlist"
REF_KIND_FEED = "feed"

# ``error_kind`` values that flow into ``loft_metadata.caption_error_kind``
# and ``subscription_videos.error_kind``. Permanent must outrank
# rate_limited (see hako ``aOQTKjK0jMtOXG0tAWpad``).
ERROR_RATE_LIMITED = "rate_limited"
ERROR_PERMANENT = "permanent"
ERROR_NO_TRANSCRIPT = "no_transcript"
ERROR_PATH_CONFLICT = "path_conflict"
# User-issued ignore: ``resolve-conflict?action=skip`` flips the row to
# ``error_kind=ERROR_DISMISSED`` so the retry path treats it as a
# permanent decision (no retry button surfaces) without polluting the
# real ``permanent`` semantic.
ERROR_DISMISSED = "dismissed"


@dataclass(frozen=True)
class SubscriptionRef:
    """Normalized handle returned by ``resolve_ref``.

    ``kind == REF_KIND_VIDEO`` signals the URL is a single item — not a
    subscription source. The frontend uses this to dispatch between the
    single-import flow and the subscription creation flow.
    """

    kind: str
    ref: str  # provider-internal identifier (channel_id, playlist_id, video_id, feed url)


@dataclass(frozen=True)
class ItemHeader:
    """Lightweight header used for diff detection (RSS list listing)."""

    item_id: str
    title: str | None = None
    published_at: str | None = None  # ISO 8601 if available


@dataclass(frozen=True)
class ItemMetadata:
    """Full metadata for one item, used to populate ``loft_metadata``."""

    item_id: str
    canonical_url: str
    title: str
    description: str | None = None
    channel: str | None = None
    published_at: str | None = None
    language: str | None = None
    duration: int | None = None
    thumbnail_url: str | None = None
    has_captions: bool = False


@dataclass(frozen=True)
class TranscriptResult:
    """Outcome of a transcript fetch.

    Either ``vtt_text`` is set (success), or ``error_kind`` is non-None.
    Both being None means the provider does not implement transcripts.
    """

    vtt_text: str | None = None
    language: str | None = None
    error_kind: str | None = None


# ---- Protocol ------------------------------------------------------


class SubscriptionProvider(Protocol):
    """Contract every subscription source must satisfy.

    Implementations MUST be safe to call concurrently for ``resolve_ref``
    (pure URL parsing). Network-bound methods (``list_items``,
    ``fetch_item``, ``fetch_transcript``) are called serially by the
    SubscriptionManager — Phase 2 keeps global concurrency at 1.
    """

    name: str
    # Seconds between consecutive ``fetch_transcript`` calls within one
    # subscription. Metadata fetches do not need throttling because they
    # use generic RSS / oEmbed-class endpoints; only transcript fetch
    # has a real 429 risk.
    inter_item_delay_seconds: float

    def resolve_ref(self, url: str) -> SubscriptionRef | None:
        """Parse ``url`` to a normalized ref, or return None if not ours."""
        ...

    def list_items(
        self, ref: SubscriptionRef, limit: int | None = None
    ) -> list[ItemHeader]:
        """Return upstream items for diff detection.

        ``limit=None`` means "all available". Implementations should be
        cheap for typical limits (RSS for YouTube is ~15 items) and may
        fall back to heavier fetchers (yt-dlp) for larger limits.
        """
        ...

    def fetch_item(
        self, ref: SubscriptionRef, item_id: str
    ) -> ItemMetadata:
        """Return full metadata for one item. Raises on permanent failure."""
        ...

    def fetch_transcript(
        self,
        ref: SubscriptionRef,
        item_id: str,
        language: str | None = None,
    ) -> TranscriptResult:
        """Return VTT transcript or an error_kind."""
        ...

    def build_loft_content(self, item: ItemMetadata) -> dict:
        """Return the ``{provider, url, ...}`` dict written into the .loft file."""
        ...


# ---- Registry ------------------------------------------------------


_registry: dict[str, SubscriptionProvider] = {}


def register_subscription_provider(provider: SubscriptionProvider) -> None:
    """Register a provider, keyed on its ``name`` attribute.

    Re-registration overwrites the previous instance (last writer wins),
    matching the policy in core's ``provider_registry.register_provider``
    so reload during dev / tests stays predictable.
    """
    _registry[provider.name] = provider


def get_subscription_provider(name: str) -> SubscriptionProvider | None:
    """Return the registered provider for ``name`` or None."""
    return _registry.get(name)


def find_subscription_provider_by_url(
    url: str,
) -> tuple[SubscriptionProvider, SubscriptionRef] | None:
    """Locate the first provider that recognizes ``url``.

    Returns ``(provider, ref)`` on hit. Iteration order is insertion
    order, so providers registered first win on ambiguous URLs — this
    only matters for URL shapes shared across providers, which is rare.
    """
    for provider in _registry.values():
        ref = provider.resolve_ref(url)
        if ref is not None:
            return provider, ref
    return None


def registered_subscription_provider_names() -> list[str]:
    """Snapshot of registered provider names (for diagnostics / tests)."""
    return list(_registry.keys())


def _reset_subscription_registry_for_tests() -> None:
    """Clear the registry. Test-only escape hatch."""
    _registry.clear()
