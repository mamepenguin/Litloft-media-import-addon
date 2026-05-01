"""URL parsing tests for YouTubeProvider.resolve_ref.

resolve_ref is pure (no I/O), so the test surface is just the URL form
matrix. Network-bound methods are stubbed in Phase 2 Commit 2 and get
their own tests in Commit 3.
"""
from __future__ import annotations

import pytest

from addons.media_import.subscription.providers.youtube import YouTubeProvider
from addons.media_import.subscription.registry import (
    REF_KIND_CHANNEL,
    REF_KIND_PLAYLIST,
    REF_KIND_VIDEO,
    ItemMetadata,
)


@pytest.fixture()
def provider() -> YouTubeProvider:
    return YouTubeProvider()


class TestResolveRefVideo:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "https://www.youtube.com/v/dQw4w9WgXcQ",
            "https://www.youtube.com/live/dQw4w9WgXcQ",
        ],
    )
    def test_recognizes_single_video_forms(
        self, provider: YouTubeProvider, url: str
    ) -> None:
        ref = provider.resolve_ref(url)
        assert ref is not None
        assert ref.kind == REF_KIND_VIDEO
        assert ref.ref == "dQw4w9WgXcQ"

    def test_extra_params_do_not_break_video(
        self, provider: YouTubeProvider
    ) -> None:
        ref = provider.resolve_ref(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42"
        )
        assert ref is not None
        assert ref.kind == REF_KIND_VIDEO
        assert ref.ref == "dQw4w9WgXcQ"


class TestResolveRefChannel:
    def test_channel_id_canonical(self, provider: YouTubeProvider) -> None:
        url = "https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw"
        ref = provider.resolve_ref(url)
        assert ref is not None
        assert ref.kind == REF_KIND_CHANNEL
        assert ref.ref == "UCuAXFkgsw1L7xaCfnd5JJOw"

    def test_handle(self, provider: YouTubeProvider) -> None:
        ref = provider.resolve_ref("https://www.youtube.com/@RickAstleyYT")
        assert ref is not None
        assert ref.kind == REF_KIND_CHANNEL
        assert ref.ref == "@RickAstleyYT"

    def test_legacy_custom_url(self, provider: YouTubeProvider) -> None:
        ref = provider.resolve_ref("https://www.youtube.com/c/RickAstley")
        assert ref is not None
        assert ref.kind == REF_KIND_CHANNEL
        assert ref.ref == "c/RickAstley"

    def test_legacy_user_url(self, provider: YouTubeProvider) -> None:
        ref = provider.resolve_ref("https://www.youtube.com/user/RickAstleyVEVO")
        assert ref is not None
        assert ref.kind == REF_KIND_CHANNEL
        assert ref.ref == "user/RickAstleyVEVO"

    def test_invalid_channel_id_format_rejected(
        self, provider: YouTubeProvider
    ) -> None:
        # Channel ids must match UC + 22 chars; anything else is not
        # canonical and should not be accepted as kind=channel.
        ref = provider.resolve_ref("https://www.youtube.com/channel/notuc")
        assert ref is None


class TestResolveRefPlaylist:
    def test_explicit_playlist_url(self, provider: YouTubeProvider) -> None:
        url = (
            "https://www.youtube.com/playlist"
            "?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
        )
        ref = provider.resolve_ref(url)
        assert ref is not None
        assert ref.kind == REF_KIND_PLAYLIST
        assert ref.ref == "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"

    def test_playlist_wins_over_video_when_both_present(
        self, provider: YouTubeProvider
    ) -> None:
        """``watch?v=...&list=...`` is more useful as a subscription source.

        Without this rule the URL would resolve to a single video, and a
        user pasting a playlist player URL would not get a subscription
        flow despite obviously wanting one.
        """
        url = (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            "&list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
        )
        ref = provider.resolve_ref(url)
        assert ref is not None
        assert ref.kind == REF_KIND_PLAYLIST


class TestResolveRefRejection:
    @pytest.mark.parametrize(
        "url",
        [
            "https://vimeo.com/123",
            "https://example.com/watch?v=dQw4w9WgXcQ",
            "not a url",
            "",
            "https://youtube.com/",  # bare host, no recognizable path
        ],
    )
    def test_non_youtube_or_unknown_returns_none(
        self, provider: YouTubeProvider, url: str
    ) -> None:
        assert provider.resolve_ref(url) is None


class TestBuildLoftContent:
    def test_minimal_shape(self, provider: YouTubeProvider) -> None:
        item = ItemMetadata(
            item_id="dQw4w9WgXcQ",
            canonical_url="https://youtu.be/dQw4w9WgXcQ",
            title="Never Gonna Give You Up",
        )
        body = provider.build_loft_content(item)
        # Phase 0 locks the {provider, url} schema; do not extend lightly.
        assert body == {
            "provider": "youtube",
            "url": "https://youtu.be/dQw4w9WgXcQ",
        }

    def test_does_not_leak_extra_metadata(
        self, provider: YouTubeProvider
    ) -> None:
        """``loft_metadata`` is the home for description / channel / etc.

        The .loft file body itself stays minimal so player dispatch
        (LoftPlayer) reads only what it needs.
        """
        item = ItemMetadata(
            item_id="x",
            canonical_url="https://youtu.be/x",
            title="t",
            description="d",
            channel="c",
            published_at="2026-01-01",
            language="ja",
            duration=42,
            thumbnail_url="https://img/x.jpg",
            has_captions=True,
        )
        body = provider.build_loft_content(item)
        assert set(body.keys()) == {"provider", "url"}


class TestProviderAttributes:
    def test_name_and_delay(self, provider: YouTubeProvider) -> None:
        assert provider.name == "youtube"
        # Phase 2 default: 3 sec inter-item delay for transcript fetches.
        assert provider.inter_item_delay_seconds == 3.0
