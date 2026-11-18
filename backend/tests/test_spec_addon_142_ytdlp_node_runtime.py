"""SPEC-ADDON-142: every ``yt_dlp.YoutubeDL`` media_import builds may use Node and only Node."""
from __future__ import annotations

import contextlib
import os
import shutil
from unittest.mock import patch

import pytest

NODE_ONLY = {"js_runtimes": {"node": {}}}
QUIET = {"quiet": True, "no_warnings": True}
FLAT = {**QUIET, "extract_flat": True, "skip_download": True}
URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
CHANNEL_URL = "https://www.youtube.com/@handle"


class _RecordingYDL:
    built: list[dict] = []

    def __init__(self, opts=None, *_a, **_kw):
        type(self).built.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def extract_info(self, _url, download=False, **_kw):
        return {
            "id": "UCabcdefghijklmnopqrstuv",
            "channel_id": "UCabcdefghijklmnopqrstuv",
            "title": "T",
            "entries": [],
        }

    def download(self, _urls):
        return 0


def _title(_tmp):
    from addons.media_import.service import _extract_title_sync

    _extract_title_sync(URL)


def _metadata(_tmp):
    from addons.media_import.service import _fetch_metadata_sync

    _fetch_metadata_sync(URL)


def _captions(tmp):
    from addons.media_import.service import _download_captions_sync

    (tmp / "Clip.loft").write_text("{}", encoding="utf-8")
    _download_captions_sync(URL, tmp / "Clip", language="en")


def _stt_audio(tmp):
    from addons.media_import.service import _download_stt_audio_sync

    (tmp / "Clip.loft").write_text("{}", encoding="utf-8")
    _download_stt_audio_sync(URL, tmp / "Clip")


def _listing(limit):
    def call(_tmp):
        from addons.media_import.subscription.providers.youtube import _yt_dlp_extract_flat

        _yt_dlp_extract_flat(CHANNEL_URL, limit)

    return call


def _envelope(_tmp):
    from addons.media_import.subscription.providers.youtube import _yt_dlp_source_info

    _yt_dlp_source_info(CHANNEL_URL)


def _channel_id(_tmp):
    from addons.media_import.subscription.manager import _resolve_channel_id_via_yt_dlp

    _resolve_channel_id_via_yt_dlp(CHANNEL_URL)


def _captions_row(opts):
    return {
        **QUIET,
        "skip_download": True,
        "writeautomaticsub": True,
        "writesubtitles": True,
        "subtitlesformat": "vtt",
        "subtitleslangs": ["en"],
        "outtmpl": opts.get("outtmpl"),
    }


def _stt_row(opts):
    return {
        **QUIET,
        "format": "bestaudio/best",
        "outtmpl": opts.get("outtmpl"),
        "keepvideo": False,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
    }


PLACES = [
    pytest.param(_title, lambda _o: dict(QUIET), None, id="service-title"),
    pytest.param(_metadata, lambda _o: dict(QUIET), None, id="service-metadata"),
    pytest.param(_captions, _captions_row, "captions.%(ext)s", id="service-captions"),
    pytest.param(_stt_audio, _stt_row, ".stt_temp.%(ext)s.part", id="service-stt-audio"),
    pytest.param(_listing(None), lambda _o: dict(FLAT), None, id="youtube-listing-no-limit"),
    pytest.param(_listing(1), lambda _o: {**FLAT, "playlistend": 1}, None, id="youtube-listing-limit-1"),
    pytest.param(_listing(50), lambda _o: {**FLAT, "playlistend": 50}, None, id="youtube-listing-limit-50"),
    pytest.param(_envelope, lambda _o: {**FLAT, "playlistend": 1}, None, id="youtube-source-envelope"),
    pytest.param(_channel_id, lambda _o: {**FLAT, "playlistend": 1}, None, id="manager-channel-id"),
]


def _built_options(call, tmp_path) -> list[dict]:
    ydl = type("YDL", (_RecordingYDL,), {"built": []})
    with patch("yt_dlp.YoutubeDL", ydl), contextlib.suppress(Exception):
        call(tmp_path)
    assert ydl.built, "no yt_dlp.YoutubeDL was built"
    return ydl.built


@pytest.mark.parametrize("call, row, outtmpl_suffix", PLACES)
def test_spec_addon_142_i3_i4_each_options_dict_is_its_row_plus_node_only(
    tmp_path, call, row, outtmpl_suffix,
):
    for opts in _built_options(call, tmp_path):
        assert opts == {**row(opts), **NODE_ONLY}
        assert "remote_components" not in opts
        if outtmpl_suffix is not None:
            assert isinstance(opts["outtmpl"], str)
            assert opts["outtmpl"].endswith(outtmpl_suffix)


@pytest.fixture()
def no_node_on_path(monkeypatch):
    kept = [
        d for d in os.environ.get("PATH", "").split(os.pathsep)
        if d and not os.path.exists(os.path.join(d, "node"))
    ]
    monkeypatch.setenv("PATH", os.pathsep.join(kept))
    assert shutil.which("node") is None


@pytest.mark.parametrize("call, _row, _suffix", PLACES)
def test_spec_addon_142_i6_without_node_on_path_each_options_dict_builds_a_real_youtubedl(
    tmp_path, no_node_on_path, call, _row, _suffix,
):
    import yt_dlp

    for opts in _built_options(call, tmp_path):
        assert opts.get("js_runtimes") == {"node": {}}
        with yt_dlp.YoutubeDL(opts):
            pass


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(".")[:3])


def test_spec_addon_142_item_1_the_test_image_has_node_24_and_yt_dlps_ejs_component():
    import subprocess
    from importlib.metadata import version

    node = shutil.which("node")
    assert node is not None, "no node on PATH"
    out = subprocess.run([node, "--version"], capture_output=True, text=True, check=True)
    assert out.stdout.strip().startswith("v24."), out.stdout
    assert _version(version("yt-dlp")) >= (2026, 10, 26)
    assert version("yt-dlp-ejs")
