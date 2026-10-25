from httpx import AsyncClient

API = "/auth"


async def _register(
    client: AsyncClient,
    email: str = "u@example.com",
    password: str = "secret123",
):
    return await client.post(
        f"{API}/register", json={"email": email, "password": password}
    )


async def _login(
    client: AsyncClient,
    email: str = "u@example.com",
    password: str = "secret123",
):
    return await client.post(
        f"{API}/login", json={"email": email, "password": password}
    )


async def test_register_creates_user(client: AsyncClient):
    r = await _register(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "u@example.com"
    assert body["role"] == "customer"
    assert "password" not in body and "password_hash" not in body


async def test_register_duplicate_email(client: AsyncClient):
    await _register(client)
    r = await _register(client)
    assert r.status_code == 409


async def test_register_short_password(client: AsyncClient):
    r = await client.post(
        f"{API}/register", json={"email": "a@b.c", "password": "short"}
    )
    assert r.status_code == 422


async def test_login_returns_tokens(client: AsyncClient):
    await _register(client)
    r = await _login(client)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"] and data["refresh_token"]


async def test_login_wrong_password(client: AsyncClient):
    await _register(client)
    r = await _login(client, password="wrong-pass")
    assert r.status_code == 401


async def test_login_unknown_email(client: AsyncClient):
    r = await _login(client, email="nobody@example.com")
    assert r.status_code == 401


async def test_me_requires_token(client: AsyncClient):
    r = await client.get("/me")
    assert r.status_code == 401


async def test_me_with_valid_token(client: AsyncClient):
    await _register(client)
    login = (await _login(client)).json()
    r = await client.get(
        "/me", headers={"Authorization": f"Bearer {login['access_token']}"}
    )
    assert r.status_code == 200
    assert r.json()["email"] == "u@example.com"


async def test_refresh_rotates_and_invalidates_old(client: AsyncClient):
    await _register(client)
    login = (await _login(client)).json()
    old_refresh = login["refresh_token"]

    r = await client.post(f"{API}/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 200
    new_refresh = r.json()["refresh_token"]
    assert new_refresh != old_refresh

    r2 = await client.post(f"{API}/refresh", json={"refresh_token": old_refresh})
    assert r2.status_code == 401

    r3 = await client.post(f"{API}/refresh", json={"refresh_token": new_refresh})
    assert r3.status_code == 200


async def test_logout_revokes_refresh(client: AsyncClient):
    await _register(client)
    login = (await _login(client)).json()
    refresh = login["refresh_token"]

    r = await client.post(f"{API}/logout", json={"refresh_token": refresh})
    assert r.status_code == 204

    r2 = await client.post(f"{API}/refresh", json={"refresh_token": refresh})
    assert r2.status_code == 401


async def test_access_token_cannot_be_used_as_refresh(client: AsyncClient):
    await _register(client)
    login = (await _login(client)).json()
    r = await client.post(
        f"{API}/refresh", json={"refresh_token": login["access_token"]}
    )
    assert r.status_code == 401


async def test_refresh_token_cannot_be_used_as_access(client: AsyncClient):
    await _register(client)
    login = (await _login(client)).json()
    r = await client.get(
        "/me", headers={"Authorization": f"Bearer {login['refresh_token']}"}
    )
    assert r.status_code == 401


async def test_password_hash_is_argon2(client: AsyncClient, db):
    from sqlalchemy import select

    from app.models import User

    await _register(client)
    user = (await db.execute(select(User))).scalar_one()
    assert user.password_hash.startswith("$argon2id$")
    assert "secret123" not in user.password_hash