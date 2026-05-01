"""Tests for Media Import provider registrations.

The addon's ``on_startup`` registers youtube/vimeo/soundcloud in core's
``provider_registry``. These tests verify the registrations match the
URL patterns the .loft pipeline relies on.
"""
from __future__ import annotations

import pytest

from app.services import provider_registry
from app.services.provider_registry import (
    GENERIC_PROVIDER,
    detect_provider,
    registered_providers,
)
from addons.media_import.provider_registration import (
    register_media_import_providers,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    provider_registry._reset_for_tests()
    yield
    provider_registry._reset_for_tests()


def test_registers_youtube_vimeo_and_soundcloud():
    register_media_import_providers()
    names = registered_providers()
    assert "youtube" in names
    assert "vimeo" in names
    assert "soundcloud" in names


def test_youtube_url_dispatch():
    register_media_import_providers()
    assert detect_provider("https://www.youtube.com/watch?v=abc") == "youtube"
    assert detect_provider("https://youtu.be/abc") == "youtube"


def test_vimeo_url_dispatch():
    register_media_import_providers()
    assert detect_provider("https://vimeo.com/12345") == "vimeo"
    assert detect_provider("https://player.vimeo.com/video/12345") == "vimeo"


def test_soundcloud_url_dispatch():
    register_media_import_providers()
    assert detect_provider("https://soundcloud.com/x/y") == "soundcloud"


def test_unrelated_url_falls_through_to_generic():
    register_media_import_providers()
    assert detect_provider("https://example.com/x") == GENERIC_PROVIDER


def test_idempotent_re_registration():
    register_media_import_providers()
    register_media_import_providers()
    names = registered_providers()
    assert names.count("youtube") == 1
    assert names.count("vimeo") == 1
    assert names.count("soundcloud") == 1
