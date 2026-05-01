"""Tests for the SubscriptionProvider registry contract.

These exercise registry plumbing only (no network). Provider-specific
behavior lives in test_youtube_provider.py.
"""
from __future__ import annotations

import pytest

from addons.media_import.subscription.registry import (
    REF_KIND_CHANNEL,
    REF_KIND_VIDEO,
    ItemHeader,
    ItemMetadata,
    SubscriptionRef,
    TranscriptResult,
    _reset_subscription_registry_for_tests,
    find_subscription_provider_by_url,
    get_subscription_provider,
    register_subscription_provider,
    registered_subscription_provider_names,
)


class _FakeProvider:
    name = "fake"
    inter_item_delay_seconds = 1.0

    def __init__(self, recognized_prefix: str) -> None:
        self._prefix = recognized_prefix

    def resolve_ref(self, url: str) -> SubscriptionRef | None:
        if url.startswith(self._prefix):
            return SubscriptionRef(kind=REF_KIND_CHANNEL, ref="x")
        return None

    def list_items(self, ref, limit=None):  # pragma: no cover
        return []

    def fetch_item(self, ref, item_id):  # pragma: no cover
        raise NotImplementedError

    def fetch_transcript(self, ref, item_id, language=None):  # pragma: no cover
        raise NotImplementedError

    def build_loft_content(self, item):  # pragma: no cover
        return {"provider": self.name, "url": item.canonical_url}


@pytest.fixture(autouse=True)
def _isolated_registry():
    _reset_subscription_registry_for_tests()
    yield
    _reset_subscription_registry_for_tests()


class TestRegistry:
    def test_register_and_get(self) -> None:
        p = _FakeProvider("https://fake.example/")
        register_subscription_provider(p)
        assert get_subscription_provider("fake") is p

    def test_get_missing_returns_none(self) -> None:
        assert get_subscription_provider("nonexistent") is None

    def test_re_register_overwrites(self) -> None:
        """Last writer wins, matching core's provider_registry policy."""
        first = _FakeProvider("https://a.example/")
        second = _FakeProvider("https://b.example/")
        first.name = "fake"  # type: ignore[misc]
        second.name = "fake"  # type: ignore[misc]
        register_subscription_provider(first)
        register_subscription_provider(second)
        got = get_subscription_provider("fake")
        assert got is second

    def test_registered_names_snapshot(self) -> None:
        p1 = _FakeProvider("https://a/")
        p1.name = "alpha"  # type: ignore[misc]
        p2 = _FakeProvider("https://b/")
        p2.name = "beta"  # type: ignore[misc]
        register_subscription_provider(p1)
        register_subscription_provider(p2)
        assert set(registered_subscription_provider_names()) == {"alpha", "beta"}

    def test_find_by_url_returns_first_match(self) -> None:
        p = _FakeProvider("https://fake.example/")
        register_subscription_provider(p)
        result = find_subscription_provider_by_url(
            "https://fake.example/something"
        )
        assert result is not None
        provider, ref = result
        assert provider is p
        assert ref.ref == "x"

    def test_find_by_url_returns_none_for_unknown(self) -> None:
        register_subscription_provider(_FakeProvider("https://known/"))
        assert (
            find_subscription_provider_by_url("https://other.example/foo")
            is None
        )

    def test_find_by_url_iteration_is_insertion_order(self) -> None:
        """Insertion order resolves ambiguous URLs deterministically."""
        first = _FakeProvider("https://shared/")
        first.name = "first"  # type: ignore[misc]
        second = _FakeProvider("https://shared/")
        second.name = "second"  # type: ignore[misc]
        register_subscription_provider(first)
        register_subscription_provider(second)
        result = find_subscription_provider_by_url("https://shared/foo")
        assert result is not None
        provider, _ = result
        assert provider is first


class TestValueObjects:
    """Frozen dataclasses are hashable + immutable, useful for tests / dedup."""

    def test_subscription_ref_is_frozen(self) -> None:
        ref = SubscriptionRef(kind=REF_KIND_VIDEO, ref="abc")
        with pytest.raises(Exception):
            ref.kind = "channel"  # type: ignore[misc]

    def test_item_header_defaults(self) -> None:
        h = ItemHeader(item_id="x")
        assert h.title is None
        assert h.published_at is None

    def test_item_metadata_required_fields(self) -> None:
        m = ItemMetadata(
            item_id="vid", canonical_url="https://x", title="t"
        )
        assert m.has_captions is False
        assert m.duration is None

    def test_transcript_result_all_optional(self) -> None:
        r = TranscriptResult()
        assert r.vtt_text is None
        assert r.language is None
        assert r.error_kind is None
