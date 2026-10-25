from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import text, update

from app.main import app
from app.models import User
from app.workers.tasks.hold_expirer import expire_holds
from tests.conftest import TestSession


async def _setup(client, db):
    email = f"admin-{uuid4().hex[:8]}@x.com"
    await client.post("/auth/register", json={"email": email, "password": "secret123"})
    await db.execute(update(User).where(User.email == email).values(role="admin"))
    await db.commit()
    login = (
        await client.post("/auth/login", json={"email": email, "password": "secret123"})
    ).json()
    admin = {"Authorization": f"Bearer {login['access_token']}"}

    venue_id = (
        await client.post("/admin/venues", headers=admin, json={"name": "V", "city": "M"})
    ).json()["id"]
    hall_id = (
        await client.post(
            f"/admin/venues/{venue_id}/halls", headers=admin, json={"name": "H"}
        )
    ).json()["id"]
    await client.post(
        f"/admin/halls/{hall_id}/seats:bulk",
        headers=admin,
        json={"rows": ["A"], "seats_per_row": 2, "default_category": "standard"},
    )
    event_id = (
        await client.post(
            "/admin/events", headers=admin, json={"title": "E", "venue_id": venue_id}
        )
    ).json()["id"]
    showtime_id = (
        await client.post(
            f"/admin/events/{event_id}/showtimes",
            headers=admin,
            json={
                "hall_id": hall_id,
                "starts_at": "2026-12-01T19:00:00Z",
                "prices": [{"category": "standard", "price_cents": 100}],
            },
        )
    ).json()["id"]
    seats = (await client.get(f"/showtimes/{showtime_id}/seats")).json()["seats"]
    return [s["id"] for s in seats], showtime_id


async def _customer(client):
    email = f"c-{uuid4().hex[:8]}@x.com"
    await client.post("/auth/register", json={"email": email, "password": "secret123"})
    login = (
        await client.post("/auth/login", json={"email": email, "password": "secret123"})
    ).json()
    return {"Authorization": f"Bearer {login['access_token']}"}


async def test_hold_expiration_releases_seats(client: AsyncClient, db):
    seat_ids, showtime_id = await _setup(client, db)
    headers = await _customer(client)
    order = (
        await client.post(
            "/orders",
            headers={**headers, "Idempotency-Key": "exp"},
            json={"showtime_id": showtime_id, "seat_ids": [seat_ids[0]]},
        )
    ).json()
    assert order["status"] == "pending"

    await db.execute(
        text(
            "UPDATE seat_holds SET expires_at = now() - interval '1 second' "
            "WHERE order_id = :oid"
        ),
        {"oid": order["id"]},
    )
    await db.commit()

    await expire_holds({"session": TestSession})
    await app.state.cache.flushdb()

    await db.rollback()
    row = await db.execute(
        text("SELECT status FROM orders WHERE id = :oid"), {"oid": order["id"]}
    )
    assert row.scalar_one() == "expired"
    seat_map = (await client.get(f"/showtimes/{showtime_id}/seats")).json()["seats"]
    assert any(s["status"] == "available" for s in seat_map)