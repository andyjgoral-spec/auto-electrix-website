import logging

import click
from flask import Flask, g, render_template
from sqlalchemy import func, select

from .config import Config
from .extensions import db
from .security import check_csrf, csrf_token, load_current_user, set_security_headers


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    db.init_app(app)

    from .routes import admin, auth, customer, payments
    app.register_blueprint(auth.bp)
    app.register_blueprint(customer.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(payments.bp)

    app.before_request(load_current_user)
    app.before_request(check_csrf)
    app.after_request(set_security_headers)

    _register_template_helpers(app)
    _register_error_pages(app)
    _register_commands(app)
    return app


def _register_template_helpers(app):
    from .models import ORDER_STATUS_LABELS, Notification
    from .services import money

    @app.context_processor
    def inject():
        unread = 0
        if g.get("user") is not None:
            unread = db.session.scalar(
                select(func.count(Notification.id)).where(
                    Notification.user_id == g.user.id, Notification.is_read.is_(False)
                )
            )
        return {
            "unread_notifications": unread,
            "stripe_enabled": bool(app.config["STRIPE_SECRET_KEY"]),
        }

    # Globals are visible inside imported macros, unlike context variables.
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["status_labels"] = ORDER_STATUS_LABELS
    app.jinja_env.filters["money"] = money

    @app.template_filter("dt")
    def format_dt(value, fmt="%d %b %Y, %H:%M"):
        return value.strftime(fmt) if value else "—"

    @app.template_filter("filesize")
    def filesize(n):
        if n is None:
            return "—"
        if n >= 1024 * 1024:
            return f"{n / 1024 / 1024:.2f} MB"
        return f"{n / 1024:.1f} KB"

    @app.template_filter("hours")
    def hours(h):
        if h is None:
            return "—"
        if h < 1:
            return f"{round(h * 60)} min"
        return f"{h:.1f} h"

    @app.template_filter("pct")
    def pct(bp):
        return f"{int(bp) / 100:g}%"


def _register_error_pages(app):
    for code, title in [(400, "Request not accepted"), (403, "Access denied"), (404, "Page not found"),
                        (413, "File too large"), (500, "Something went wrong")]:
        def handler(error, code=code, title=title):
            if code == 500:
                db.session.rollback()
            description = getattr(error, "description", "") if code != 500 else ""
            return render_template("errors/error.html", code=code, title=title, description=description), code
        app.register_error_handler(code, handler)


def _register_commands(app):
    @app.cli.command("init-db")
    def init_db():
        """Create database tables and default settings."""
        from .seed import ensure_defaults
        db.create_all()
        ensure_defaults()
        click.echo("Database ready.")

    @app.cli.command("seed-demo")
    @click.option("--reset", is_flag=True, help="Delete all data and files first.")
    def seed_demo(reset):
        """Load sample workshops, orders and an admin account."""
        from .seed import seed_demo_data
        seed_demo_data(reset=reset)

    @app.cli.command("create-admin")
    @click.argument("email")
    @click.argument("name")
    @click.password_option()
    def create_admin(email, name, password):
        """Create a CAT administrator account."""
        from .models import ROLE_ADMIN, User
        from .services import audit
        user = User(email=email.strip().lower(), name=name, role=ROLE_ADMIN)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        audit("user.created", user, role=ROLE_ADMIN, via="cli")
        db.session.commit()
        click.echo(f"Admin {email} created.")
