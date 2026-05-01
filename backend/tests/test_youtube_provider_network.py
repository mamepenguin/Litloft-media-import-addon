"""Network-bound method tests for YouTubeProvider.

Commit 3b adds ``list_items`` / ``fetch_item`` / ``fetch_transcript``.
All upstream I/O (RSS HTTP fetch, yt-dlp invocation, file system) is
mocked here — provider tests should not hit the network or run yt-dlp.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from addons.media_import.subscription.providers.youtube import YouTubeProvider
from addons.media_import.subscription.registry import (
    ERROR_NO_TRANSCRIPT,
    ERROR_PERMANENT,
    ERROR_RATE_LIMITED,
    REF_KIND_CHANNEL,
    REF_KIND_PLAYLIST,
    REF_KIND_VIDEO,
    SubscriptionRef,
)


@pytest.fixture()
def provider() -> YouTubeProvider:
    return YouTubeProvider()


# ---- RSS sample ---------------------------------------------------

_RSS_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Channel Title</title>
  <entry>
    <id>yt:video:abc12345678</id>
    <yt:videoId>abc12345678</yt:videoId>
    <yt:channelId>UCfoo</yt:channelId>
    <title>First video</title>
    <published>2026-04-30T10:00:00+00:00</published>
  </entry>
  <entry>
    <id>yt:video:def12345678</id>
    <yt:videoId>def12345678</yt:videoId>
    <yt:channelId>UCfoo</yt:channelId>
    <title>Second video</title>
    <published>2026-04-29T10:00:00+00:00</published>
  </entry>
</feed>
"""


class TestListItemsChannelViaRSS:
    def test_rss_returns_headers(self, provider: YouTubeProvider) -> None:
        ref = SubscriptionRef(kind=REF_KIND_CHANNEL, ref="UCabcdefghijklmnopqrstuv")
        with patch(
            "addons.media_import.subscription.providers.youtube._http_get_bytes",
            return_value=_RSS_SAMPLE,
        ) as mock_fetch:
            items = provider.list_items(ref, limit=15)

        # The fetch URL must include the channel id.
        called_url = mock_fetch.call_args[0][0]
        assert "channel_id=UCabcdefghijklmnopqrstuv" in called_url
        assert "feeds/videos.xml" in called_url

        assert [i.item_id for i in items] == ["abc12345678", "def12345678"]
        assert items[0].title == "First video"
        assert items[0].published_at == "2026-04-30T10:00:00+00:00"

    def test_rss_respects_limit(self, provider: YouTubeProvider) -> None:
        ref = SubscriptionRef(kind=REF_KIND_CHANNEL, ref="UCabcdefghijklmnopqrstuv")
        with patch(
            "addons.media_import.subscription.providers.youtube._http_get_bytes",
            return_value=_RSS_SAMPLE,
        ):
            items = provider.list_items(ref, limit=1)
        assert len(items) == 1
        assert items[0].item_id == "abc12345678"

    def test_rejects_non_canonical_channel_ref(
        self, provider: YouTubeProvider
    ) -> None:
        # SubscriptionManager (Commit 3c) is responsible for canonicalizing
        # @handle / c/name / user/name to UC... before persisting. If a
        # non-canonical ref leaks into list_items, fail loudly rather than
        # silently fetching the wrong feed.
        ref = SubscriptionRef(kind=REF_KIND_CHANNEL, ref="@handle")
        with pytest.raises(ValueError):
            provider.list_items(ref)


class TestListItemsChannelLargeLimitFallback:
    def test_yt_dlp_fallback_when_limit_exceeds_rss(
        self, provider: YouTubeProvider
    ) -> None:
        ref = SubscriptionRef(kind=REF_KIND_CHANNEL, ref="UCabcdefghijklmnopqrstuv")
        flat_entries = [
            {"id": "vid111111111", "title": "Eleven", "upload_date": "20260420"},
            {"id": "vid222222222", "title": "Twelve", "upload_date": None},
        ]
        with patch(
            "addons.media_import.subscription.providers.youtube._yt_dlp_extract_flat",
            return_value=flat_entries,
        ) as mock_flat:
            items = provider.list_items(ref, limit=50)
        # The fallback URL must point at the channel's videos tab.
        called_url, called_limit = mock_flat.call_args[0]
        assert "channel/UCabcdefghijklmnopqrstuv" in called_url
        assert called_limit == 50
        assert [i.item_id for i in items] == ["vid111111111", "vid222222222"]


