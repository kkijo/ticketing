from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import update

from app.models import User
from app.services import payments as pay_svc
from tests.conftest import TestSession


async def _seed_with_paid_order(client, db):
    email = f"admin-{uuid4().hex[:8]}@x.com"
    await client.post("/auth/register", json={"email": email, "password": "secret123"})
    await db.execute(update(User).where(User.email == email).values(role="admin"))
    await db.commit()

    login = (
        await client.post(
            "/auth/login", json={"email": email, "password": "secret123"}
        )
    ).json()
    admin = {"Authorization": f"Bearer {login['access_token']}"}

    v = (
        await client.post(
            "/admin/venues", headers=admin, json={"name": "V", "city": "M"}
        )
    ).json()["id"]
    h = (
        await client.post(
            f"/admin/venues/{v}/halls", headers=admin, json={"name": "H"}
        )
    ).json()["id"]
    await client.post(
        f"/admin/halls/{h}/seats:bulk",
        headers=admin,
        json={"rows": ["A"], "seats_per_row": 1, "default_category": "standard"},
    )
    e = (
        await client.post(
            "/admin/events", headers=admin, json={"title": "E", "venue_id": v}
        )
    ).json()["id"]
    st = (
        await client.post(
            f"/admin/events/{e}/showtimes",
            headers=admin,
            json={
                "hall_id": h,
                "starts_at": "2026-12-01T19:00:00Z",
                "prices": [{"category": "standard", "price_cents": 1000}],
            },
        )
    ).json()["id"]

    ce = f"c-{uuid4().hex[:6]}@x.com"
    await client.post("/auth/register", json={"email": ce, "password": "secret123"})
    cl = (
        await client.post(
            "/auth/login", json={"email": ce, "password": "secret123"}
        )
    ).json()
    user = {"Authorization": f"Bearer {cl['access_token']}"}

    seats = (await client.get(f"/showtimes/{st}/seats")).json()["seats"]
    order = (
        await client.post(
            "/orders",
            headers={**user, "Idempotency-Key": "x"},
            json={"showtime_id": st, "seat_ids": [seats[0]["id"]]},
        )
    ).json()

    intent = {
        "id": f"pi_{uuid4().hex[:8]}",
        "amount": 1000,
        "metadata": {"order_id": order["id"]},
    }
    async with TestSession() as session:
        await pay_svc.handle_payment_succeeded(
            session, event_id=f"evt_{uuid4().hex[:8]}", intent=intent
        )

    se = f"s-{uuid4().hex[:6]}@x.com"
    await client.post("/auth/register", json={"email": se, "password": "secret123"})
    await db.execute(update(User).where(User.email == se).values(role="staff"))
    await db.commit()
    sl = (
        await client.post(
            "/auth/login", json={"email": se, "password": "secret123"}
        )
    ).json()
    staff = {"Authorization": f"Bearer {sl['access_token']}"}

    return user, staff, order


async def test_my_tickets_lists_paid(client: AsyncClient, db):
    user, _, _ = await _seed_with_paid_order(client, db)
    r = await client.get("/me/tickets", headers=user)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["status"] == "valid"
    assert "." in body[0]["qr_payload"]


async def test_check_in_success(client: AsyncClient, db):
    user, staff, _ = await _seed_with_paid_order(client, db)
    tickets = (await client.get("/me/tickets", headers=user)).json()
    payload = tickets[0]["qr_payload"]

    r = await client.post("/check-in", headers=staff, json={"qr_payload": payload})
    assert r.status_code == 200, r.text
    assert r.json()["first_entry"] is True


async def test_check_in_duplicate(client: AsyncClient, db):
    user, staff, _ = await _seed_with_paid_order(client, db)
    tickets = (await client.get("/me/tickets", headers=user)).json()
    payload = tickets[0]["qr_payload"]

    r1 = await client.post("/check-in", headers=staff, json={"qr_payload": payload})
    assert r1.status_code == 200
    r2 = await client.post("/check-in", headers=staff, json={"qr_payload": payload})
    assert r2.status_code == 409
    assert r2.json()["detail"]["type"] == "ticket-already-used"


async def test_check_in_invalid_signature(client: AsyncClient, db):
    _, staff, _ = await _seed_with_paid_order(client, db)
    r = await client.post(
        "/check-in", headers=staff, json={"qr_payload": "deadbeef.notarealsig"}
    )
    assert r.status_code == 400


async def test_check_in_requires_staff(client: AsyncClient, db):
    user, _, _ = await _seed_with_paid_order(client, db)
    tickets = (await client.get("/me/tickets", headers=user)).json()
    payload = tickets[0]["qr_payload"]
    r = await client.post("/check-in", headers=user, json={"qr_payload": payload})
    assert r.status_code == 403