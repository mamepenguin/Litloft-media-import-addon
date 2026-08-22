"""Core-owned update notifications go through event_hooks, not the broadcaster.

Core derives the browser event (`drive.file_updated`) inside
`event_hooks.emit*`. An addon that calls `broadcast_from_thread` directly
reaches the raw socket but never produces that derived event, so the file
list does not refresh — which is what happened before this migration.

Addon-owned events (`media_import.subscription.*`) are the opposite case
and still broadcast directly; that is checked here too so the distinction
does not quietly erode.
"""

import inspect

import addons.media_import.service as service
import addons.media_import.subscription.manager as manager_mod
import addons.media_import.subscription.worker as worker_mod


def _source(module) -> str:
    return inspect.getsource(module)


class TestCoreOwnedEventsUseEventHooks:
    def test_service_does_not_broadcast_directly(self):
        src = _source(service)
        assert "broadcast_from_thread(" not in src, (
            "service.py must notify through event_hooks so core can derive "
            "the browser event"
        )

    def test_manager_does_not_broadcast_directly(self):
        src = _source(manager_mod)
        assert "broadcast_from_thread(" not in src

    def test_files_updated_is_emitted_with_ids_and_drive(self):
        for module in (service, manager_mod):
            src = _source(module)
            if '"files.updated"' not in src:
                continue
            # The core webhook contract is {"file_ids": [...]}, and passing
            # the drive explicitly saves core a lookup we already did.
            assert "file_ids" in src
            assert "drives=" in src

    def test_no_module_still_imports_the_broadcaster_for_core_events(self):
        for module in (service, manager_mod):
            assert "broadcast_from_thread" not in _source(module), (
                f"{module.__name__} should no longer import the broadcaster"
            )


class TestAddonOwnedEventsStillBroadcast:
    def test_worker_broadcasts_its_own_namespace_directly(self):
        src = _source(worker_mod)
        # Addon-owned events are not core lifecycle names, so core has
        # nothing to derive from them; a direct broadcast is correct.
        assert "broadcast_from_thread(" in src
        assert "media_import.subscription." in src
