"""Acceptance tests for the YouTube provider's item-failure classification,
its oEmbed title lookup and its item URL.

SPEC-ADDON-027 (unavailable), SPEC-ADDON-028 (not started, rate limited,
other), SPEC-ADDON-029 (title lookup). Network is cut at yt-dlp
(``_fetch_metadata_sync_for_provider``) and at the YouTube-only opener.
"""
from __future__ import annotations

import json
import urllib.parse
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text
from yt_dlp.utils import DownloadError

from addons.media_import.subscription.providers.youtube import YouTubeProvider
from addons.media_import.subscription.registry import (
    REF_KIND_CHANNEL,
    SubscriptionRef,
    _reset_subscription_registry_for_tests,
    register_subscription_provider,
)

_YT = "addons.media_import.subscription.providers.youtube"
_REF = SubscriptionRef(kind=REF_KIND_CHANNEL, ref="UCabcdefghijklmnopqrstuv")
_PREFIX = "ERROR: [youtube] dQw4w9WgXcQ: "
_RATE_SESSION = (
    "Video unavailable. This content isn't available, try again later. "
    "The current session has been rate-limited by YouTube for up to an hour. "
    "It is recommended to use `-t sleep` to add a delay between video requests"
)

RATE = "rate_limited"
UNAVAILABLE = "unavailable"
NOT_STARTED = "not_started"
OTHER = "other"

_CASES = [
    # group 1: rate limited, checked before "Video unavailable"
    (_RATE_SESSION, RATE),
    (_RATE_SESSION.replace("'", "’"), RATE),
    ("HTTP Error 429: Too Many Requests", RATE),
    ("Sign in to confirm you're not a bot. Use --cookies", RATE),
    ("Sign in to confirm you’re not a bot. Use --cookies", RATE),
    ("Please solve the CAPTCHA", RATE),
    ("Premieres in 2 hours. Try again later", RATE),
    # group 2: unavailable
    ("Join this channel to get access to members-only content like this video", UNAVAILABLE),
    ("This video is available to this channel's members on level: Gold", UNAVAILABLE),
    ("This video is available to this channel’s members on level: Gold", UNAVAILABLE),
    ("Sign in to confirm your age. This video may be inappropriate for some users.", UNAVAILABLE),
    ("This video is not available", UNAVAILABLE),
    ("Video unavailable", UNAVAILABLE),
    ("VIDEO UNAVAILABLE", UNAVAILABLE),
    ("Private video. Sign in if you've been granted access to this video", UNAVAILABLE),
    ("This video has been removed by the uploader", UNAVAILABLE),
    ("This content is not available in your country", UNAVAILABLE),
    ("Video unavailable. Premieres in 3 days", UNAVAILABLE),
    # group 3: not started
    ("This live event will begin in 3 days.", NOT_STARTED),
    ("Premieres in 2 hours", NOT_STARTED),
    ("premieres IN 10 minutes", NOT_STARTED),
    # group 4: everything else
    ("Unable to extract initial player response", OTHER),
    ("Requested format is not available", OTHER),
    ("Unable to download webpage: <urlopen error timed out>", OTHER),
]


def _expected_class(outcome: str):
    from addons.media_import.subscription import registry

    return {
        RATE: registry.ItemRateLimited,
        UNAVAILABLE: registry.ItemUnavailable,
        NOT_STARTED: registry.ItemNotStarted,
    }.get(outcome)


class TestFetchItemClassification:
    """SPEC-ADDON-027, SPEC-ADDON-028 section 5 / I1, I3."""

    @pytest.mark.parametrize("message, outcome", _CASES)
    def test_spec_addon_027_028_message_maps_to_outcome(
        self, message: str, outcome: str
    ) -> None:
        from addons.media_import.subscription import registry

        typed = (
            registry.ItemRateLimited,
            registry.ItemUnavailable,
            registry.ItemNotStarted,
        )
        with patch(
            f"{_YT}._fetch_metadata_sync_for_provider",
            side_effect=DownloadError(_PREFIX + message),
        ):
            with pytest.raises(Exception) as excinfo:
                YouTubeProvider().fetch_item(_REF, "dQw4w9WgXcQ")

        expected = _expected_class(outcome)
        if expected is None:
            assert not isinstance(excinfo.value, typed)
        else:
            assert isinstance(excinfo.value, expected)
            others = tuple(t for t in typed if t is not expected)
            assert not isinstance(excinfo.value, others)


