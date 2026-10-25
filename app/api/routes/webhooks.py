import stripe
from fastapi import APIRouter, HTTPException, Request, status

from app.core.config import settings
from app.core.db import SessionLocal
from app.services import payments as payments_svc

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/stripe", status_code=status.HTTP_200_OK)
async def stripe_webhook(request: Request) -> dict:
    if settings.STRIPE_WEBHOOK_SECRET is None:
        raise HTTPException(status_code=503, detail="Webhook not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing stripe-signature header")

    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            settings.STRIPE_WEBHOOK_SECRET.get_secret_value(),
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid signature") from None

    if event["type"] == "payment_intent.succeeded":
        async with SessionLocal() as session:
            await payments_svc.handle_payment_succeeded(
                session,
                event_id=event["id"],
                intent=event["data"]["object"],
            )

    return {"received": True}