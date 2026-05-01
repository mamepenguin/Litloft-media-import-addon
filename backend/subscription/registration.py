"""Startup wiring for SubscriptionProvider implementations.

Mirrors ``provider_registration.py`` (which handles the URL→player
``provider_registry`` in core) — same explicit register-on-startup
pattern, different registry. Keeping the two side-by-side makes the
mental model consistent: providers are registered eagerly, idempotently,
and re-registration overwrites.
"""
from __future__ import annotations

from .providers.youtube import YouTubeProvider
from .registry import register_subscription_provider


def register_subscription_providers() -> None:
    """Register all subscription providers Media Import ships with.

    Phase 2: YouTube only. Adding podcast / Vimeo / etc later means
    appending a single ``register_subscription_provider(...)`` line here
    — no other code in the addon needs to change.
    """
    register_subscription_provider(YouTubeProvider())
