"""The slots Media Import declares to the core registry."""
from __future__ import annotations


def test_declares_exactly_these_slot_entries() -> None:
    from addons.media_import.router import ADDON_META

    declared = {
        slot: sorted(entry["id"] for entry in entries)
        for slot, entries in ADDON_META["slots"].items()
    }
    assert declared == {
        "loft-metadata": ["loft-metadata"],
        "folder-actions-menu": ["media-import-url"],
        "file-actions-menu": ["loft-refresh"],
        "video-discover-shelves": ["media-import-feed", "media-import-regular"],
    }


def test_video_discover_shelves_entries_spec_addon_091_i8() -> None:
    """SPEC-ADDON-088, revised by SPEC-ADDON-091 I8: the two lanes as video app
    Discover shelves, with no ``href``, so See all opens the core shelf page."""
    from addons.media_import.router import ADDON_META

    entries = {e["id"]: e for e in ADDON_META["slots"]["video-discover-shelves"]}
    assert {eid: (e["priority"], e.get("i18n_key")) for eid, e in entries.items()} == {
        "media-import-regular": (10, "mediaImport.slots.videoRegular"),
        "media-import-feed": (20, "mediaImport.slots.videoFeed"),
    }
    assert [eid for eid, e in entries.items() if "href" in e] == []
