"""SPEC-ADDON-002: Media Import names files in NFC and finds its sidecars by NFC-literal name."""
from __future__ import annotations

import os
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from addons.media_import.subscription.registry import (
    REF_KIND_CHANNEL,
    ItemHeader,
    ItemMetadata,
    SubscriptionRef,
    TranscriptResult,
    _reset_subscription_registry_for_tests,
    register_subscription_provider,
)
from addons.media_import.tests.test_subscription_manager import _FakeProvider


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def nfd(s: str) -> str:
    return unicodedata.normalize("NFD", s)


CAFE = "Café ガイド"
CAFE_MIXED = "Café ガイド"
VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n"

# q has no precomposed form with an acute, so NFC leaves the marks in place and
# the cut at 200 falls between them.
UNCOMPOSABLE_CUT = "a" * 198 + "q́́" + "tail"

SANITIZE = [
    pytest.param(nfd(CAFE), nfc(CAFE), id="nfd"),
    pytest.param(CAFE_MIXED, nfc(CAFE), id="mixed"),
    pytest.param(nfd("ガ" * 150), "ガ" * 150, id="nfd-longer-than-cut-nfc-shorter"),
    pytest.param(nfd("é" * 250), "é" * 200, id="nfd-truncated-after-nfc"),
    pytest.param(UNCOMPOSABLE_CUT, "a" * 198 + "q", id="no-orphan-combining-mark"),
    pytest.param("a" * 197 + "q́́" + "tail", "a" * 197 + "q", id="every-orphaned-mark-dropped"),
    pytest.param("ไม่", "ไม่", id="uncut-title-keeps-its-final-mark"),
]


@pytest.mark.parametrize("title, expected", SANITIZE)
def test_spec_addon_002_both_sanitizers_return_the_same_nfc_name(title, expected):
    from addons.media_import.service import _sanitize_filename as link_sanitize
    from addons.media_import.subscription.manager import (
        _sanitize_filename as subscription_sanitize,
    )

    link, subscription = link_sanitize(title), subscription_sanitize(title)

    assert link == expected
    assert subscription == expected
    assert len(link) <= 200
    if len(nfc(title)) > 200:
        assert unicodedata.combining(link[-1]) == 0


@pytest.fixture()
def _providers():
    from app.services import provider_registry
    from addons.media_import.provider_registration import (
        register_media_import_providers,
    )

    provider_registry._reset_for_tests()
    register_media_import_providers()
    yield
    provider_registry._reset_for_tests()


def test_spec_addon_002_link_import_writes_nfc_loft_name(
    media_import_db, drive_path, _providers
):
    from addons.media_import.service import LoftManager

    with patch(
        "addons.media_import.service._extract_title_sync",
        side_effect=lambda _url: nfd(CAFE),
    ):
        _, filename = LoftManager().create_loft_sync(
            "https://www.youtube.com/watch?v=abc", "drv", ""
        )

    assert filename == nfc(CAFE) + ".loft"
    assert os.listdir(drive_path) == [nfc(CAFE) + ".loft"]


@pytest.fixture()
def fake_provider():
    def _resolve(url: str) -> SubscriptionRef | None:
        if "fake/channel/" in url:
            return SubscriptionRef(kind=REF_KIND_CHANNEL, ref=url.rsplit("/", 1)[-1])
        return None

    _reset_subscription_registry_for_tests()
    p = _FakeProvider(resolve_url=_resolve)
    register_subscription_provider(p)
    yield p
    _reset_subscription_registry_for_tests()


def test_spec_addon_002_subscription_writes_loft_and_vtt_in_one_nfc_stem(
    media_import_db, drive_path, fake_provider
):
    from addons.media_import.subscription.manager import SubscriptionManager

    fake_provider.headers = [ItemHeader(item_id="v1", title=nfd(CAFE))]
    fake_provider.items = {
        "v1": ItemMetadata(
            item_id="v1",
            canonical_url="https://fake/v/v1",
            title=nfd(CAFE),
            has_captions=True,
            language="ja",
        ),
    }
    fake_provider.transcripts = {"v1": TranscriptResult(vtt_text=VTT, language="ja")}

    mgr = SubscriptionManager()
    sub_id = mgr.create(
        url="https://fake/channel/UCabcdefghijklmnopqrstuv", drive="d", folder_path="yt"
    )
    mgr._sync_blocking(sub_id)

    names = sorted(n for n in os.listdir(drive_path / "yt") if n.endswith((".loft", ".vtt")))
    assert names == [nfc(CAFE) + ".loft", nfc(CAFE) + ".vtt"]


class _FakeYDL:
    def __init__(self, *_a, **_kw): ...
    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def download(self, _urls): return 0


@pytest.mark.parametrize("stem, lang_file", [
    pytest.param(nfc(CAFE), nfd(CAFE) + ".en.vtt", id="nfd-lang-file"),
    pytest.param("Live [2026] *best?", "Live [2026] *best?.en.vtt", id="glob-metacharacters"),
    pytest.param(nfc("Été [ライブ]"), nfd("Été [ライブ]") + ".ja.vtt", id="metacharacters-and-nfd"),
])
def test_spec_addon_002_caption_download_leaves_stem_vtt(tmp_path, stem, lang_file):
    from addons.media_import.service import _download_captions_sync

    (tmp_path / lang_file).write_text(VTT, encoding="utf-8")
    (tmp_path / (stem + ".loft")).write_text("{}", encoding="utf-8")

    with patch("yt_dlp.YoutubeDL", _FakeYDL):
        result = _download_captions_sync(
            "https://example.com/v", tmp_path / stem, language="en"
        )

    assert result == (True, None)
    assert stem + ".vtt" in os.listdir(tmp_path)
    assert "hello" in (tmp_path / (stem + ".vtt")).read_text(encoding="utf-8")
    assert (tmp_path / (stem + ".loft")).read_text(encoding="utf-8") == "{}"


