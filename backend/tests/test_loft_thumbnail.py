"""Tests for ``_save_loft_thumbnail`` — shared helper that writes a
.loft's remote thumbnail to ``data/thumbnails/`` and updates
``File.thumbnail_path``.

Both ``LoftManager._fetch_and_update`` (the /link pipeline) and
``SubscriptionManager._import_one_item`` (the subscription pipeline)
go through this helper so the two paths produce identical thumbnail
state per file_id (see hako ``IpF19kUI3OKoY_ps7iKg1`` / ``rSexxNohzBFCSwvD7oQPI``).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text


def _seed_file(db_session, *, file_id: str, drive: str, folder_path: str,
               filename: str) -> None:
    """Insert a minimal File row so the helper can update its thumbnail_path."""
    db = db_session()
    try:
        rel_path = (
            f"{folder_path}/{filename}" if folder_path else filename
        )
        db.execute(
            text(
                "INSERT INTO files (id, filename, title, description, drive, "
                " folder_path, file_path, file_size, file_type, mime_type, "
                " created_at, updated_at) "
                "VALUES (:id, :filename, :title, '', :drive, "
                " :folder_path, :file_path, 0, 'other', "
                " 'application/vnd.litloft.loft+json', "
                " '2026-05-01T00:00:00', '2026-05-01T00:00:00')"
            ),
            {
                "id": file_id,
                "filename": filename,
                "title": filename,
                "drive": drive,
                "folder_path": folder_path,
                "file_path": rel_path,
            },
        )
        db.commit()
    finally:
        db.close()


def _read_thumbnail_path(db_session, file_id: str) -> str | None:
    db = db_session()
    try:
        row = db.execute(
            text("SELECT thumbnail_path FROM files WHERE id = :id"),
            {"id": file_id},
        ).first()
    finally:
        db.close()
    return row[0] if row else None


class TestSaveLoftThumbnailSuccess:
    """SPEC-ADDON-040: the picture lands at ``.by-id/<file id>.jpg``."""

    def test_writes_thumbnail_path_with_folder(
        self, media_import_db, drive_path, monkeypatch,
        thumbnails_dir,
    ) -> None:
        from addons.media_import import service

        _seed_file(
            media_import_db,
            file_id="f1", drive="d", folder_path="yt",
            filename="Sample.loft",
        )

        captured: dict = {}

        def _fake_download(url: str, dest: Path) -> bool:
            captured["url"] = url
            captured["dest"] = dest
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake jpg")
            return True

        monkeypatch.setattr(service, "_download_thumbnail_sync", _fake_download)

        rel = service._save_loft_thumbnail(
            file_id="f1",
            drive="d",
            folder_path="yt",
            filename="Sample.loft",
            thumbnail_url="https://i.ytimg.com/vi/abc/hqdefault.jpg",
        )

        assert rel == ".by-id/f1.jpg"
        assert _read_thumbnail_path(media_import_db, "f1") == ".by-id/f1.jpg"
        assert captured["dest"] == thumbnails_dir / ".by-id/f1.jpg"
        assert captured["url"] == "https://i.ytimg.com/vi/abc/hqdefault.jpg"

    def test_writes_thumbnail_path_without_folder(
        self, media_import_db, drive_path, monkeypatch
    ) -> None:
        from addons.media_import import service

        _seed_file(
            media_import_db,
            file_id="f2", drive="d", folder_path="",
            filename="Top.loft",
        )

        monkeypatch.setattr(
            service, "_download_thumbnail_sync",
            lambda url, dest: True,
        )

        rel = service._save_loft_thumbnail(
            file_id="f2",
            drive="d",
            folder_path="",
            filename="Top.loft",
            thumbnail_url="https://example/x.jpg",
        )

        assert rel == ".by-id/f2.jpg"
        assert _read_thumbnail_path(media_import_db, "f2") == ".by-id/f2.jpg"


class TestSaveLoftThumbnailNoOp:
    def test_returns_none_when_thumbnail_url_is_none(
        self, media_import_db, drive_path, monkeypatch
    ) -> None:
        from addons.media_import import service

        _seed_file(
            media_import_db, file_id="f3", drive="d",
            folder_path="", filename="X.loft",
        )

        called: list = []
        monkeypatch.setattr(
            service, "_download_thumbnail_sync",
            lambda *a, **kw: called.append(True) or True,
        )

        rel = service._save_loft_thumbnail(
            file_id="f3", drive="d", folder_path="",
            filename="X.loft", thumbnail_url=None,
        )

        assert rel is None
        assert called == []  # no download attempted
        assert _read_thumbnail_path(media_import_db, "f3") is None

    def test_returns_none_when_thumbnail_url_is_empty(
        self, media_import_db, drive_path, monkeypatch
    ) -> None:
        from addons.media_import import service

        _seed_file(
            media_import_db, file_id="f4", drive="d",
            folder_path="", filename="Y.loft",
        )
        monkeypatch.setattr(
            service, "_download_thumbnail_sync",
            lambda *a, **kw: True,
        )

        rel = service._save_loft_thumbnail(
            file_id="f4", drive="d", folder_path="",
            filename="Y.loft", thumbnail_url="",
        )

        assert rel is None
        assert _read_thumbnail_path(media_import_db, "f4") is None


class TestSaveLoftThumbnailFailures:
    def test_download_failure_leaves_thumbnail_path_unchanged(
        self, media_import_db, drive_path, monkeypatch
    ) -> None:
        """SPEC-ADDON-040: a failed download keeps the old behaviour."""
        from addons.media_import import service

        _seed_file(
            media_import_db, file_id="f5", drive="d",
            folder_path="yt", filename="Z.loft",
        )
        monkeypatch.setattr(
            service, "_download_thumbnail_sync",
            lambda url, dest: False,
        )

        rel = service._save_loft_thumbnail(
            file_id="f5", drive="d", folder_path="yt",
            filename="Z.loft", thumbnail_url="https://example/z.jpg",
        )

        assert rel is None
        assert _read_thumbnail_path(media_import_db, "f5") is None

    def test_missing_file_id_silently_skipped(
        self, media_import_db, drive_path, monkeypatch
    ) -> None:
        from addons.media_import import service

        # Don't seed any file row. Pretend download succeeded so we
        # exercise the file_record None branch.
        monkeypatch.setattr(
            service, "_download_thumbnail_sync",
            lambda url, dest: True,
        )

        rel = service._save_loft_thumbnail(
            file_id="ghost", drive="d", folder_path="yt",
            filename="Ghost.loft",
            thumbnail_url="https://example/ghost.jpg",
        )

        assert rel is None  # file_record lookup returns None


class TestSaveLoftThumbnailReplacesPathForm:
    def test_path_form_pointer_is_replaced_and_old_file_removed(
        self, media_import_db, drive_path, monkeypatch,
        thumbnails_dir,
    ) -> None:
        """SPEC-ADDON-040: a row still pointing at a stem-form slot moves
        to its own slot, and the old picture goes once the pointer commits."""
        from addons.media_import import service

        _seed_file(
            media_import_db, file_id="f7", drive="d",
            folder_path="yt", filename="Old.loft",
        )
        old_rel = "d/yt/Old.jpg"
        old_full = thumbnails_dir / old_rel
        old_full.parent.mkdir(parents=True, exist_ok=True)
        old_full.write_bytes(b"old")
        db = media_import_db()
        try:
            db.execute(
                text("UPDATE files SET thumbnail_path = :p WHERE id = 'f7'"),
                {"p": old_rel},
            )
            db.commit()
        finally:
            db.close()

        def _fake_download(url: str, dest: Path) -> bool:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"new")
            return True

        monkeypatch.setattr(service, "_download_thumbnail_sync", _fake_download)

        rel = service._save_loft_thumbnail(
            file_id="f7", drive="d", folder_path="yt",
            filename="Old.loft", thumbnail_url="https://example/o.jpg",
        )

        assert rel == ".by-id/f7.jpg"
        assert _read_thumbnail_path(media_import_db, "f7") == ".by-id/f7.jpg"
        assert (thumbnails_dir / ".by-id/f7.jpg").read_bytes() == b"new"
        assert not old_full.exists()

    def test_by_id_pointer_is_rewritten_in_place_and_kept(
        self, media_import_db, drive_path, monkeypatch,
        thumbnails_dir,
    ) -> None:
        """SPEC-ADDON-040: re-fetching must not delete the slot it just wrote."""
        from addons.media_import import service

        _seed_file(
            media_import_db, file_id="f8", drive="d",
            folder_path="", filename="Again.loft",
        )
        db = media_import_db()
        try:
            db.execute(
                text(
                    "UPDATE files SET thumbnail_path = '.by-id/f8.jpg' "
                    "WHERE id = 'f8'"
                )
            )
            db.commit()
        finally:
            db.close()

        def _fake_download(url: str, dest: Path) -> bool:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"again")
            return True

        monkeypatch.setattr(service, "_download_thumbnail_sync", _fake_download)

        service._save_loft_thumbnail(
            file_id="f8", drive="d", folder_path="",
            filename="Again.loft", thumbnail_url="https://example/a.jpg",
        )

        assert (thumbnails_dir / ".by-id/f8.jpg").read_bytes() == b"again"


class TestSaveLoftThumbnailNFDFilename:
    def test_nfd_filename_still_yields_the_by_id_slot(
        self, media_import_db, drive_path, monkeypatch,
        thumbnails_dir,
    ) -> None:
        """SPEC-ADDON-040: a decomposed (macOS) filename has no bearing on
        the slot; the picture lands at ``.by-id/<file id>.jpg``.
        """
        from addons.media_import import service

        nfd_filename = "がmeta.loft"  # "か" + combining dakuten
        _seed_file(
            media_import_db, file_id="f6", drive="d",
            folder_path="", filename=nfd_filename,
        )

        captured: dict = {}

        def _fake_download(url: str, dest: Path) -> bool:
            captured["dest"] = dest
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake jpg")
            return True

        monkeypatch.setattr(service, "_download_thumbnail_sync", _fake_download)

        rel = service._save_loft_thumbnail(
            file_id="f6", drive="d", folder_path="",
            filename=nfd_filename, thumbnail_url="https://example/g.jpg",
        )

        assert rel == ".by-id/f6.jpg"
        assert _read_thumbnail_path(media_import_db, "f6") == ".by-id/f6.jpg"
        assert captured["dest"] == thumbnails_dir / ".by-id/f6.jpg"
        assert "meta" not in str(captured["dest"])


def _set_pointer(db_session, file_id: str, pointer: str) -> None:
    db = db_session()
    try:
        db.execute(
            text("UPDATE files SET thumbnail_path = :p WHERE id = :id"),
            {"p": pointer, "id": file_id},
        )
        db.commit()
    finally:
        db.close()


class TestSaveLoftThumbnailKeepsWhatItHas:
    def test_failed_download_keeps_pointer_and_path_form_file(
        self, media_import_db, drive_path, monkeypatch, thumbnails_dir
    ) -> None:
        """SPEC-ADDON-040: nothing is removed before a download succeeds."""
        from addons.media_import import service

        _seed_file(
            media_import_db, file_id="k1", drive="d",
            folder_path="yt", filename="Keep.loft",
        )
        _set_pointer(media_import_db, "k1", "d/yt/Keep.jpg")
        old_full = thumbnails_dir / "d/yt/Keep.jpg"
        old_full.parent.mkdir(parents=True)
        old_full.write_bytes(b"old")
        monkeypatch.setattr(
            service, "_download_thumbnail_sync", lambda url, dest: False
        )

        rel = service._save_loft_thumbnail(
            file_id="k1", drive="d", folder_path="yt",
            filename="Keep.loft", thumbnail_url="https://example/k.jpg",
        )

        assert rel is None
        assert _read_thumbnail_path(media_import_db, "k1") == "d/yt/Keep.jpg"
        assert old_full.read_bytes() == b"old"

    def test_removal_failure_leaves_new_pointer_committed(
        self, media_import_db, drive_path, monkeypatch, thumbnails_dir
    ) -> None:
        """SPEC-ADDON-040: an OSError removing the old picture is not an error."""
        from addons.media_import import service

        _seed_file(
            media_import_db, file_id="k2", drive="d",
            folder_path="yt", filename="Stuck.loft",
        )
        _set_pointer(media_import_db, "k2", "d/yt/Stuck.jpg")
        old_full = thumbnails_dir / "d/yt/Stuck.jpg"
        old_full.parent.mkdir(parents=True)
        old_full.write_bytes(b"old")

        def _download(url: str, dest: Path) -> bool:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"new")
            return True

        real_unlink = Path.unlink

        def _unlink(self, *a, **kw):
            if self == old_full:
                raise PermissionError("denied")
            return real_unlink(self, *a, **kw)

        monkeypatch.setattr(service, "_download_thumbnail_sync", _download)
        monkeypatch.setattr(Path, "unlink", _unlink)

        rel = service._save_loft_thumbnail(
            file_id="k2", drive="d", folder_path="yt",
            filename="Stuck.loft", thumbnail_url="https://example/s.jpg",
        )

        assert rel == ".by-id/k2.jpg"
        assert _read_thumbnail_path(media_import_db, "k2") == ".by-id/k2.jpg"

    def test_removal_prunes_the_emptied_old_directory(
        self, media_import_db, drive_path, monkeypatch, thumbnails_dir
    ) -> None:
        """SPEC-ADDON-040: the old picture's emptied folders go with it."""
        from addons.media_import import service

        _seed_file(
            media_import_db, file_id="k3", drive="d",
            folder_path="yt/deep", filename="Gone.loft",
        )
        _set_pointer(media_import_db, "k3", "d/yt/deep/Gone.jpg")
        old_full = thumbnails_dir / "d/yt/deep/Gone.jpg"
        old_full.parent.mkdir(parents=True)
        old_full.write_bytes(b"old")

        def _download(url: str, dest: Path) -> bool:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"new")
            return True

        monkeypatch.setattr(service, "_download_thumbnail_sync", _download)

        service._save_loft_thumbnail(
            file_id="k3", drive="d", folder_path="yt/deep",
            filename="Gone.loft", thumbnail_url="https://example/g.jpg",
        )

        assert not (thumbnails_dir / "d").exists()
        assert (thumbnails_dir / ".by-id/k3.jpg").exists()


