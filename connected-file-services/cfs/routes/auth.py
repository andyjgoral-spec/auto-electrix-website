import re
from datetime import timedelta
from urllib.parse import urlparse

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from ..extensions import db
from ..models import ROLE_CUSTOMER, Setting, User, Workshop, utcnow
from ..security import login_required, login_user, logout_user
from ..services import audit, notify_admins

bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 10


def password_problem(password):
    if len(password) < MIN_PASSWORD:
        return f"Use a password of at least {MIN_PASSWORD} characters."
    if password.isalpha() or password.isdigit():
        return "Use a mix of letters and numbers in your password."
    return None


def _safe_next(target):
    if target and urlparse(target).netloc == "" and target.startswith("/") and not target.startswith("//"):
        return target
    return None


def home_for(user):
    return url_for("admin.dashboard") if user.is_admin else url_for("customer.dashboard")


@bp.route("/")
def index():
    if g.user:
        return redirect(home_for(g.user))
    return render_template("auth/landing.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(home_for(g.user))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db.session.scalar(select(User).where(User.email == email))
        now = utcnow()
        if user and user.locked_until and user.locked_until > now:
            flash("Too many failed attempts. Please wait 15 minutes and try again.", "error")
            audit("auth.login_blocked", user, actor=user)
            db.session.commit()
            return render_template("auth/login.html", email=email), 429
        if user and user.is_enabled and user.check_password(password):
            user.failed_logins = 0
            user.locked_until = None
            user.last_login_at = now
            audit("auth.login", user, actor=user)
            db.session.commit()
            login_user(user)
            return redirect(_safe_next(request.args.get("next")) or home_for(user))
        if user:
            user.failed_logins += 1
            if user.failed_logins >= current_app.config["LOGIN_MAX_ATTEMPTS"]:
                user.locked_until = now + timedelta(minutes=current_app.config["LOGIN_LOCK_MINUTES"])
            audit("auth.login_failed", user, actor=None, email=email)
        else:
            audit("auth.login_failed", None, actor=None, email=email)
        db.session.commit()
        flash("Email or password not recognised.", "error")
        return render_template("auth/login.html", email=email), 401
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    if g.user:
        audit("auth.logout", g.user)
        db.session.commit()
    logout_user()
    flash("You have signed out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(home_for(g.user))
    form = request.form
    if request.method == "POST":
        errors = []
        email = form.get("email", "").strip().lower()
        required = {"workshop_name": "Workshop name", "contact_name": "Your name", "email": "Email",
                    "phone": "Phone", "address": "Address", "city": "Town or city", "postcode": "Postcode",
                    "country": "Country"}
        for key, label in required.items():
            if not form.get(key, "").strip():
                errors.append(f"{label} is required.")
        if email and not EMAIL_RE.match(email):
            errors.append("Enter a valid email address.")
        problem = password_problem(form.get("password", ""))
        if problem:
            errors.append(problem)
        if form.get("password") != form.get("password_confirm"):
            errors.append("The two passwords do not match.")
        if not form.get("terms"):
            errors.append("Please accept the terms of service.")
        if email and db.session.scalar(select(User).where(User.email == email)):
            errors.append("An account with this email already exists. Try signing in.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("auth/register.html", form=form), 400

        needs_approval = Setting.get("require_account_approval") == "1"
        workshop = Workshop(
            name=form["workshop_name"].strip(), contact_name=form["contact_name"].strip(),
            phone=form["phone"].strip(), address=form["address"].strip(), city=form["city"].strip(),
            postcode=form["postcode"].strip().upper(), country=form["country"].strip(),
            vat_number=form.get("vat_number", "").strip() or None,
            status="pending" if needs_approval else "active",
        )
        user = User(email=email, name=form["contact_name"].strip(), role=ROLE_CUSTOMER, workshop=workshop)
        user.set_password(form["password"])
        db.session.add_all([workshop, user])
        db.session.flush()
        audit("workshop.registered", workshop, actor=user, status=workshop.status)
        notify_admins(f"New workshop registration: {workshop.name}")
        db.session.commit()
        login_user(user)
        if needs_approval:
            flash("Account created. CAT will verify your workshop shortly. You will be notified when it is active.", "info")
        else:
            flash("Account created. Welcome to Connected File Services.", "success")
        return redirect(url_for("customer.dashboard"))
    return render_template("auth/register.html", form={"country": "United Kingdom"})


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        if not g.user.check_password(current):
            flash("Your current password is not correct.", "error")
        elif new != request.form.get("new_password_confirm"):
            flash("The new passwords do not match.", "error")
        elif password_problem(new):
            flash(password_problem(new), "error")
        else:
            g.user.set_password(new)
            audit("auth.password_changed", g.user)
            db.session.commit()
            login_user(g.user)
            flash("Password changed.", "success")
            return redirect(home_for(g.user))
    return render_template("auth/password.html")