class TestItemUrl:
    """SPEC-ADDON-029: source_url for a YouTube item, read through the
    items endpoint so the provider method's name is not assumed."""

    def test_spec_addon_029_youtube_source_url_is_watch_url(
        self, media_import_db, drive_path
    ) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from addons.media_import.router import router

        _reset_subscription_registry_for_tests()
        register_subscription_provider(YouTubeProvider())
        try:
            sub_id = _insert_youtube_subscription(media_import_db)
            db = media_import_db()
            try:
                db.execute(
                    text(
                        "INSERT INTO subscription_videos (subscription_id, item_id, "
                        " status, error_kind, first_seen_at) VALUES "
                        " (:s, 'dQw4w9WgXcQ', 'failed', 'permanent', "
                        " '2026-05-01T00:00:00')"
                    ),
                    {"s": sub_id},
                )
                db.commit()
            finally:
                db.close()
            app = FastAPI()
            app.include_router(router)
            with TestClient(app) as c:
                c.headers["X-Lit-Drive"] = "d"
                body = c.get(
                    f"/api/addons/media_import/subscriptions/{sub_id}/videos"
                ).json()
        finally:
            _reset_subscription_registry_for_tests()

        assert body[0]["source_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


# ---- oEmbed title lookup through a real sync ----------------------

_RSS_EMPTY_TITLES = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Channel</title>
  <entry>
    <id>yt:video:memb0000001</id>
    <yt:videoId>memb0000001</yt:videoId>
    <yt:channelId>UCabcdefghijklmnopqrstuv</yt:channelId>
    <title></title>
    <published>2026-10-01T10:00:00+00:00</published>
  </entry>
  <entry>
    <id>yt:video:upcm0000001</id>
    <yt:videoId>upcm0000001</yt:videoId>
    <yt:channelId>UCabcdefghijklmnopqrstuv</yt:channelId>
    <title></title>
    <published>2026-10-02T10:00:00+00:00</published>
  </entry>
</feed>
"""

_FLAT_EMPTY_TITLES = [
    {"id": "memb0000001", "title": "", "upload_date": "20261001"},
    {"id": "upcm0000001", "title": "", "upload_date": "20261002"},
]


def _insert_youtube_subscription(session_factory) -> int:
    db = session_factory()
    try:
        db.execute(
            text(
                "INSERT INTO subscriptions "
                "(provider, source_kind, source_ref, drive, folder_path, "
                " is_enabled, cooldown_minutes, include_no_transcript, created_at, "
                " display_title) "
                "VALUES ('youtube', 'channel', 'UCabcdefghijklmnopqrstuv', 'd', '', "
                " 1, 60, 0, '2026-05-01T00:00:00', 'Channel')"
            )
        )
        sub_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
        db.commit()
    finally:
        db.close()
    return sub_id


class _Opener:
    """Answers the RSS feed and oEmbed; records every oEmbed call."""

    def __init__(self, oembed: dict[str, bytes]) -> None:
        self.oembed = oembed
        self.oembed_calls: list[tuple[str, object]] = []

    def __call__(self, req, *args, **kwargs):
        url = req if isinstance(req, str) else req.full_url
        timeout = kwargs.get("timeout", args[1] if len(args) > 1 else (args[0] if args else None))
        resp = MagicMock()
        resp.status = 200
        resp.getcode.return_value = 200
        resp.headers.get_content_charset.return_value = "utf-8"
        if "/oembed" in url:
            self.oembed_calls.append((url, timeout))
            target = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["url"][0]
            vid = urllib.parse.parse_qs(urllib.parse.urlparse(target).query)["v"][0]
            body = self.oembed[vid]
        elif "feeds/videos.xml" in url:
            body = _RSS_EMPTY_TITLES
        else:
            raise AssertionError(f"unexpected request {url}")
        resp.read.return_value = body
        cm = MagicMock()
        cm.__enter__.return_value = resp
        cm.__exit__.return_value = False
        return cm


class TestOEmbedTitleLookup:
    """SPEC-ADDON-029 step 9 and section 5."""

    def test_spec_addon_029_failed_items_get_oembed_title_empty_answer_stays_null(
        self, media_import_db, drive_path
    ) -> None:
        from addons.media_import.subscription.manager import SubscriptionManager

        def _meta(url, *a, **kw):
            if "memb0000001" in url:
                raise DownloadError(
                    "ERROR: [youtube] memb0000001: Join this channel to get "
                    "access to members-only content like this video"
                )
            raise DownloadError(
                "ERROR: [youtube] upcm0000001: This live event will begin in 3 days."
            )

        opener = _Opener({
            "memb0000001": json.dumps({"title": "Members Stream #12"}).encode(),
            "upcm0000001": json.dumps({"title": ""}).encode(),
        })
        _reset_subscription_registry_for_tests()
        register_subscription_provider(YouTubeProvider())
        try:
            sub_id = _insert_youtube_subscription(media_import_db)
            with patch(f"{_YT}._youtube_opener.open", side_effect=opener), \
                 patch(f"{_YT}._yt_dlp_extract_flat", return_value=_FLAT_EMPTY_TITLES), \
                 patch(f"{_YT}._fetch_metadata_sync_for_provider", side_effect=_meta), \
                 patch("time.sleep"):
                SubscriptionManager()._sync_blocking(sub_id)
        finally:
            _reset_subscription_registry_for_tests()

        db = media_import_db()
        try:
            rows = {
                r["item_id"]: r
                for r in db.execute(
                    text(
                        "SELECT item_id, status, error_kind, title "
                        "FROM subscription_videos WHERE subscription_id = :s"
                    ),
                    {"s": sub_id},
                ).mappings().all()
            }
        finally:
            db.close()

        assert (rows["memb0000001"]["error_kind"], rows["memb0000001"]["title"]) == (
            "permanent", "Members Stream #12",
        )
        assert (rows["upcm0000001"]["error_kind"], rows["upcm0000001"]["title"]) == (
            "upcoming", None,
        )
        assert len(opener.oembed_calls) == 2
        for url, timeout in opener.oembed_calls:
            parsed = urllib.parse.urlparse(url)
            assert (parsed.scheme, parsed.netloc, parsed.path) == (
                "https", "www.youtube.com", "/oembed",
            )
            q = urllib.parse.parse_qs(parsed.query)
            assert q["format"] == ["json"]
            assert q["url"][0].startswith("https://www.youtube.com/watch?v=")
            assert timeout == 10
