import logging
import os
import random

import pytest

from cfs import create_app
from cfs.extensions import db
from cfs.models import ROLE_ADMIN, ROLE_CUSTOMER, User, Workshop
from cfs.seed import ensure_defaults

PASSWORD = "TestPass12345"


@pytest.fixture
def app(tmp_path):
    logging.disable(logging.INFO)
    # Tests use SQLite by default. Set TEST_DATABASE_URL to run them against PostgreSQL.
    url = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": url,
        "SQLALCHEMY_ENGINE_OPTIONS": {},
        "STORAGE_DIR": str(tmp_path / "storage"),
        "STRIPE_SECRET_KEY": "",
        "STRIPE_WEBHOOK_SECRET": "",
        "DEMO_PAYMENTS": True,
    })
    with app.app_context():
        db.drop_all()
        db.create_all()
        ensure_defaults()
        yield app
        db.session.remove()
        db.drop_all()


def make_workshop(name="Test Garage", credits=3, status="active", email=None):
    ws = Workshop(name=name, contact_name="Alex Test", phone="0100", address="1 Road", city="Poole",
                  postcode="BH1 1AA", country="United Kingdom", status=status, credit_balance=credits)
    user = User(email=email or f"{name.split()[0].lower()}@test.example", name="Alex Test",
                role=ROLE_CUSTOMER, workshop=ws)
    user.set_password(PASSWORD)
    db.session.add_all([ws, user])
    db.session.commit()
    return ws, user


def make_admin(email="admin@test.example"):
    user = User(email=email, name="Admin", role=ROLE_ADMIN)
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


def ecu_bytes(seed="a", size=64 * 1024):
    return random.Random(seed).randbytes(size)


class Browser:
    """Test client that handles CSRF tokens like a real form would."""

    def __init__(self, app):
        self.client = app.test_client()

    def token(self):
        self.client.get("/login")
        with self.client.session_transaction() as s:
            return s.get("csrf_token")

    def login(self, email, password=PASSWORD):
        return self.post("/login", {"email": email, "password": password})

    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def post(self, url, data=None, **kw):
        data = dict(data or {})
        data.setdefault("csrf_token", self.token())
        return self.client.post(url, data=data, **kw)


@pytest.fixture
def browser(app):
    return Browser(app)


@pytest.fixture
def admin_browser(app):
    make_admin()
    b = Browser(app)
    b.login("admin@test.example")
    return b


ORDER_FORM = {
    "vehicle_make": "Volkswagen", "vehicle_model": "Golf", "ecu_manufacturer": "Bosch", "ecu_model": "EDC17C64",
    "hardware_number": "04L 907 309 B", "software_number": "1037541789",
    "service_requested": "Stage 1 performance calibration", "declaration": "1",
}
