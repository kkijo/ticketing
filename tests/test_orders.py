from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import update

from app.models import Order, User


async def _setup_showtime(client: AsyncClient, db) -> tuple[dict, list[str], str]:
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
        json={"rows": ["A"], "seats_per_row": 3, "default_category": "standard"},
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
                "prices": [{"category": "standard", "price_cents": 50000}],
            },
        )
    ).json()["id"]
    seats = (await client.get(f"/showtimes/{showtime_id}/seats")).json()["seats"]
    return admin, [s["id"] for s in seats], showtime_id


async def _customer(client: AsyncClient) -> dict:
    email = f"c-{uuid4().hex[:8]}@x.com"
    await client.post("/auth/register", json={"email": email, "password": "secret123"})
    login = (
        await client.post("/auth/login", json={"email": email, "password": "secret123"})
    ).json()
    return {"Authorization": f"Bearer {login['access_token']}"}


async def test_create_order_holds_seats(client: AsyncClient, db):
    _, seat_ids, showtime_id = await _setup_showtime(client, db)
    headers = await _customer(client)

    r = await client.post(
        "/orders",
        headers={**headers, "Idempotency-Key": "k1"},
        json={"showtime_id": showtime_id, "seat_ids": [seat_ids[0]]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["total_cents"] == 50000
    assert len(body["seats"]) == 1

    seat_map = (await client.get(f"/showtimes/{showtime_id}/seats")).json()["seats"]
    assert seat_map[0]["status"] == "held"


async def test_create_order_requires_idempotency_key(client: AsyncClient, db):
    _, seat_ids, showtime_id = await _setup_showtime(client, db)
    headers = await _customer(client)
    r = await client.post(
        "/orders",
        headers=headers,
        json={"showtime_id": showtime_id, "seat_ids": [seat_ids[0]]},
    )
    assert r.status_code == 400


async def test_create_order_idempotent(client: AsyncClient, db):
    _, seat_ids, showtime_id = await _setup_showtime(client, db)
    headers = await _customer(client)
    req_headers = {**headers, "Idempotency-Key": "same-key"}
    body = {"showtime_id": showtime_id, "seat_ids": [seat_ids[0]]}
    r1 = await client.post("/orders", headers=req_headers, json=body)
    r2 = await client.post("/orders", headers=req_headers, json=body)
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
    assert r1.json()["seats"] == r2.json()["seats"]


async def test_create_order_idempotency_conflict(client: AsyncClient, db):
    _, seat_ids, showtime_id = await _setup_showtime(client, db)
    headers = await _customer(client)
    r1 = await client.post(
        "/orders",
        headers={**headers, "Idempotency-Key": "k"},
        json={"showtime_id": showtime_id, "seat_ids": [seat_ids[0]]},
    )
    assert r1.status_code == 201
    r2 = await client.post(
        "/orders",
        headers={**headers, "Idempotency-Key": "k"},
        json={"showtime_id": showtime_id, "seat_ids": [seat_ids[1]]},
    )
    assert r2.status_code == 422


async def test_seat_already_held(client: AsyncClient, db):
    _, seat_ids, showtime_id = await _setup_showtime(client, db)
    u1 = await _customer(client)
    u2 = await _customer(client)
    r1 = await client.post(
        "/orders",
        headers={**u1, "Idempotency-Key": "a"},
        json={"showtime_id": showtime_id, "seat_ids": [seat_ids[0]]},
    )
    assert r1.status_code == 201
    r2 = await client.post(
        "/orders",
        headers={**u2, "Idempotency-Key": "b"},
        json={"showtime_id": showtime_id, "seat_ids": [seat_ids[0]]},
    )
    assert r2.status_code == 409
    assert "conflicts" in r2.json()["detail"]


async def test_best_available(client: AsyncClient, db):
    _, _, showtime_id = await _setup_showtime(client, db)
    headers = await _customer(client)
    r = await client.post(
        "/orders",
        headers={**headers, "Idempotency-Key": "ba"},
        json={"showtime_id": showtime_id, "category": "standard", "quantity": 2},
    )
    assert r.status_code == 201, r.text
    assert len(r.json()["seats"]) == 2


async def test_best_available_not_enough(client: AsyncClient, db):
    _, _, showtime_id = await _setup_showtime(client, db)
    headers = await _customer(client)
    r = await client.post(
        "/orders",
        headers={**headers, "Idempotency-Key": "ba2"},
        json={"showtime_id": showtime_id, "category": "standard", "quantity": 5},
    )
    assert r.status_code == 409


async def test_get_order_forbidden_for_other(client: AsyncClient, db):
    _, seat_ids, showtime_id = await _setup_showtime(client, db)
    u1 = await _customer(client)
    u2 = await _customer(client)
    order = (
        await client.post(
            "/orders",
            headers={**u1, "Idempotency-Key": "k"},
            json={"showtime_id": showtime_id, "seat_ids": [seat_ids[0]]},
        )
    ).json()
    r = await client.get(f"/orders/{order['id']}", headers=u2)
    assert r.status_code == 403


async def test_cancel_order_releases_seats(client: AsyncClient, db):
    _, seat_ids, showtime_id = await _setup_showtime(client, db)
    headers = await _customer(client)
    order = (
        await client.post(
            "/orders",
            headers={**headers, "Idempotency-Key": "c"},
            json={"showtime_id": showtime_id, "seat_ids": [seat_ids[0]]},
        )
    ).json()
    r = await client.post(f"/orders/{order['id']}/cancel", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    seat_map = (await client.get(f"/showtimes/{showtime_id}/seats")).json()["seats"]
    assert any(s["status"] == "available" for s in seat_map)


async def test_cancel_paid_order_fails(client: AsyncClient, db):
    _, seat_ids, showtime_id = await _setup_showtime(client, db)
    headers = await _customer(client)
    order = (
        await client.post(
            "/orders",
            headers={**headers, "Idempotency-Key": "p"},
            json={"showtime_id": showtime_id, "seat_ids": [seat_ids[0]]},
        )
    ).json()
    await db.execute(update(Order).where(Order.id == order["id"]).values(status="paid"))
    await db.commit()
    r = await client.post(f"/orders/{order['id']}/cancel", headers=headers)
    assert r.status_code == 409