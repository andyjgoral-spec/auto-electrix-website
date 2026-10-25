import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-key")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://cfs:cfs@localhost:5432/cfs"
    )
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    STORAGE_DIR = os.environ.get("STORAGE_DIR", str(BASE_DIR / "storage"))

    # Upload rules for ECU files
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024
    ECU_MIN_BYTES = 1024
    ECU_MAX_BYTES = 16 * 1024 * 1024
    ECU_ALLOWED_EXTENSIONS = {
        "bin", "ori", "hex", "s19", "srec", "mot", "frf", "sgo", "odx", "mpc", "kess", "slave", "mod",
    }

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE")
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    LOGIN_MAX_ATTEMPTS = 5
    LOGIN_LOCK_MINUTES = 15

    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    # Demo checkout is only ever available when Stripe is not configured.
    DEMO_PAYMENTS = _bool("DEMO_PAYMENTS", True)
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5000").rstrip("/")

    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    MAIL_FROM = os.environ.get("MAIL_FROM", "files@connected-automotive.example")

    SELLER_NAME = os.environ.get("SELLER_NAME", "Connected Automotive Technologies")
    SELLER_ADDRESS = os.environ.get("SELLER_ADDRESS", "Address not configured")
    SELLER_VAT_NUMBER = os.environ.get("SELLER_VAT_NUMBER", "Not configured")
