"""The navigation entry Media Import declares to the core sidebar."""
from __future__ import annotations

from app.services.addon_registry import _navigation_error



def test_declares_the_youtube_and_feeds_entry() -> None:
    from addons.media_import.router import ADDON_META

    assert ADDON_META["navigation"] == {
        "label": "YouTube & Feeds",
        "i18n_key": "mediaImport.sidebar.label",
        "icon": "rss",
        "placement": "sources",
        "priority": 10,
    }
    assert ADDON_META["href"] == "/addons/media_import"


def test_declaration_passes_the_core_validator() -> None:
    from addons.media_import.router import ADDON_META

    assert _navigation_error(ADDON_META["navigation"]) is None
