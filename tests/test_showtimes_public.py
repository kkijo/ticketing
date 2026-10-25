from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import update

from app.models import User


async def _seed_showtime(client: AsyncClient, db) -> str:
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
    headers = {"Authorization": f"Bearer {login['access_token']}"}

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
            "rows": ["A"],
            "seats_per_row": 3,
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
    showtime_id = (
        await client.post(
            f"/admin/events/{event_id}/showtimes",
            headers=headers,
            json={
                "hall_id": hall_id,
                "starts_at": "2026-12-01T19:00:00Z",
                "prices": [{"category": "standard", "price_cents": 50000}],
            },
        )
    ).json()["id"]
    return showtime_id


async def test_get_showtime_public(client: AsyncClient, db):
    showtime_id = await _seed_showtime(client, db)
    r = await client.get(f"/showtimes/{showtime_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["total_seats"] == 3
    assert body["available_seats"] == 3
    assert body["currency"] == "RUB"


async def test_get_showtime_seats_map(client: AsyncClient, db):
    showtime_id = await _seed_showtime(client, db)
    r = await client.get(f"/showtimes/{showtime_id}/seats")
    assert r.status_code == 200
    body = r.json()
    assert body["showtime_id"] == showtime_id
    assert len(body["seats"]) == 3
    seats = body["seats"]
    assert all(s["status"] == "available" for s in seats)
    assert seats[0]["row"] == "A"
    assert seats[0]["number"] == 1
    assert seats[0]["price_cents"] == 50000
    # Must not leak internal fields
    for s in seats:
        assert "hold_id" not in s
        assert "order_id" not in s


async def test_get_showtime_seats_cached(client: AsyncClient, db):
    showtime_id = await _seed_showtime(client, db)
    r1 = await client.get(f"/showtimes/{showtime_id}/seats")
    r2 = await client.get(f"/showtimes/{showtime_id}/seats")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["seats"] == r2.json()["seats"]
    # updated_at may differ between cached and non-cached — don't compare it


async def test_showtime_not_found(client: AsyncClient):
    r = await client.get(f"/showtimes/{uuid4()}/seats")
    assert r.status_code == 404
    r2 = await client.get(f"/showtimes/{uuid4()}")
    assert r2.status_code == 404