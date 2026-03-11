"""
Stripe Checkout + Customer Portal + Webhook API.
Run separately: uvicorn stripe_api:app --reload --port 8000
"""
import os
from urllib.parse import urlencode

import stripe
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Query
from fastapi.responses import RedirectResponse, JSONResponse

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
PRICE_TRIAL = os.getenv("STRIPE_PRICE_TRIAL")
PRICE_MONTHLY = os.getenv("STRIPE_PRICE_MONTHLY")
PRICE_YEARLY = os.getenv("STRIPE_PRICE_YEARLY")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8501")
API_BASE_URL = os.getenv("STRIPE_API_BASE_URL", "http://localhost:8000")

app = FastAPI(title="RouteCalc Stripe API")


def _success_url(session_id: str) -> str:
    return f"{APP_BASE_URL}/?session_id={session_id}"


@app.get("/create-checkout-session")
def create_checkout_session(plan: str = Query(..., description="trial | monthly | yearly")):
    if not stripe.api_key or not APP_BASE_URL:
        return JSONResponse(
            status_code=500,
            content={"error": "STRIPE_SECRET_KEY or APP_BASE_URL not set"},
        )
    plan = plan.lower().strip()
    if plan not in ("trial", "monthly", "yearly"):
        return JSONResponse(status_code=400, content={"error": "Invalid plan"})

    price_id = None
    subscription_data = {}
    if plan == "trial":
        price_id = PRICE_TRIAL or PRICE_MONTHLY
        subscription_data = {"trial_period_days": 7}
    elif plan == "monthly":
        price_id = PRICE_MONTHLY
    elif plan == "yearly":
        price_id = PRICE_YEARLY

    if not price_id:
        return JSONResponse(status_code=500, content={"error": "Price ID not configured"})

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=_success_url("{CHECKOUT_SESSION_ID}"),
            cancel_url=APP_BASE_URL + "/",
            subscription_data=subscription_data if subscription_data else None,
        )
        return RedirectResponse(url=session.url, status_code=303)
    except stripe.StripeError as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/customer-portal")
def customer_portal(customer_id: str = Query(..., description="Stripe customer ID")):
    if not stripe.api_key:
        return JSONResponse(status_code=500, content={"error": "STRIPE_SECRET_KEY not set"})
    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=APP_BASE_URL + "/",
        )
        return RedirectResponse(url=session.url, status_code=303)
    except stripe.StripeError as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


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
