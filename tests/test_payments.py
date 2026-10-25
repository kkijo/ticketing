from unittest.mock import patch
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import text, update

from app.models import User
from app.services import payments as pay_svc
from tests.conftest import TestSession


async def _seed(client, db):
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
        json={"rows": ["A"], "seats_per_row": 2, "default_category": "standard"},
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
    return admin, user, st


async def _create_order(client, user, showtime_id, key="k"):
    seats = (await client.get(f"/showtimes/{showtime_id}/seats")).json()["seats"]
    return (
        await client.post(
            "/orders",
            headers={**user, "Idempotency-Key": key},
            json={"showtime_id": showtime_id, "seat_ids": [seats[0]["id"]]},
        )
    ).json()


async def test_pay_order_creates_payment_intent(client: AsyncClient, db):
    _, user, showtime_id = await _seed(client, db)
    order = await _create_order(client, user, showtime_id)

    fake_pi = {
        "id": "pi_test_123",
        "client_secret": "pi_test_123_secret_abc",
        "amount": 1000,
        "currency": "rub",
    }
    with patch.object(pay_svc.stripe.PaymentIntent, "create", return_value=fake_pi):
        r = await client.post(f"/orders/{order['id']}/pay", headers=user)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["payment_intent_id"] == "pi_test_123"
    assert body["client_secret"] == "pi_test_123_secret_abc"
    assert body["amount_cents"] == 1000


async def test_pay_order_requires_ownership(client: AsyncClient, db):
    _, user, showtime_id = await _seed(client, db)
    order = await _create_order(client, user, showtime_id)

    other_email = f"o-{uuid4().hex[:6]}@x.com"
    await client.post(
        "/auth/register", json={"email": other_email, "password": "secret123"}
    )
    other = (
        await client.post(
            "/auth/login", json={"email": other_email, "password": "secret123"}
        )
    ).json()
    other_h = {"Authorization": f"Bearer {other['access_token']}"}

    r = await client.post(f"/orders/{order['id']}/pay", headers=other_h)
    assert r.status_code == 404


async def test_webhook_paid_issues_tickets(client: AsyncClient, db):
    _, user, showtime_id = await _seed(client, db)
    order = await _create_order(client, user, showtime_id)

    intent = {
        "id": f"pi_{uuid4().hex[:8]}",
        "amount": 1000,
        "metadata": {"order_id": order["id"]},
    }

    async with TestSession() as session:
        result = await pay_svc.handle_payment_succeeded(
            session, event_id=f"evt_{uuid4().hex[:8]}", intent=intent
        )
    assert result == "paid"

    async with TestSession() as session:
        row = await session.execute(
            text("SELECT status FROM orders WHERE id = :oid"), {"oid": order["id"]}
        )
        assert row.scalar_one() == "paid"
        tickets = await session.execute(
            text("SELECT id, status FROM tickets WHERE order_id = :oid"),
            {"oid": order["id"]},
        )
        t_rows = tickets.all()
        assert len(t_rows) == 1
        assert t_rows[0].status == "valid"


async def test_webhook_duplicate_event(client: AsyncClient, db):
    _, user, showtime_id = await _seed(client, db)
    order = await _create_order(client, user, showtime_id)
    intent = {
        "id": f"pi_{uuid4().hex[:8]}",
        "amount": 1000,
        "metadata": {"order_id": order["id"]},
    }

    async with TestSession() as session:
        r1 = await pay_svc.handle_payment_succeeded(
            session, event_id="evt_same", intent=intent
        )
    async with TestSession() as session:
        r2 = await pay_svc.handle_payment_succeeded(
            session, event_id="evt_same", intent=intent
        )
    assert r1 == "paid"
    assert r2 == "duplicate"


async def test_webhook_after_expiry_refunds(client: AsyncClient, db):
    _, user, showtime_id = await _seed(client, db)
    order = await _create_order(client, user, showtime_id)

    async with TestSession() as session:
        await session.execute(
            text("UPDATE orders SET status='expired' WHERE id = :oid"),
            {"oid": order["id"]},
        )
        await session.commit()

    intent = {
        "id": f"pi_{uuid4().hex[:8]}",
        "amount": 1000,
        "metadata": {"order_id": order["id"]},
    }

    with patch.object(pay_svc, "_refund") as refund_mock:
        refund_mock.return_value = None
        async with TestSession() as session:
            result = await pay_svc.handle_payment_succeeded(
                session, event_id=f"evt_{uuid4().hex[:8]}", intent=intent
            )
        refund_mock.assert_called_once()
    assert result == "refunded"

    async with TestSession() as session:
        row = await session.execute(
            text("SELECT status FROM payments WHERE provider_payment_id = :pid"),
            {"pid": intent["id"]},
        )
        assert row.scalar_one() == "refunded"