class _DroppingResponse:
    """A response that yields some bytes, then loses the connection."""

    def __init__(self) -> None:
        self._sent = False

    def read(self, size: int = -1) -> bytes:
        if not self._sent:
            self._sent = True
            return b"partial"
        raise ConnectionResetError("dropped")

    def info(self):
        from email.message import Message

        return Message()

    def close(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


class TestDownloadThumbnailAtomic:
    def test_dropped_connection_leaves_existing_slot_and_no_partial(
        self, media_import_db, drive_path, monkeypatch, thumbnails_dir
    ) -> None:
        """SPEC-ADDON-040: a slot is replaced whole or not at all."""
        from addons.media_import import service

        _seed_file(
            media_import_db, file_id="a1", drive="d",
            folder_path="", filename="Atomic.loft",
        )
        _set_pointer(media_import_db, "a1", ".by-id/a1.jpg")
        slot = thumbnails_dir / ".by-id/a1.jpg"
        slot.parent.mkdir(parents=True)
        slot.write_bytes(b"good")
        monkeypatch.setattr(
            service.urllib.request, "urlopen",
            lambda *a, **kw: _DroppingResponse(),
        )

        rel = service._save_loft_thumbnail(
            file_id="a1", drive="d", folder_path="",
            filename="Atomic.loft", thumbnail_url="https://example/a.jpg",
        )

        assert rel is None
        assert slot.read_bytes() == b"good"
        assert [p.name for p in slot.parent.iterdir()] == ["a1.jpg"]
        assert _read_thumbnail_path(media_import_db, "a1") == ".by-id/a1.jpg"


@pytest.fixture()
def picture_server():
    """A local HTTP server: /good serves a body, /short promises more than it sends."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    good = b"\xff\xd8good-jpeg-bytes\xff\xd9"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = good if self.path == "/good" else b"cut"
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header(
                "Content-Length",
                str(len(good) if self.path == "/good" else 1000),
            )
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            return None

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base, good
    finally:
        server.shutdown()
        server.server_close()


class TestDownloadThumbnailOverHttp:
    def test_good_download_publishes_the_bytes_and_leaves_no_temp_file(
        self, media_import_db, drive_path, thumbnails_dir, picture_server
    ) -> None:
        """SPEC-ADDON-040: the real download body fills the slot."""
        from addons.media_import import service

        base, good = picture_server
        _seed_file(
            media_import_db, file_id="h1", drive="d",
            folder_path="", filename="Http.loft",
        )

        rel = service._save_loft_thumbnail(
            file_id="h1", drive="d", folder_path="",
            filename="Http.loft", thumbnail_url=f"{base}/good",
        )

        slot = thumbnails_dir / ".by-id/h1.jpg"
        assert rel == ".by-id/h1.jpg"
        assert slot.read_bytes() == good
        assert [p.name for p in slot.parent.iterdir()] == ["h1.jpg"]

    def test_truncated_body_is_a_failure_that_keeps_the_slot(
        self, media_import_db, drive_path, thumbnails_dir, picture_server
    ) -> None:
        """SPEC-ADDON-040: a body shorter than its Content-Length is not
        published; the existing slot and pointer stay."""
        from addons.media_import import service

        base, _ = picture_server
        _seed_file(
            media_import_db, file_id="h2", drive="d",
            folder_path="", filename="Cut.loft",
        )
        _set_pointer(media_import_db, "h2", ".by-id/h2.jpg")
        slot = thumbnails_dir / ".by-id/h2.jpg"
        slot.parent.mkdir(parents=True)
        slot.write_bytes(b"good")

        rel = service._save_loft_thumbnail(
            file_id="h2", drive="d", folder_path="",
            filename="Cut.loft", thumbnail_url=f"{base}/short",
        )

        assert rel is None
        assert slot.read_bytes() == b"good"
        assert [p.name for p in slot.parent.iterdir()] == ["h2.jpg"]
        assert _read_thumbnail_path(media_import_db, "h2") == ".by-id/h2.jpg"

    def test_truncated_candidate_moves_on_to_the_next_url(
        self, media_import_db, drive_path, thumbnails_dir, picture_server
    ) -> None:
        """SPEC-ADDON-040: a truncated first candidate falls through."""
        from addons.media_import import service

        base, good = picture_server
        _seed_file(
            media_import_db, file_id="h3", drive="d",
            folder_path="", filename="Next.loft",
        )

        rel = service._save_loft_thumbnail(
            file_id="h3", drive="d", folder_path="",
            filename="Next.loft", thumbnail_url=f"{base}/short",
            fallback_urls=[f"{base}/good"],
        )

        assert rel == ".by-id/h3.jpg"
        assert (thumbnails_dir / ".by-id/h3.jpg").read_bytes() == good


class TestSaveLoftThumbnailCommitOrder:
    def test_failed_commit_leaves_the_old_picture_on_disk(
        self, media_import_db, drive_path, monkeypatch, thumbnails_dir
    ) -> None:
        """SPEC-ADDON-040: the old picture goes only after the commit."""
        from addons.media_import import service
        from sqlalchemy.orm import Session

        _seed_file(
            media_import_db, file_id="c1", drive="d",
            folder_path="yt", filename="Order.loft",
        )
        _set_pointer(media_import_db, "c1", "d/yt/Order.jpg")
        old_full = thumbnails_dir / "d/yt/Order.jpg"
        old_full.parent.mkdir(parents=True)
        old_full.write_bytes(b"old")

        def _download(url: str, dest: Path) -> bool:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"new")
            return True

        def _boom(self) -> None:
            raise RuntimeError("commit failed")

        monkeypatch.setattr(service, "_download_thumbnail_sync", _download)
        monkeypatch.setattr(Session, "commit", _boom)

        with pytest.raises(RuntimeError):
            service._save_loft_thumbnail(
                file_id="c1", drive="d", folder_path="yt",
                filename="Order.loft", thumbnail_url="https://example/o.jpg",
            )

        assert old_full.read_bytes() == b"old"
