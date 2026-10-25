import asyncio
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.models import User
from app.services import orders as orders_svc
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
        await client.post(f"/admin/venues/{venue_id}/halls", headers=admin, json={"name": "H"})
    ).json()["id"]
    await client.post(
        f"/admin/halls/{hall_id}/seats:bulk",
        headers=admin,
        json={"rows": ["A"], "seats_per_row": 1, "default_category": "standard"},
    )
    event_id = (
        await client.post("/admin/events", headers=admin, json={"title": "E", "venue_id": venue_id})
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
    seat = (await client.get(f"/showtimes/{showtime_id}/seats")).json()["seats"][0]
    return seat["id"], showtime_id


async def _make_users(n: int) -> list:
    out = []
    async with TestSession() as s:
        for i in range(n):
            u = User(
                email=f"race-{i}-{uuid4().hex[:6]}@x.com",
                password_hash="$argon2id$v=19$m=65536,t=3,p=4$dummy",
                role="customer",
            )
            s.add(u)
            out.append(u)
        await s.commit()
        for u in out:
            await s.refresh(u)
    return out


@pytest.mark.integration
async def test_last_seat_only_one_winner(client: AsyncClient, db):
    seat_id, showtime_id = await _setup(client, db)
    users = await _make_users(50)

    async def attempt(user_id):
        async with TestSession() as s:
            try:
                async with s.begin():
                    await orders_svc.create_order(
                        s,
                        user_id=user_id,
                        showtime_id=showtime_id,
                        seat_ids=[seat_id],
                        category=None,
                        quantity=None,
                        idempotency_key=f"k-{uuid4().hex}",
                        user_agent="test",
                        ip="127.0.0.1",
                    )
                return "ok"
            except orders_svc.SeatsUnavailable:
                return "conflict"

    results = await asyncio.gather(*(attempt(u.id) for u in users))
    assert results.count("ok") == 1, f"Unexpected results: {results}"
    assert results.count("conflict") == 49