def test_spec_addon_002_caption_download_replaces_an_older_stem_vtt(tmp_path):
    from addons.media_import.service import _download_captions_sync

    (tmp_path / "Clip.vtt").write_text("WEBVTT\n\nold\n", encoding="utf-8")
    (tmp_path / "Clip.zh.vtt").write_text(VTT, encoding="utf-8")

    with patch("yt_dlp.YoutubeDL", _FakeYDL):
        result = _download_captions_sync("https://example.com/v", tmp_path / "Clip", language="zh")

    assert result == (True, None)
    assert sorted(os.listdir(tmp_path)) == ["Clip.vtt"]
    assert "hello" in (tmp_path / "Clip.vtt").read_text(encoding="utf-8")


def test_spec_addon_002_sidecar_lookups_in_a_missing_folder_find_nothing(tmp_path):
    from addons.media_import.service import _cleanup_stt_temp, _download_captions_sync

    stem = tmp_path / "absent" / "Clip"
    _cleanup_stt_temp(stem)
    with patch("yt_dlp.YoutubeDL", _FakeYDL):
        assert _download_captions_sync("https://example.com/v", stem) == (False, None)


class _WritingYDL(_FakeYDL):
    """Writes one file, as yt-dlp's audio download would, under the given name."""

    produced: Path

    def download(self, _urls):
        self.produced.write_bytes(b"audio")
        return 0


@pytest.mark.parametrize("stem, produced", [
    pytest.param(nfc(CAFE), nfd(CAFE) + ".stt_temp.webm.part", id="part-nfd"),
    pytest.param("Live [2026] *best?", "Live [2026] *best?.stt_temp.webm", id="no-part-metacharacters"),
])
def test_spec_addon_002_stt_audio_download_moves_the_produced_file_into_place(
    tmp_path, stem, produced
):
    from addons.media_import.service import _download_stt_audio_sync

    _WritingYDL.produced = tmp_path / produced
    (tmp_path / (stem + ".loft")).write_text("{}", encoding="utf-8")

    with patch("yt_dlp.YoutubeDL", _WritingYDL):
        final = _download_stt_audio_sync("https://example.com/v", tmp_path / stem)

    assert Path(final).name == stem + ".stt_temp.m4a"
    assert sorted(os.listdir(tmp_path)) == sorted([stem + ".loft", stem + ".stt_temp.m4a"])


@pytest.mark.parametrize("stem, on_disk_stem", [
    pytest.param(nfc(CAFE), nfd(CAFE), id="nfd-on-disk"),
    pytest.param("Live [2026] *best?", "Live [2026] *best?", id="glob-metacharacters"),
])
def test_spec_addon_002_stt_temp_cleanup_matches_nfc_literal_stem(tmp_path, stem, on_disk_stem):
    from addons.media_import.service import _cleanup_stt_temp

    temps = [on_disk_stem + ".stt_temp.m4a", on_disk_stem + ".stt_temp.webm.part"]
    kept = [on_disk_stem + ".loft", on_disk_stem + ".vtt"]
    for name in temps + kept:
        (tmp_path / name).write_bytes(b"x")

    _cleanup_stt_temp(tmp_path / stem)

    assert sorted(os.listdir(tmp_path)) == sorted(kept)


def test_spec_addon_002_stale_cleanup_matches_nfd_temp_and_keeps_young_ones(
    media_import_db, drive_path
):
    from app.models import File
    from addons.media_import.service import _cleanup_stale_stt_temp_files

    loft = drive_path / (nfc(CAFE) + ".loft")
    loft.write_text("{}", encoding="utf-8")
    vtt = drive_path / (nfc(CAFE) + ".vtt")
    vtt.write_text(VTT, encoding="utf-8")
    old = drive_path / (nfd(CAFE) + ".stt_temp.m4a")
    young = drive_path / (nfd(CAFE) + ".stt_temp.webm.part")
    old.write_bytes(b"audio")
    young.write_bytes(b"audio")
    long_ago = 946684800
    for aged in (old, loft, vtt):
        os.utime(aged, (long_ago, long_ago))
    an_hour_ago = time.time() - 3600
    os.utime(young, (an_hour_ago, an_hour_ago))

    db = media_import_db()
    try:
        db.add(File(
            id="fnfdstale001",
            filename=nfc(CAFE) + ".loft",
            title=nfc(CAFE),
            drive="drv",
            folder_path="",
            file_path=nfc(CAFE) + ".loft",
            file_size=2,
            file_type="other",
            mime_type="application/vnd.litloft.loft+json",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ))
        db.commit()
    finally:
        db.close()

    assert _cleanup_stale_stt_temp_files() == 1
    assert sorted(os.listdir(drive_path)) == sorted([loft.name, vtt.name, young.name])
