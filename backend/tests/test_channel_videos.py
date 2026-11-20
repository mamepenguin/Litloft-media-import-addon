"""SPEC-ADDON-161: ``GET /channel-videos`` lists one channel's active ``.loft`` files in one drive."""
from __future__ import annotations

import os
from datetime import datetime
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.config as config

_REAL_GET_DRIVE_PATH = config.get_drive_path
_REAL_GET_DRIVE_ACCESS_GROUP = config.get_drive_access_group

PATH = "/api/addons/media_import/channel-videos"
DRIVE = "d"
OTHER_DRIVE = "e"


@pytest.fixture()
def client(media_import_db, drive_path, monkeypatch):
    from addons.media_import.router import router

    monkeypatch.setattr(
        config,
        "load_drives",
        lambda: [
            {"name": DRIVE, "path": str(drive_path)},
            {"name": OTHER_DRIVE, "path": str(drive_path)},
            {"name": "secret", "path": str(drive_path), "access_group": "vip"},
        ],
    )
    monkeypatch.setattr(config, "get_drive_path", _REAL_GET_DRIVE_PATH)
    monkeypatch.setattr(
        config, "get_drive_access_group", _REAL_GET_DRIVE_ACCESS_GROUP
    )

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _seed(
    session,
    file_id: str,
    *,
    provider: str = "youtube",
    channel: str | None = "Chan",
    published_at: str | None = "20260101",
    created_at: str = "2026-01-01T00:00:00",
    drive: str = DRIVE,
    deleted_at: str | None = None,
    missing_since: str | None = None,
) -> None:
    from app.models import File

    db = session()
    try:
        db.add(
            File(
                id=file_id,
                filename=f"{file_id}.loft",
                title=file_id,
                drive=drive,
                folder_path="",
                file_path=f"{file_id}.loft",
                file_size=100,
                file_type="video",
                mime_type="application/vnd.litloft.loft+json",
                duration=300.0,
                created_at=_ts(created_at),
                updated_at=_ts(created_at),
                deleted_at=_ts(deleted_at),
                missing_since=_ts(missing_since),
            )
        )
        db.flush()
        db.execute(
            text(
                "INSERT INTO loft_metadata "
                "(file_id, provider, provider_item_id, url, published_at, "
                " channel, has_captions, captions_downloaded) "
                "VALUES (:id, :provider, :item, :url, :pub, :channel, 0, 0)"
            ),
            {
                "id": file_id,
                "provider": provider,
                "item": file_id,
                "url": f"https://example.test/{file_id}",
                "pub": published_at,
                "channel": channel,
            },
        )
        db.commit()
    finally:
        db.close()


def _get(client, *, drive: str | None = DRIVE, **params):
    headers = {"X-Lit-Drive": quote(drive)} if drive is not None else {}
    return client.get(PATH, params=params, headers=headers)


def _ids(res) -> list[str]:
    assert res.status_code == 200, res.text
    return [item["file_id"] for item in res.json()["items"]]


def _dump(session) -> list[str]:
    db = session()
    try:
        raw = db.connection().connection.driver_connection
        return list(raw.iterdump())
    finally:
        db.close()


def _tree(root) -> list[tuple[str, int]]:
    return sorted(
        (os.path.relpath(os.path.join(d, f), root), os.path.getmtime(os.path.join(d, f)))
        for d, _, files in os.walk(root)
        for f in files
    )


# --- Authorization and validation order (I1, I3) ---------------------------


def test_spec_addon_161_missing_drive_header_is_400(client, media_import_db) -> None:
    _seed(media_import_db, "a")

    res = _get(client, drive=None, provider="youtube", channel="Chan")

    assert res.status_code == 400
    assert "items" not in res.json()


@pytest.mark.parametrize("drive", ["secret", "nosuch"])
@pytest.mark.parametrize(
    "params",
    [
        {"provider": "youtube", "channel": "Chan"},
        {},
        {"provider": "", "channel": "  "},
    ],
    ids=["valid", "no-params", "blank-params"],
)
def test_spec_addon_161_i1_locked_or_unknown_drive_is_404_before_validation(
    client, media_import_db, drive, params
) -> None:
    _seed(media_import_db, "a", drive=drive)

    res = _get(client, drive=drive, **params)

    assert res.status_code == 404
    assert res.json() == {"detail": f"Drive not found: {drive}"}


@pytest.mark.parametrize(
    "params",
    [
        {"channel": "Chan"},
        {"provider": "youtube"},
        {},
        {"provider": "", "channel": "Chan"},
        {"provider": "youtube", "channel": ""},
        {"provider": "   ", "channel": "Chan"},
        {"provider": "youtube", "channel": " \t "},
    ],
    ids=[
        "no-provider",
        "no-channel",
        "neither",
        "empty-provider",
        "empty-channel",
        "blank-provider",
        "blank-channel",
    ],
)
def test_spec_addon_161_i3_missing_empty_or_blank_parameter_is_422(
    client, media_import_db, params
) -> None:
    _seed(media_import_db, "a")
    assert _ids(_get(client, provider="youtube", channel="Chan")) == ["a"]

    res = _get(client, **params)

    assert res.status_code == 422
    assert "items" not in res.json()


def test_spec_addon_161_no_match_is_200_with_empty_items(client, media_import_db) -> None:
    _seed(media_import_db, "a")

    res = _get(client, provider="youtube", channel="Nobody")

    assert res.status_code == 200
    assert res.json() == {"items": []}


# --- Matching (I1, I2) -----------------------------------------------------


