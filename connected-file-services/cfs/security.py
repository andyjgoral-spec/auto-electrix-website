import hmac
import secrets
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for

from .extensions import db
from .models import ROLE_ADMIN, ROLE_CUSTOMER, User


def load_current_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id is None:
        return
    user = db.session.get(User, user_id)
    # A disabled account or a password change (new auth_version) ends the session.
    if user is None or not user.is_enabled or session.get("auth_v") != user.password_hash[-12:]:
        session.clear()
        return
    g.user = user


def login_user(user):
    session.clear()  # new session on login, which prevents session fixation
    session.permanent = True
    session["user_id"] = user.id
    session["auth_v"] = user.password_hash[-12:]
    session["csrf_token"] = secrets.token_urlsafe(32)


def logout_user():
    session.clear()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Please sign in to continue.", "info")
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def role_required(role):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user.role != role:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


admin_required = role_required(ROLE_ADMIN)
customer_required = role_required(ROLE_CUSTOMER)


# --- CSRF protection -------------------------------------------------------

def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


CSRF_EXEMPT_ENDPOINTS = {"payments.stripe_webhook"}


def check_csrf():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.endpoint in CSRF_EXEMPT_ENDPOINTS:
        return
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, description="Your form expired. Please go back, refresh the page and try again.")


def set_security_headers(response):
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "form-action 'self' https://checkout.stripe.com; frame-ancestors 'none'; base-uri 'self'",
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if g.get("user") is not None:
        response.headers.setdefault("Cache-Control", "no-store")
    return response
