"""
Stripe webhook endpoint only. Checkout and Customer Portal are in app.py.
Run separately for webhooks: uvicorn stripe_api:app --reload --port 8000
"""
import os

import stripe
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

app = FastAPI(title="RouteCalc Stripe Webhook")


@app.post("/webhook")
async def webhook(request: Request):
    if not WEBHOOK_SECRET:
        return JSONResponse(status_code=500, content={"error": "Webhook secret not set"})
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, WEBHOOK_SECRET)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid payload"})
    except stripe.SignatureVerificationError:
        return JSONResponse(status_code=400, content={"error": "Invalid signature"})

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        # Optional: send email, update internal state, etc.
        pass
    elif event["type"] in (
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        # Subscription changed; Stripe is source of truth, no DB to update
        pass

    return JSONResponse(content={"received": True})
