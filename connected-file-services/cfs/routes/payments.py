import json
import logging

import stripe
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from ..extensions import db
from ..models import Payment, PricePackage, StripeEvent
from ..security import customer_required
from ..services import BusinessError, audit, fail_payment, fulfil_payment, start_payment

bp = Blueprint("payments", __name__)
log = logging.getLogger("cfs.payments")


def _stripe_client():
    return stripe.StripeClient(current_app.config["STRIPE_SECRET_KEY"])


def _demo_allowed():
    cfg = current_app.config
    return not cfg["STRIPE_SECRET_KEY"] and cfg["DEMO_PAYMENTS"]


@bp.route("/workshop/credits/buy/<int:package_id>", methods=["POST"])
@customer_required
def checkout(package_id):
    package = db.session.get(PricePackage, package_id)
    if package is None:
        abort(404)
    use_stripe = bool(current_app.config["STRIPE_SECRET_KEY"])
    if not use_stripe and not _demo_allowed():
        abort(503, description="Payments are not configured.")
    try:
        payment = start_payment(g.user, package, "stripe" if use_stripe else "demo")
    except BusinessError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("customer.credits"))

    if not use_stripe:
        db.session.commit()
        return redirect(url_for("payments.demo_checkout", payment_id=payment.id))

    base = current_app.config["APP_BASE_URL"]
    try:
        session = _stripe_client().v1.checkout.sessions.create(params={
            "mode": "payment",
            "customer_email": g.user.email,
            "client_reference_id": str(payment.id),
            "metadata": {"payment_id": str(payment.id), "workshop_id": str(payment.workshop_id)},
            "line_items": [{
                "quantity": 1,
                "price_data": {
                    "currency": "gbp",
                    "unit_amount": payment.gross_pence,
                    "product_data": {
                        "name": f"{payment.package_name} ({payment.credits} file credits)",
                        "description": "Connected File Services prepaid ECU file credits, including VAT",
                    },
                },
            }],
            "success_url": f"{base}{url_for('payments.checkout_return', payment_id=payment.id)}",
            "cancel_url": f"{base}{url_for('customer.credits')}",
        })
    except stripe.StripeError:
        log.exception("Stripe checkout creation failed")
        db.session.rollback()
        flash("The payment page could not be opened. Please try again shortly.", "error")
        return redirect(url_for("customer.credits"))
    payment.stripe_session_id = session.id
    db.session.commit()
    return redirect(session.url, code=303)


@bp.route("/workshop/credits/return/<int:payment_id>")
@customer_required
def checkout_return(payment_id):
    payment = db.session.get(Payment, payment_id)
    if payment is None or payment.workshop_id != g.user.workshop_id:
        abort(404)
    # Credits are only added by the verified webhook, never by this redirect.
    return render_template("customer/payment_return.html", payment=payment)


@bp.route("/workshop/credits/demo/<int:payment_id>", methods=["GET", "POST"])
@customer_required
def demo_checkout(payment_id):
    if not _demo_allowed():
        abort(404)
    payment = db.session.get(Payment, payment_id)
    if payment is None or payment.workshop_id != g.user.workshop_id or payment.provider != "demo":
        abort(404)
    if request.method == "POST":
        if request.form.get("action") == "cancel":
            fail_payment(payment, "Cancelled on demo checkout")
            flash("Demo payment cancelled. No credits were added.", "info")
            return redirect(url_for("customer.credits"))
        fulfil_payment(payment, actor=g.user)
        flash(f"Demo payment complete: {payment.credits} credits added.", "success")
        return redirect(url_for("customer.invoice_detail", invoice_id=payment.invoice.id))
    return render_template("customer/demo_checkout.html", payment=payment)


@bp.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    secret = current_app.config["STRIPE_WEBHOOK_SECRET"]
    if not secret:
        abort(404)
    payload = request.get_data()
    try:
        stripe.Webhook.construct_event(payload, request.headers.get("Stripe-Signature"), secret)
    except (ValueError, stripe.SignatureVerificationError):
        log.warning("Rejected Stripe webhook with bad signature")
        return {"error": "invalid signature"}, 400
    # Signature verified; work with the plain JSON body.
    event = json.loads(payload)

    if db.session.get(StripeEvent, event["id"]):
        return {"status": "duplicate"}, 200
    db.session.add(StripeEvent(id=event["id"], type=event["type"]))

    obj = event["data"]["object"]
    payment = None
    if event["type"].startswith("checkout.session."):
        payment = db.session.scalar(select(Payment).where(Payment.stripe_session_id == obj["id"]))
        if payment is None:
            log.warning("Stripe event %s for unknown session %s", event["id"], obj["id"])

    if payment is not None:
        if event["type"] in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            if obj.get("payment_status") == "paid":
                if obj.get("amount_total") != payment.gross_pence:
                    audit("payment.amount_mismatch", payment, expected=payment.gross_pence,
                          received=obj.get("amount_total"), event=event["id"])
                    db.session.commit()
                    return {"status": "amount mismatch"}, 200
                fulfil_payment(payment, payment_intent=obj.get("payment_intent"))
        elif event["type"] in ("checkout.session.expired", "checkout.session.async_payment_failed"):
            fail_payment(payment, event["type"])
    db.session.commit()
    return {"status": "ok"}, 200
