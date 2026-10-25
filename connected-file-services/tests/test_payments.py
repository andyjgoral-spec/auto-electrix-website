import hashlib
import hmac
import json
import time

from sqlalchemy import select

from cfs.extensions import db
from cfs.models import Invoice, Payment, PricePackage
from cfs.services import price_breakdown, start_payment
from conftest import Browser, make_workshop

WEBHOOK_SECRET = "whsec_test_secret"


def test_price_breakdown(app):
    assert price_breakdown(7000) == {"net": 7000, "vat": 1400, "gross": 8400, "rate_bp": 2000}
    packages = {p.name: (p.price_pence, p.credits) for p in db.session.scalars(select(PricePackage))}
    assert packages == {"Single file": (7000, 1), "5+1 package": (35000, 6), "10+2 package": (70000, 12)}


def test_demo_checkout_adds_credits_and_invoice(app):
    ws, user = make_workshop(credits=0)
    b = Browser(app)
    b.login(user.email)
    pkg = db.session.scalar(select(PricePackage).where(PricePackage.credits == 6))
    r = b.post(f"/workshop/credits/buy/{pkg.id}")
    assert r.status_code == 302 and "/demo/" in r.headers["Location"]
    payment = db.session.scalar(select(Payment))
    b.post(r.headers["Location"], {"action": "pay"})
    db.session.refresh(ws)
    assert ws.credit_balance == 6
    invoice = db.session.scalar(select(Invoice))
    assert invoice.gross_pence == 42000 and invoice.vat_pence == 7000 and invoice.number.startswith("CFS-INV-")
    assert b.get(f"/workshop/invoices/{invoice.id}").status_code == 200
    # Paying twice does not double the credits.
    b.post(r.headers["Location"], {"action": "pay"})
    db.session.refresh(ws)
    assert ws.credit_balance == 6
    assert payment.status == "paid"


def test_admin_price_change_applies_to_new_purchases(app):
    from conftest import make_admin
    make_admin()
    admin = Browser(app)
    admin.login("admin@test.example")
    pkg = db.session.scalar(select(PricePackage).where(PricePackage.credits == 1))
    admin.post("/admin/pricing", {"action": "save_package", "package_id": pkg.id, "name": "Single file",
                                  "price": "75.00", "credits": "1", "active": "1", "sort_order": "1"})
    db.session.refresh(pkg)
    assert pkg.price_pence == 7500
    _, user = make_workshop()
    payment = start_payment(user, pkg, "demo")
    assert payment.net_pence == 7500 and payment.gross_pence == 9000


def test_pending_workshop_cannot_buy(app):
    _, user = make_workshop(status="pending")
    b = Browser(app)
    b.login(user.email)
    pkg = db.session.scalar(select(PricePackage))
    b.post(f"/workshop/credits/buy/{pkg.id}")
    assert db.session.scalar(select(Payment)) is None


# --- Stripe webhook ----------------------------------------------------------

def _signed(app, event, secret=WEBHOOK_SECRET):
    payload = json.dumps(event)
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return app.test_client().post("/stripe/webhook", data=payload, content_type="application/json",
                                  headers={"Stripe-Signature": f"t={ts},v1={sig}"})


def _stripe_payment(app):
    app.config["STRIPE_WEBHOOK_SECRET"] = WEBHOOK_SECRET
    ws, user = make_workshop(credits=0)
    pkg = db.session.scalar(select(PricePackage).where(PricePackage.credits == 12))
    payment = start_payment(user, pkg, "stripe")
    payment.stripe_session_id = "cs_test_123"
    db.session.commit()
    return ws, payment


def _event(payment, event_id="evt_1", amount=None, kind="checkout.session.completed", status="paid"):
    return {"id": event_id, "object": "event", "type": kind, "data": {"object": {
        "id": "cs_test_123", "object": "checkout.session", "payment_status": status,
        "amount_total": payment.gross_pence if amount is None else amount, "payment_intent": "pi_123"}}}


def test_webhook_rejects_bad_signature(app):
    ws, payment = _stripe_payment(app)
    r = _signed(app, _event(payment), secret="whsec_wrong")
    assert r.status_code == 400
    db.session.refresh(ws)
    assert ws.credit_balance == 0


def test_webhook_adds_credits_once(app):
    ws, payment = _stripe_payment(app)
    assert _signed(app, _event(payment)).status_code == 200
    db.session.refresh(ws)
    assert ws.credit_balance == 12
    assert payment.stripe_payment_intent == "pi_123"
    # Stripe retries and replays are ignored.
    assert _signed(app, _event(payment)).json["status"] == "duplicate"
    assert _signed(app, _event(payment, event_id="evt_2")).status_code == 200
    db.session.refresh(ws)
    assert ws.credit_balance == 12
    assert len(db.session.scalars(select(Invoice)).all()) == 1


def test_webhook_amount_mismatch_adds_nothing(app):
    ws, payment = _stripe_payment(app)
    _signed(app, _event(payment, amount=100))
    db.session.refresh(ws)
    assert ws.credit_balance == 0 and payment.status == "pending"


def test_webhook_unpaid_or_expired_session(app):
    ws, payment = _stripe_payment(app)
    _signed(app, _event(payment, status="unpaid"))
    db.session.refresh(ws)
    assert ws.credit_balance == 0
    _signed(app, _event(payment, event_id="evt_3", kind="checkout.session.expired", status="unpaid"))
    db.session.refresh(payment)
    assert payment.status == "failed"


def test_webhook_disabled_without_secret(app):
    assert app.test_client().post("/stripe/webhook", data="{}").status_code == 404
