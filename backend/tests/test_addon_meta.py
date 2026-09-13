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
    }

