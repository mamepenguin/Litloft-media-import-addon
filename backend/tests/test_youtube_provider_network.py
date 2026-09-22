"""Network-bound method tests for YouTubeProvider.

Commit 3b adds ``list_items`` / ``fetch_item`` / ``fetch_transcript``.
All upstream I/O (RSS HTTP fetch, yt-dlp invocation, file system) is
mocked here — provider tests should not hit the network or run yt-dlp.
"""
from __future__ import annotations

import http.client
import urllib.error
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
            "addons.media_import.subscription.providers.youtube._http_get_bytes",
        ) as mock_fetch, patch(
            "addons.media_import.subscription.providers.youtube._yt_dlp_extract_flat",
            return_value=flat_entries,
        ) as mock_flat:
            items = provider.list_items(ref, limit=50)
        # The fallback URL must point at the channel's videos tab.
        called_url, called_limit = mock_flat.call_args[0]
        assert "channel/UCabcdefghijklmnopqrstuv" in called_url
        assert mock_fetch.call_count == 0
        assert called_limit == 50
        assert [i.item_id for i in items] == ["vid111111111", "vid222222222"]

    def test_empty_backfill_is_a_legitimate_answer(
        self, provider: YouTubeProvider
    ) -> None:
        # Nothing failed here, so there is no failure to re-raise.
        ref = SubscriptionRef(kind=REF_KIND_CHANNEL, ref="UCabcdefghijklmnopqrstuv")
        with patch(
            "addons.media_import.subscription.providers.youtube._yt_dlp_extract_flat",
            return_value=[],
        ):
            assert provider.list_items(ref, limit=50) == []


