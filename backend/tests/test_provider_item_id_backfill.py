"""Tests for ``_backfill_provider_item_ids`` (Phase 2 migration helper).

Pre-Phase-2 ``loft_metadata`` rows have ``provider_item_id IS NULL``.
After the registry is populated and migration runs, video URLs should
be backfilled; non-video URLs (channel / playlist / unknown providers)
must remain NULL.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text


def _seed_file(session, file_id: str) -> None:
    from app.models import File

    db = session()
    try:
        db.add(
            File(
                id=file_id,
                filename=f"{file_id}.loft",
                title=file_id,
                drive="d",
                folder_path="",
                file_path=f"{file_id}.loft",
                file_size=1,
                file_type="video",
                mime_type="application/vnd.litloft.loft+json",
            )
        )
        db.commit()
    finally:
        db.close()


def _seed_loft(session, file_id: str, provider: str, url: str) -> None:
    db = session()
    try:
        db.execute(
            text(
                "INSERT INTO loft_metadata (file_id, provider, url) "
                "VALUES (:fid, :p, :u)"
            ),
            {"fid": file_id, "p": provider, "u": url},
        )
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _registry_with_youtube():
    """Each test gets the production set of subscription providers."""
    from addons.media_import.subscription.registration import (
        register_subscription_providers,
    )
    from addons.media_import.subscription.registry import (
        _reset_subscription_registry_for_tests,
    )

    _reset_subscription_registry_for_tests()
    register_subscription_providers()
    yield
    _reset_subscription_registry_for_tests()


class TestBackfill:
    def test_video_url_gets_id_filled(self, media_import_db) -> None:
        _seed_file(media_import_db, "f1")
        _seed_loft(
            media_import_db,
            "f1",
            "youtube",
            "https://youtu.be/dQw4w9WgXcQ",
        )

        from addons.media_import.service import _backfill_provider_item_ids

        updated = _backfill_provider_item_ids()
        assert updated == 1

        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT provider_item_id FROM loft_metadata "
                    "WHERE file_id = 'f1'"
                )
            ).first()
        finally:
            db.close()
        assert row[0] == "dQw4w9WgXcQ"

    def test_long_form_video_url(self, media_import_db) -> None:
        _seed_file(media_import_db, "f2")
        _seed_loft(
            media_import_db,
            "f2",
            "youtube",
            "https://www.youtube.com/watch?v=abcDEF12345",
        )

        from addons.media_import.service import _backfill_provider_item_ids

        _backfill_provider_item_ids()

        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT provider_item_id FROM loft_metadata "
                    "WHERE file_id = 'f2'"
                )
            ).first()
        finally:
            db.close()
        assert row[0] == "abcDEF12345"

    def test_unknown_provider_url_stays_null(self, media_import_db) -> None:
        _seed_file(media_import_db, "f3")
        _seed_loft(
            media_import_db,
            "f3",
            "soundcloud",
            "https://soundcloud.com/artist/track",
        )

        from addons.media_import.service import _backfill_provider_item_ids

        _backfill_provider_item_ids()

        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT provider_item_id FROM loft_metadata "
                    "WHERE file_id = 'f3'"
                )
            ).first()
        finally:
            db.close()
        assert row[0] is None

    def test_already_filled_rows_are_skipped(self, media_import_db) -> None:
        """Idempotent: running twice does not double-write or change values."""
        _seed_file(media_import_db, "f4")
        # Pre-fill with a different value than the URL would resolve to.
        # If the backfill were not skip-on-NULL, it would clobber this.
        db = media_import_db()
        try:
            db.execute(
                text(
                    "INSERT INTO loft_metadata "
                    "(file_id, provider, provider_item_id, url) "
                    "VALUES (:fid, :p, :iid, :u)"
                ),
                {
                    "fid": "f4",
                    "p": "youtube",
                    "iid": "preexisting_id",
                    "u": "https://youtu.be/dQw4w9WgXcQ",
                },
            )
            db.commit()
        finally:
            db.close()

        from addons.media_import.service import _backfill_provider_item_ids

        updated = _backfill_provider_item_ids()
        assert updated == 0

        db = media_import_db()
        try:
            row = db.execute(
                text(
                    "SELECT provider_item_id FROM loft_metadata "
                    "WHERE file_id = 'f4'"
                )
            ).first()
        finally:
            db.close()
        assert row[0] == "preexisting_id"

    def test_mixed_batch(self, media_import_db) -> None:
        """Run across rows that should + should not be backfilled."""
        for fid in ("v1", "v2", "u1"):
            _seed_file(media_import_db, fid)
        _seed_loft(
            media_import_db,
            "v1",
            "youtube",
            "https://youtu.be/aaaaaaaaaaa",
        )
        _seed_loft(
            media_import_db,
            "v2",
            "youtube",
            "https://www.youtube.com/watch?v=bbbbbbbbbbb",
        )
        _seed_loft(
            media_import_db,
            "u1",
            "soundcloud",
            "https://soundcloud.com/artist/track",
        )

        from addons.media_import.service import _backfill_provider_item_ids

        updated = _backfill_provider_item_ids()
        assert updated == 2

        db = media_import_db()
        try:
            results = {
                row[0]: row[1]
                for row in db.execute(
                    text(
                        "SELECT file_id, provider_item_id FROM loft_metadata"
                    )
                ).fetchall()
            }
        finally:
            db.close()
        assert results == {
            "v1": "aaaaaaaaaaa",
            "v2": "bbbbbbbbbbb",
            "u1": None,
        }

    def test_empty_table_no_op(self, media_import_db) -> None:
        from addons.media_import.service import _backfill_provider_item_ids

        assert _backfill_provider_item_ids() == 0