class TestListItemsPlaylist:
    def test_playlist_uses_yt_dlp(self, provider: YouTubeProvider) -> None:
        ref = SubscriptionRef(
            kind=REF_KIND_PLAYLIST, ref="PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
        )
        flat_entries = [
            {"id": "p_vid_a_aaaa", "title": "A", "upload_date": "20260101"},
            {"id": "p_vid_b_bbbb", "title": "B", "upload_date": "20260102"},
        ]
        with patch(
            "addons.media_import.subscription.providers.youtube._yt_dlp_extract_flat",
            return_value=flat_entries,
        ) as mock_flat:
            items = provider.list_items(ref, limit=None)
        called_url, _ = mock_flat.call_args[0]
        assert "playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf" in called_url
        assert [i.item_id for i in items] == ["p_vid_a_aaaa", "p_vid_b_bbbb"]


# ---- fetch_item ---------------------------------------------------


class TestFetchItem:
    def test_builds_canonical_url_and_maps_metadata(
        self, provider: YouTubeProvider
    ) -> None:
        ref = SubscriptionRef(kind=REF_KIND_CHANNEL, ref="UCabcdefghijklmnopqrstuv")
        meta_dict = {
            "title": "How To",
            "duration": 600,
            "description": "Body",
            "channel": "Author",
            "published_at": "20260415",
            "language": "en",
            "thumbnail_url": "https://i.ytimg.com/x.jpg",
            "has_captions": True,
        }
        with patch(
            "addons.media_import.subscription.providers.youtube"
            "._fetch_metadata_sync_for_provider",
            return_value=meta_dict,
        ) as mock_meta:
            md = provider.fetch_item(ref, "dQw4w9WgXcQ")

        # canonical URL is the watch?v= form (fully qualified, accepted by
        # downstream LoftPlayer dispatch and yt-dlp alike).
        called_url = mock_meta.call_args[0][0]
        assert called_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        assert md.item_id == "dQw4w9WgXcQ"
        assert md.canonical_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert md.title == "How To"
        assert md.duration == 600
        assert md.description == "Body"
        assert md.channel == "Author"
        assert md.published_at == "20260415"
        assert md.language == "en"
        assert md.thumbnail_url == "https://i.ytimg.com/x.jpg"
        assert md.has_captions is True

    def test_falls_back_to_item_id_when_title_missing(
        self, provider: YouTubeProvider
    ) -> None:
        ref = SubscriptionRef(kind=REF_KIND_PLAYLIST, ref="PLfoo")
        # Use a valid 11-char id; the boundary check now rejects anything
        # else outright.
        with patch(
            "addons.media_import.subscription.providers.youtube"
            "._fetch_metadata_sync_for_provider",
            return_value={},
        ):
            md = provider.fetch_item(ref, "missingtitl")
        # Title is required (non-Optional). When yt-dlp returns nothing,
        # fall back to the item_id rather than crashing the whole sync.
        assert md.title == "missingtitl"
        assert md.has_captions is False


# ---- fetch_transcript --------------------------------------------


class _FakeDownloadCaptions:
    """Stand-in for ``_download_captions_sync`` driven by the test."""

    def __init__(
        self,
        ok: bool,
        error_kind: str | None,
        vtt_body: str | None = None,
    ) -> None:
        self.ok = ok
        self.error_kind = error_kind
        self.vtt_body = vtt_body

    def __call__(
        self, url: str, output_stem: Path, language: str | None = None
    ) -> tuple[bool, str | None]:
        if self.ok:
            assert self.vtt_body is not None
            (output_stem.parent / f"{output_stem.name}.vtt").write_text(
                self.vtt_body, encoding="utf-8"
            )
        return self.ok, self.error_kind