class TestListItemsChannelRSSFallback:
    """The RSS feed endpoint goes down for days at a time upstream, returning
    404 or 500 for channels that exist. yt-dlp keeps working through it.
    """

    _FLAT_ENTRIES = [
        {"id": "fb_vid_aaaa", "title": "Fallback A", "upload_date": "20260901"},
        {"id": "fb_vid_bbbb", "title": "Fallback B", "upload_date": "20260902"},
    ]

    def _ref(self) -> SubscriptionRef:
        return SubscriptionRef(
            kind=REF_KIND_CHANNEL, ref="UCabcdefghijklmnopqrstuv"
        )

    def test_http_error_falls_back_to_yt_dlp_capped_at_rss_size(
        self, provider: YouTubeProvider
    ) -> None:
        # A cron sync arrives with limit=None. Handing that to yt-dlp
        # unchanged enumerates the channel's entire history.
        error = urllib.error.HTTPError(
            "https://www.youtube.com/feeds/videos.xml", 404, "Not Found", {}, None
        )
        with patch(
            "addons.media_import.subscription.providers.youtube._http_get_bytes",
            side_effect=error,
        ), patch(
            "addons.media_import.subscription.providers.youtube._yt_dlp_extract_flat",
            return_value=self._FLAT_ENTRIES,
        ) as mock_flat:
            items = provider.list_items(self._ref(), limit=None)

        called_url, called_limit = mock_flat.call_args[0]
        assert called_url == (
            "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv/videos"
        )
        assert called_limit == 15
        assert [i.item_id for i in items] == ["fb_vid_aaaa", "fb_vid_bbbb"]

    def test_unparseable_body_falls_back_to_yt_dlp(
        self, provider: YouTubeProvider
    ) -> None:
        # Google's error page, whose unquoted attribute values are not XML.
        with patch(
            "addons.media_import.subscription.providers.youtube._http_get_bytes",
            return_value=(
                b"<!DOCTYPE html>\n<html lang=en>\n  <meta charset=utf-8>\n"
                b"  <title>Error 404 (Not Found)!!1</title>\n"
            ),
        ), patch(
            "addons.media_import.subscription.providers.youtube._yt_dlp_extract_flat",
            return_value=self._FLAT_ENTRIES,
        ) as mock_flat:
            items = provider.list_items(self._ref(), limit=None)

        _, called_limit = mock_flat.call_args[0]
        assert called_limit == 15
        assert [i.item_id for i in items] == ["fb_vid_aaaa", "fb_vid_bbbb"]

    def test_non_feed_document_falls_back_to_yt_dlp(
        self, provider: YouTubeProvider
    ) -> None:
        # Well-formed XML with no Atom entries parses cleanly and yields an
        # empty list, which is indistinguishable from "no new videos".
        with patch(
            "addons.media_import.subscription.providers.youtube._http_get_bytes",
            return_value=b"<html><body><p>404.</p></body></html>",
        ), patch(
            "addons.media_import.subscription.providers.youtube._yt_dlp_extract_flat",
            return_value=self._FLAT_ENTRIES,
        ) as mock_flat:
            items = provider.list_items(self._ref(), limit=None)

        _, called_limit = mock_flat.call_args[0]
        assert called_limit == 15
        assert [i.item_id for i in items] == ["fb_vid_aaaa", "fb_vid_bbbb"]

    def test_explicit_small_limit_survives_the_fallback(
        self, provider: YouTubeProvider
    ) -> None:
        error = urllib.error.HTTPError(
            "https://www.youtube.com/feeds/videos.xml", 500, "Server Error", {}, None
        )
        with patch(
            "addons.media_import.subscription.providers.youtube._http_get_bytes",
            side_effect=error,
        ), patch(
            "addons.media_import.subscription.providers.youtube._yt_dlp_extract_flat",
            return_value=self._FLAT_ENTRIES,
        ) as mock_flat:
            provider.list_items(self._ref(), limit=5)

        _, called_limit = mock_flat.call_args[0]
        assert called_limit == 5

    def test_both_paths_failing_raises(self, provider: YouTubeProvider) -> None:
        # Swallowing this would be indistinguishable from "no new videos",
        # and the cron backoff would never fire.
        error = urllib.error.HTTPError(
            "https://www.youtube.com/feeds/videos.xml", 404, "Not Found", {}, None
        )
        with patch(
            "addons.media_import.subscription.providers.youtube._http_get_bytes",
            side_effect=error,
        ), patch(
            "addons.media_import.subscription.providers.youtube._yt_dlp_extract_flat",
            side_effect=RuntimeError("yt-dlp exploded"),
        ):
            with pytest.raises(RuntimeError):
                provider.list_items(self._ref(), limit=None)

    def test_response_phase_failure_also_falls_back(
        self, provider: YouTubeProvider
    ) -> None:
        # urllib wraps the connect phase in URLError; a body cut short
        # after the status line arrives as http.client.IncompleteRead.
        with patch(
            "addons.media_import.subscription.providers.youtube._http_get_bytes",
            side_effect=http.client.IncompleteRead(b"", 10),
        ), patch(
            "addons.media_import.subscription.providers.youtube._yt_dlp_extract_flat",
            return_value=self._FLAT_ENTRIES,
        ) as mock_flat:
            items = provider.list_items(self._ref(), limit=None)

        _, called_limit = mock_flat.call_args[0]
        assert called_limit == 15
        assert [i.item_id for i in items] == ["fb_vid_aaaa", "fb_vid_bbbb"]

    def test_read_timeout_also_falls_back(
        self, provider: YouTubeProvider
    ) -> None:
        with patch(
            "addons.media_import.subscription.providers.youtube._http_get_bytes",
            side_effect=TimeoutError("timed out"),
        ), patch(
            "addons.media_import.subscription.providers.youtube._yt_dlp_extract_flat",
            return_value=self._FLAT_ENTRIES,
        ) as mock_flat:
            provider.list_items(self._ref(), limit=None)

        assert mock_flat.call_count == 1

    def test_empty_fallback_reraises_the_rss_failure(
        self, provider: YouTubeProvider
    ) -> None:
        # yt-dlp served a consent or bot-check page reports success with no
        # entries. Returning [] would advance last_synced_at and clear the
        # cooldown, reporting a healthy subscription that imports nothing.
        error = urllib.error.HTTPError(
            "https://www.youtube.com/feeds/videos.xml", 404, "Not Found", {}, None
        )
        with patch(
            "addons.media_import.subscription.providers.youtube._http_get_bytes",
            side_effect=error,
        ), patch(
            "addons.media_import.subscription.providers.youtube._yt_dlp_extract_flat",
            return_value=[],
        ):
            with pytest.raises(urllib.error.HTTPError):
                provider.list_items(self._ref(), limit=None)

    def test_successful_rss_does_not_reach_yt_dlp(
        self, provider: YouTubeProvider
    ) -> None:
        with patch(
            "addons.media_import.subscription.providers.youtube._http_get_bytes",
            return_value=_RSS_SAMPLE,
        ), patch(
            "addons.media_import.subscription.providers.youtube._yt_dlp_extract_flat",
        ) as mock_flat:
            items = provider.list_items(self._ref(), limit=None)

        assert mock_flat.call_count == 0
        assert [i.item_id for i in items] == ["abc12345678", "def12345678"]


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
            "addons.media_import.subscription.providers.youtube._http_get_bytes",
        ) as mock_fetch, patch(
            "addons.media_import.subscription.providers.youtube._yt_dlp_extract_flat",
            return_value=flat_entries,
        ) as mock_flat:
            items = provider.list_items(ref, limit=None)
        called_url, _ = mock_flat.call_args[0]
        assert "playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf" in called_url
        assert mock_fetch.call_count == 0
        assert [i.item_id for i in items] == ["p_vid_a_aaaa", "p_vid_b_bbbb"]

    def test_empty_playlist_is_a_legitimate_answer(
        self, provider: YouTubeProvider
    ) -> None:
        ref = SubscriptionRef(
            kind=REF_KIND_PLAYLIST, ref="PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
        )
        with patch(
            "addons.media_import.subscription.providers.youtube._yt_dlp_extract_flat",
            return_value=[],
        ):
            assert provider.list_items(ref, limit=None) == []


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
