from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import update

from app.models import User


async def _make_admin(client: AsyncClient, db) -> dict[str, str]:
    email = f"admin-{uuid4().hex[:8]}@x.com"
    await client.post(
        "/auth/register", json={"email": email, "password": "secret123"}
    )
    await db.execute(update(User).where(User.email == email).values(role="admin"))
    await db.commit()
    login = (
        await client.post(
            "/auth/login", json={"email": email, "password": "secret123"}
        )
    ).json()
    return {"Authorization": f"Bearer {login['access_token']}"}


async def _make_customer(client: AsyncClient) -> dict[str, str]:
    email = f"cust-{uuid4().hex[:8]}@x.com"
    await client.post(
        "/auth/register", json={"email": email, "password": "secret123"}
    )
    login = (
        await client.post(
            "/auth/login", json={"email": email, "password": "secret123"}
        )
    ).json()
    return {"Authorization": f"Bearer {login['access_token']}"}


async def test_admin_endpoints_require_admin(client: AsyncClient, db):
    headers = await _make_customer(client)
    r = await client.post(
        "/admin/venues",
        headers=headers,
        json={"name": "Test", "city": "Moscow"},
    )
    assert r.status_code == 403


async def test_admin_endpoints_require_auth(client: AsyncClient):
    r = await client.post(
        "/admin/venues", json={"name": "Test", "city": "Moscow"}
    )
    assert r.status_code == 401


async def test_create_venue_hall_and_seats(client: AsyncClient, db):
    headers = await _make_admin(client, db)

    v = await client.post(
        "/admin/venues",
        headers=headers,
        json={"name": "Big Hall", "city": "Moscow", "timezone": "Europe/Moscow"},
    )
    assert v.status_code == 201, v.text
    venue_id = v.json()["id"]

    h = await client.post(
        f"/admin/venues/{venue_id}/halls",
        headers=headers,
        json={"name": "Main"},
    )
    assert h.status_code == 201, h.text
    hall_id = h.json()["id"]

    s = await client.post(
        f"/admin/halls/{hall_id}/seats:bulk",
        headers=headers,
        json={
            "rows": ["A", "B"],
            "seats_per_row": 5,
            "categories": {"A": "vip"},
            "default_category": "standard",
        },
    )
    assert s.status_code == 201, s.text
    assert s.json()["created"] == 10


async def test_create_hall_unknown_venue(client: AsyncClient, db):
    headers = await _make_admin(client, db)
    r = await client.post(
        f"/admin/venues/{uuid4()}/halls",
        headers=headers,
        json={"name": "Main"},
    )
    assert r.status_code == 404


async def test_bulk_seats_unknown_hall(client: AsyncClient, db):
    headers = await _make_admin(client, db)
    r = await client.post(
        f"/admin/halls/{uuid4()}/seats:bulk",
        headers=headers,
        json={"rows": ["A"], "seats_per_row": 3},
    )
    assert r.status_code == 404


async def test_create_showtime_materializes_inventory(client: AsyncClient, db):
    headers = await _make_admin(client, db)

    venue_id = (
        await client.post(
            "/admin/venues",
            headers=headers,
            json={"name": "V", "city": "Moscow"},
        )
    ).json()["id"]
    hall_id = (
        await client.post(
            f"/admin/venues/{venue_id}/halls",
            headers=headers,
            json={"name": "H"},
        )
    ).json()["id"]
    await client.post(
        f"/admin/halls/{hall_id}/seats:bulk",
        headers=headers,
        json={
            "rows": ["A", "B"],
            "seats_per_row": 4,
            "categories": {"A": "vip"},
            "default_category": "standard",
        },
    )

    event_id = (
        await client.post(
            "/admin/events",
            headers=headers,
            json={"title": "Concert", "venue_id": venue_id},
        )
    ).json()["id"]

    st = await client.post(
        f"/admin/events/{event_id}/showtimes",
        headers=headers,
        json={
            "hall_id": hall_id,
            "starts_at": "2026-12-01T19:00:00Z",
            "currency": "RUB",
            "prices": [
                {"category": "standard", "price_cents": 100000},
                {"category": "vip", "price_cents": 300000},
            ],
        },
    )
    assert st.status_code == 201, st.text

    detail = await client.get(f"/showtimes/{st.json()['id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["total_seats"] == 8
    assert body["available_seats"] == 8


async def test_create_showtime_missing_prices(client: AsyncClient, db):
    headers = await _make_admin(client, db)

    venue_id = (
        await client.post(
            "/admin/venues",
            headers=headers,
            json={"name": "V", "city": "Moscow"},
        )
    ).json()["id"]
    hall_id = (
        await client.post(
            f"/admin/venues/{venue_id}/halls",
            headers=headers,
            json={"name": "H"},
        )
    ).json()["id"]
    await client.post(
        f"/admin/halls/{hall_id}/seats:bulk",
        headers=headers,
        json={
            "rows": ["A", "B"],
            "seats_per_row": 3,
            "categories": {"A": "vip"},
            "default_category": "standard",
        },
    )
    event_id = (
        await client.post(
            "/admin/events",
            headers=headers,
            json={"title": "E", "venue_id": venue_id},
        )
    ).json()["id"]

    st = await client.post(
        f"/admin/events/{event_id}/showtimes",
        headers=headers,
        json={
            "hall_id": hall_id,
            "starts_at": "2026-12-01T19:00:00Z",
            "currency": "RUB",
            "prices": [{"category": "standard", "price_cents": 100000}],
        },
    )
    assert st.status_code == 422
    assert st.json()["detail"]["missing_categories"] == ["vip"]