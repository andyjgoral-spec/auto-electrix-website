import io

from sqlalchemy import select

from cfs.extensions import db
from cfs.models import AuditLog, Order, User, Workshop
from cfs.services import create_order
from conftest import ORDER_FORM, PASSWORD, Browser, ecu_bytes, make_admin, make_workshop


def _fields():
    f = {k: v for k, v in ORDER_FORM.items() if k != "declaration"}
    f["declaration_accepted"] = True
    return f


def test_registration_creates_pending_workshop(browser):
    r = browser.post("/register", {
        "workshop_name": "New Garage", "contact_name": "Kim", "email": "kim@new.example", "phone": "01",
        "address": "1 St", "city": "Leeds", "postcode": "ls1 1aa", "country": "United Kingdom",
        "password": "GoodPass2026", "password_confirm": "GoodPass2026", "terms": "1",
    })
    assert r.status_code == 302
    ws = db.session.scalar(select(Workshop).where(Workshop.name == "New Garage"))
    assert ws.status == "pending" and ws.postcode == "LS1 1AA"
    user = db.session.scalar(select(User).where(User.email == "kim@new.example"))
    assert user.password_hash != "GoodPass2026"
    page = browser.get("/workshop/upload").data
    assert b"awaiting verification" in page


def test_registration_rejects_weak_password(browser):
    r = browser.post("/register", {
        "workshop_name": "G", "contact_name": "K", "email": "k@x.example", "phone": "1", "address": "a",
        "city": "c", "postcode": "p", "country": "UK", "password": "short", "password_confirm": "short", "terms": "1",
    })
    assert r.status_code == 400
    assert db.session.scalar(select(User).where(User.email == "k@x.example")) is None


def test_login_and_lockout(app, browser):
    make_workshop()
    for _ in range(5):
        assert browser.login("test@test.example", "wrong-password").status_code == 401
    # Correct password is refused while the account is locked.
    assert browser.login("test@test.example", PASSWORD).status_code == 429
    assert db.session.scalar(select(AuditLog).where(AuditLog.action == "auth.login_blocked"))


def test_post_without_csrf_token_is_rejected(app):
    make_workshop()
    client = app.test_client()
    r = client.post("/login", data={"email": "test@test.example", "password": PASSWORD})
    assert r.status_code == 400


def test_anonymous_redirected_to_login(browser):
    r = browser.get("/workshop/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_customer_cannot_open_admin(app, browser):
    make_workshop()
    browser.login("test@test.example")
    assert browser.get("/admin/").status_code == 403
    assert browser.get("/admin/orders").status_code == 403


def test_admin_has_no_customer_dashboard(admin_browser):
    assert admin_browser.get("/workshop/upload").status_code == 403


def test_workshops_cannot_see_each_others_orders_or_files(app):
    _, alice = make_workshop("Alice Garage")
    _, bob = make_workshop("Bob Garage")
    order = create_order(alice, _fields(), "a.bin", ecu_bytes())
    b = Browser(app)
    b.login(bob.email)
    assert b.get(f"/workshop/orders/{order.id}").status_code == 404
    assert b.get(f"/workshop/files/{order.original_file.id}/download").status_code == 404


def test_completed_file_hidden_until_order_completed(app):
    _, alice = make_workshop("Alice Garage")
    admin = make_admin()
    order = create_order(alice, _fields(), "a.bin", ecu_bytes())
    from cfs.services import approve_order, deliver_uploaded_file, reopen_for_revision
    approve_order(order, admin)
    rev = deliver_uploaded_file(order, admin, "c.bin", ecu_bytes("c"), None, True)
    reopen_for_revision(order, admin, "Customer reported a fault")
    b = Browser(app)
    b.login(alice.email)
    assert b.get(f"/workshop/files/{rev.id}/download").status_code == 404


def test_disabled_user_session_ends(app):
    _, user = make_workshop()
    b = Browser(app)
    b.login(user.email)
    assert b.get("/workshop/").status_code == 200
    user.is_enabled = False
    db.session.commit()
    assert b.get("/workshop/").status_code == 302


def test_security_headers(browser):
    r = browser.get("/login")
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_upload_page_rejects_missing_declaration(app):
    _, user = make_workshop()
    b = Browser(app)
    b.login(user.email)
    form = dict(ORDER_FORM, ecu_file=(io.BytesIO(ecu_bytes()), "orig.bin"))
    form.pop("declaration")
    r = b.post("/workshop/upload", form, content_type="multipart/form-data")
    assert r.status_code == 400
    assert db.session.scalar(select(Order)) is None
