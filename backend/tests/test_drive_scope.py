"""A locked drive and a drive that does not exist must answer identically."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.config as config

# Captured at import, before the ``drive_path`` fixture replaces them with
# fakes that accept every name: the oracle only shows through the real lookups.
_REAL_GET_DRIVE_PATH = config.get_drive_path
_REAL_GET_DRIVE_ACCESS_GROUP = config.get_drive_access_group

ROUTES = [
    (
        "POST",
        "/api/addons/media_import/link",
        {"url": "https://www.youtube.com/watch?v=abc", "folder_path": ""},
    ),
    ("GET", "/api/addons/media_import/link/f1/metadata", None),
    ("POST", "/api/addons/media_import/link/f1/refresh", None),
    ("POST", "/api/addons/media_import/link/f1/stt", None),
    ("GET", "/api/addons/media_import/subscriptions?drive={drive}", None),
]


@pytest.fixture()
def client(media_import_db, drive_path, monkeypatch):
    from addons.media_import.router import router

    monkeypatch.setattr(
        config,
        "load_drives",
        lambda: [
            {"name": "drv", "path": str(drive_path)},
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


def _call(client, method: str, path: str, body: dict | None, drive: str):
    json = {**body, "drive": drive} if body is not None else None
    return client.request(
        method,
        path.format(drive=drive),
        json=json,
        headers={"X-Lit-Drive": drive},
    )


@pytest.mark.parametrize("method,path,body", ROUTES)
@pytest.mark.parametrize("drive", ["secret", "nosuch"])
def test_locked_and_unknown_drive_share_one_answer(
    client, method, path, body, drive
) -> None:
    res = _call(client, method, path, body, drive)

    assert res.status_code == 404
    assert res.json() == {"detail": f"Drive not found: {drive}"}