def test_spec_addon_161_i1_i2_returns_only_this_drive_active_exact_matches(
    client, media_import_db
) -> None:
    _seed(media_import_db, "match1")
    _seed(media_import_db, "match2", published_at="20251231")
    _seed(media_import_db, "other_drive", drive=OTHER_DRIVE)
    _seed(media_import_db, "trashed", deleted_at="2026-02-01T00:00:00")
    _seed(media_import_db, "missing", missing_since="2026-02-01T00:00:00")
    _seed(media_import_db, "other_provider", provider="vimeo")
    _seed(media_import_db, "other_channel", channel="Chan2")
    _seed(media_import_db, "lower_case", channel="chan")
    _seed(media_import_db, "trailing_space", channel="Chan ")
    _seed(media_import_db, "leading_space", channel=" Chan")
    _seed(media_import_db, "no_channel", channel=None)

    assert _ids(_get(client, provider="youtube", channel="Chan")) == [
        "match1",
        "match2",
    ]


def test_spec_addon_161_i1_the_other_drive_answers_its_own_files(
    client, media_import_db
) -> None:
    _seed(media_import_db, "here")
    _seed(media_import_db, "there", drive=OTHER_DRIVE)

    assert _ids(_get(client, drive=OTHER_DRIVE, provider="youtube", channel="Chan")) == [
        "there"
    ]


def test_spec_addon_161_i2_same_channel_under_another_provider_is_separate(
    client, media_import_db
) -> None:
    _seed(media_import_db, "yt")
    _seed(media_import_db, "vm", provider="vimeo")

    assert _ids(_get(client, provider="vimeo", channel="Chan")) == ["vm"]


@pytest.mark.parametrize(
    "requested,expected",
    [
        (" Chan", ["leading"]),
        ("Chan ", ["trailing"]),
        ("chan", ["lower"]),
        ("Chan", ["exact"]),
    ],
)
def test_spec_addon_161_i2_channel_matches_exactly_without_trimming_or_case_folding(
    client, media_import_db, requested, expected
) -> None:
    _seed(media_import_db, "exact", channel="Chan")
    _seed(media_import_db, "leading", channel=" Chan")
    _seed(media_import_db, "trailing", channel="Chan ")
    _seed(media_import_db, "lower", channel="chan")

    assert _ids(_get(client, provider="youtube", channel=requested)) == expected


def test_spec_addon_161_i2_characters_that_need_encoding_match(
    client, media_import_db
) -> None:
    name = "Tom & Jerry / 公式 #1?"
    _seed(media_import_db, "odd", channel=name)
    _seed(media_import_db, "plain")

    assert _ids(_get(client, provider="youtube", channel=name)) == ["odd"]


# --- Order (I4) ------------------------------------------------------------


def test_spec_addon_161_i4_orders_by_published_date_then_created_then_id_descending(
    client, media_import_db
) -> None:
    # Published dates: b 01-10, d (empty -> created 01-08), c (NULL -> created 01-07),
    # a 01-05, e and f tie on 01-03 and split on created_at, g and h tie on both.
    _seed(media_import_db, "a", published_at="20260105", created_at="2026-03-01T00:00:00")
    _seed(media_import_db, "b", published_at="2026-01-10", created_at="2026-01-01T00:00:00")
    _seed(media_import_db, "c", published_at=None, created_at="2026-01-07T09:00:00")
    _seed(media_import_db, "d", published_at="", created_at="2026-01-08T09:00:00")
    _seed(media_import_db, "e", published_at="20260103", created_at="2026-02-02T00:00:00")
    _seed(media_import_db, "f", published_at="20260103", created_at="2026-02-01T00:00:00")
    _seed(media_import_db, "g", published_at="20260102", created_at="2026-02-01T00:00:00")
    _seed(media_import_db, "h", published_at="20260102", created_at="2026-02-01T00:00:00")

    assert _ids(_get(client, provider="youtube", channel="Chan")) == [
        "b",
        "d",
        "c",
        "a",
        "e",
        "f",
        "h",
        "g",
    ]


def test_spec_addon_161_items_carry_file_id_and_published_at(
    client, media_import_db
) -> None:
    _seed(media_import_db, "dated", published_at="20260105")
    _seed(media_import_db, "undated", published_at=None, created_at="2026-01-01T00:00:00")

    items = _get(client, provider="youtube", channel="Chan").json()["items"]

    assert [set(item) for item in items] == [{"file_id", "published_at"}] * 2
    by_id = {item["file_id"]: item["published_at"] for item in items}
    assert by_id["undated"] is None
    assert isinstance(by_id["dated"], str)


def test_spec_addon_161_returns_every_match_without_a_cap(
    client, media_import_db
) -> None:
    for i in range(250):
        _seed(media_import_db, f"v{i:04d}", published_at=f"2025{(i % 12) + 1:02d}01")

    assert len(_ids(_get(client, provider="youtube", channel="Chan"))) == 250


# --- Writes nothing (I3) ---------------------------------------------------


def test_spec_addon_161_i3_writes_nothing(
    client, media_import_db, drive_path
) -> None:
    _seed(media_import_db, "a")
    (drive_path / "a.loft").write_text("{}")
    before_db = _dump(media_import_db)
    before_fs = _tree(drive_path)

    res = _get(client, provider="youtube", channel="Chan")
    bad = _get(client, provider="youtube", channel="  ")

    assert _ids(res) == ["a"]
    assert bad.status_code == 422
    assert _dump(media_import_db) == before_db
    assert _tree(drive_path) == before_fs
