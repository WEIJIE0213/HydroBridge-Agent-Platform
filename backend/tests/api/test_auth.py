from __future__ import annotations

import httpx

from app.db.models.identity import User


async def test_login_and_current_user(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "SecurePass123!"},
    )
    assert login.status_code == 200
    tokens = login.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"]
    assert tokens["refresh_token"]

    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == seeded_user.email
    assert "admin" in me.json()["roles"]


async def test_login_rejects_wrong_password(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "WrongPassword!"},
    )
    assert response.status_code == 401


async def test_refresh_rotates_token_and_logout_revokes_it(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "SecurePass123!"},
    )
    original_refresh = login.json()["refresh_token"]

    refreshed = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": original_refresh},
    )
    assert refreshed.status_code == 200
    rotated_refresh = refreshed.json()["refresh_token"]
    assert rotated_refresh != original_refresh

    replay = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": original_refresh},
    )
    assert replay.status_code == 401

    logout = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": rotated_refresh},
    )
    assert logout.status_code == 200

    after_logout = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": rotated_refresh},
    )
    assert after_logout.status_code == 401
