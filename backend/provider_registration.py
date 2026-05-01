"""Register the .loft providers Media Import ships with.

Phase 1 ships YouTube + Vimeo with corresponding frontend players, and
SoundCloud as a name-only entry so legacy `.loft` files written before
Phase 0 keep their `provider` field stable. The frontend has no
SoundCloud player registered; LoftPlayer falls through to
GenericLinkCard for it.

Called from the addon's ``on_startup`` lifespan hook. Idempotent: the
core ``register_provider`` overwrites prior entries with the same name
(last writer wins), so re-registration is safe across reloads.
"""
from __future__ import annotations

import re

from app.services.provider_registry import register_provider


def register_media_import_providers() -> None:
    """Register YouTube / Vimeo / SoundCloud URL → name dispatch."""
    register_provider("youtube", re.compile(r"(?:youtube\.com|youtu\.be)"))
    register_provider("vimeo", re.compile(r"vimeo\.com"))
    # SoundCloud has no embedded player in Phase 1; the registration only
    # ensures `.loft` files keep `provider="soundcloud"` rather than
    # silently degrading to "generic".
    register_provider("soundcloud", re.compile(r"soundcloud\.com"))