class TestItemIdValidation:
    """Untrusted item_id (e.g. retry path param) must not flow into the
    yt-dlp URL without re-validation. A crafted id like ``XX&list=PLevil``
    would otherwise switch yt-dlp into playlist-extraction mode against
    an attacker-chosen list.
    """

    def test_fetch_item_rejects_invalid_id(
        self, provider: YouTubeProvider
    ) -> None:
        ref = SubscriptionRef(kind=REF_KIND_CHANNEL, ref="UCabcdefghijklmnopqrstuv")
        with pytest.raises(ValueError):
            provider.fetch_item(ref, "abc&list=PLevil")
        with pytest.raises(ValueError):
            provider.fetch_item(ref, "")
        with pytest.raises(ValueError):
            provider.fetch_item(ref, "../etc")

    def test_fetch_transcript_rejects_invalid_id(
        self, provider: YouTubeProvider
    ) -> None:
        ref = SubscriptionRef(kind=REF_KIND_CHANNEL, ref="UCabcdefghijklmnopqrstuv")
        with pytest.raises(ValueError):
            provider.fetch_transcript(ref, "abc&list=PLevil")


class TestFetchTranscript:
    def test_success_returns_vtt_text(
        self, provider: YouTubeProvider
    ) -> None:
        ref = SubscriptionRef(kind=REF_KIND_CHANNEL, ref="UCabcdefghijklmnopqrstuv")
        fake = _FakeDownloadCaptions(
            ok=True,
            error_kind=None,
            vtt_body="WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhi\n",
        )
        with patch(
            "addons.media_import.subscription.providers.youtube"
            "._download_captions_sync_for_provider",
            new=fake,
        ):
            result = provider.fetch_transcript(ref, "dQw4w9WgXcQ", language="ja")

        assert result.vtt_text is not None
        assert "WEBVTT" in result.vtt_text
        assert result.language == "ja"
        assert result.error_kind is None

    def test_rate_limited(self, provider: YouTubeProvider) -> None:
        ref = SubscriptionRef(kind=REF_KIND_CHANNEL, ref="UCabcdefghijklmnopqrstuv")
        fake = _FakeDownloadCaptions(ok=False, error_kind=ERROR_RATE_LIMITED)
        with patch(
            "addons.media_import.subscription.providers.youtube"
            "._download_captions_sync_for_provider",
            new=fake,
        ):
            result = provider.fetch_transcript(ref, "dQw4w9WgXcQ")
        assert result.error_kind == ERROR_RATE_LIMITED
        assert result.vtt_text is None

    def test_permanent(self, provider: YouTubeProvider) -> None:
        ref = SubscriptionRef(kind=REF_KIND_CHANNEL, ref="UCabcdefghijklmnopqrstuv")
        fake = _FakeDownloadCaptions(ok=False, error_kind=ERROR_PERMANENT)
        with patch(
            "addons.media_import.subscription.providers.youtube"
            "._download_captions_sync_for_provider",
            new=fake,
        ):
            result = provider.fetch_transcript(ref, "dQw4w9WgXcQ")
        assert result.error_kind == ERROR_PERMANENT

    def test_no_captions_yields_no_transcript(
        self, provider: YouTubeProvider
    ) -> None:
        # _download_captions_sync returns (False, None) when yt-dlp
        # succeeded but produced no .vtt — the video has no captions.
        # Provider must surface this as ERROR_NO_TRANSCRIPT so downstream
        # bookkeeping can distinguish "skip but keep .loft" from a
        # transient failure.
        ref = SubscriptionRef(kind=REF_KIND_CHANNEL, ref="UCabcdefghijklmnopqrstuv")
        fake = _FakeDownloadCaptions(ok=False, error_kind=None)
        with patch(
            "addons.media_import.subscription.providers.youtube"
            "._download_captions_sync_for_provider",
            new=fake,
        ):
            result = provider.fetch_transcript(ref, "dQw4w9WgXcQ")
        assert result.error_kind == ERROR_NO_TRANSCRIPT
        assert result.vtt_text is None
