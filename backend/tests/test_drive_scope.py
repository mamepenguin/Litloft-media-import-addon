"""A locked drive and a drive that does not exist must answer identically."""
from __future__ import annotations

from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.config as config

# Captured at import, before the ``drive_path`` fixture replaces them with
# fakes that accept every name: the oracle only shows through the real lookups.
_REAL_GET_DRIVE_PATH = config.get_drive_path
_REAL_GET_DRIVE_ACCESS_GROUP = config.get_drive_access_group

_P = "/api/addons/media_import"
_URL = "https://www.youtube.com/watch?v=abc"

# Every route that takes X-Lit-Drive, with a body that passes validation so
# the drive check is what answers. ``{drive}`` is filled with the header's name.
ROUTES: list[tuple[str, str, dict | None]] = [
    ("POST", f"{_P}/link", {"url": _URL, "drive": "{drive}"}),
    ("GET", f"{_P}/link/f1/metadata", None),
    ("POST", f"{_P}/link/f1/refresh", None),
    ("POST", f"{_P}/link/f1/stt", None),
    ("POST", f"{_P}/subscriptions/resolve", {"url": _URL}),
    ("POST", f"{_P}/subscriptions", {"url": _URL, "drive": "{drive}"}),
    ("GET", f"{_P}/subscriptions?drive={{drive}}", None),
    ("GET", f"{_P}/subscriptions/summary?drive={{drive}}", None),
    ("PATCH", f"{_P}/subscriptions/1", {}),
    ("GET", f"{_P}/subscriptions/1/avatar", None),
    ("POST", f"{_P}/subscriptions/1/refresh-metadata", None),
    ("DELETE", f"{_P}/subscriptions/1", None),
    ("POST", f"{_P}/subscriptions/1/sync", None),
    ("POST", f"{_P}/subscriptions/1/backfill", {}),
    ("GET", f"{_P}/subscriptions/1/videos", None),
    ("POST", f"{_P}/subscriptions/1/videos/x/retry", None),
    ("GET", f"{_P}/activity?drive={{drive}}", None),
    ("GET", f"{_P}/watch?lane=feed&drive={{drive}}", None),
    ("POST", f"{_P}/subscriptions/1/videos/x/resolve-conflict", {"action": "skip"}),
    ("POST", f"{_P}/subscriptions/1/videos/x/dismiss", None),
]


@pytest.fixture()
def client(media_import_db, drive_path, monkeypatch):
    from addons.media_import.router import router

    monkeypatch.setattr(
        config,
        "load_drives",
        lambda: [
            {"name": "drv", "path": str(drive_path)},
            {"name": "動画", "path": str(drive_path)},
            {"name": "secret", "path": str(drive_path), "access_group": "vip"},
            {"name": "秘密", "path": str(drive_path), "access_group": "vip"},
        ],
    )
    monkeypatch.setattr(config, "get_drive_path", _REAL_GET_DRIVE_PATH)
    monkeypatch.setattr(
        config, "get_drive_access_group", _REAL_GET_DRIVE_ACCESS_GROUP
    )

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _call(client, method: str, path: str, body: dict | None, drive: str):
    json = (
        {k: v.format(drive=drive) if isinstance(v, str) else v for k, v in body.items()}
        if body is not None
        else None
    )
    return client.request(
        method,
        path.format(drive=quote(drive)),
        json=json,
        headers={"X-Lit-Drive": quote(drive)},
    )


def test_routes_table_is_every_drive_scoped_route() -> None:
    from addons.media_import.router import router

    scoped = {
        (method, route.path)
        for route in router.routes
        if isinstance(route, APIRoute)
        and any(p.alias == "X-Lit-Drive" for p in route.dependant.header_params)
        for method in route.methods
    }
    declared = {
        (method, route.path)
        for method, path, _ in ROUTES
        for route in router.routes
        if isinstance(route, APIRoute)
        and method in route.methods
        and route.path_regex.match(path.split("?")[0])
    }

    assert len(ROUTES) == 20
    assert declared == scoped


@pytest.mark.parametrize("method,path,body", ROUTES)
@pytest.mark.parametrize("drive", ["secret", "nosuch", "秘密"])
def test_locked_and_unknown_drive_share_one_answer(
    client, method, path, body, drive
) -> None:
    res = _call(client, method, path, body, drive)

    assert res.status_code == 404
    assert res.json() == {"detail": f"Drive not found: {drive}"}


def test_percent_encoded_accessible_drive_is_decoded(client) -> None:
    res = _call(client, "GET", f"{_P}/subscriptions?drive={{drive}}", None, "動画")

    assert res.status_code == 200
    assert res.json() == []
