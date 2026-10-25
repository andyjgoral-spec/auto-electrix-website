import io
from pathlib import Path

import pytest
from sqlalchemy import select

from cfs import storage
from cfs.extensions import db
from cfs.models import AuditLog, CreditTransaction, LibraryFile, Order
from cfs.services import (
    BusinessError, add_library_from_order, approve_order, compatibility_check, create_order,
    deliver_library_file, deliver_uploaded_file, reject_order,
)
from conftest import ORDER_FORM, Browser, ecu_bytes, make_admin, make_workshop


def fields(**over):
    f = {k: v for k, v in ORDER_FORM.items() if k != "declaration"}
    f["declaration_accepted"] = True
    f.update(over)
    return f


def upload(b, data, name="orig.bin", **over):
    form = dict(ORDER_FORM, **over)
    form["ecu_file"] = (io.BytesIO(data), name)
    return b.post("/workshop/upload", form, content_type="multipart/form-data")


def test_upload_uses_a_credit_and_stores_file_privately(app):
    ws, user = make_workshop(credits=2)
    b = Browser(app)
    b.login(user.email)
    data = ecu_bytes()
    r = upload(b, data)
    assert r.status_code == 302
    order = db.session.scalar(select(Order))
    db.session.refresh(ws)
    assert ws.credit_balance == 1
    original = order.original_file
    path = storage.path_for(original.storage_key)
    assert path.read_bytes() == data  # stored byte for byte
    assert "static" not in str(path) and original.storage_key.startswith("originals/")
    assert Path(app.static_folder) not in path.parents
    assert db.session.scalar(select(AuditLog).where(AuditLog.action == "order.submitted"))


@pytest.mark.parametrize("name,data,reason", [
    ("tool.exe", b"MZ" + b"\x00" * 4096, b"not accepted"),
    ("orig.bin", b"MZ" + b"\x01" * 4096, b"Windows programs"),
    ("orig.bin", b"\xff" * 4096, b"same value"),
    ("orig.bin", b"\x01\x02" * 100, b"too small"),
    ("orig.hex", b"not intel hex\n" * 200, b"Intel HEX"),
    ("orig.bin", b"PK\x03\x04" + b"\x01" * 4096, b"ZIP"),
])
def test_invalid_files_are_rejected_and_credit_kept(app, name, data, reason):
    ws, user = make_workshop(credits=1)
    b = Browser(app)
    b.login(user.email)
    r = upload(b, data, name)
    assert r.status_code == 400
    assert reason in r.data
    db.session.refresh(ws)
    assert ws.credit_balance == 1
    assert db.session.scalar(select(Order)) is None


def test_no_credits_no_upload(app):
    _, user = make_workshop(credits=0)
    with pytest.raises(BusinessError):
        create_order(user, fields(), "a.bin", ecu_bytes())


def test_pending_workshop_cannot_upload(app):
    _, user = make_workshop(credits=5, status="pending")
    with pytest.raises(BusinessError):
        create_order(user, fields(), "a.bin", ecu_bytes())


def test_reject_refunds_credit(app):
    ws, user = make_workshop(credits=1)
    admin = make_admin()
    order = create_order(user, fields(), "a.bin", ecu_bytes())
    db.session.refresh(ws)
    assert ws.credit_balance == 0
    reject_order(order, admin, "Read is incomplete")
    db.session.refresh(ws)
    assert ws.credit_balance == 1 and order.status == "rejected"
    kinds = [t.kind for t in db.session.scalars(select(CreditTransaction).order_by(CreditTransaction.id))]
    assert kinds == ["order", "refund"]


def test_full_admin_flow_through_web(app, admin_browser):
    ws, user = make_workshop()
    data = ecu_bytes()
    order = create_order(user, fields(), "a.bin", data)
    url = f"/admin/orders/{order.id}/action"

    # Admin downloads the original, which is audited.
    r = admin_browser.get(f"/admin/files/{order.original_file.id}/download")
    assert r.status_code == 200 and r.data == data

    admin_browser.post(url, {"action": "approve"})
    db.session.refresh(order)
    assert order.status == "in_progress"

    # Wrong size is refused.
    admin_browser.post(url, {"action": "deliver_upload", "confirm": "1",
                             "completed_file": (io.BytesIO(ecu_bytes("x", 1024 * 32)), "c.bin")},
                       content_type="multipart/form-data")
    db.session.refresh(order)
    assert order.status == "in_progress"

    # Missing approval tick is refused.
    admin_browser.post(url, {"action": "deliver_upload",
                             "completed_file": (io.BytesIO(ecu_bytes("c")), "c.bin")},
                       content_type="multipart/form-data")
    db.session.refresh(order)
    assert order.status == "in_progress"

    completed = ecu_bytes("c")
    admin_browser.post(url, {"action": "deliver_upload", "confirm": "1",
                             "completed_file": (io.BytesIO(completed), "c.bin")},
                       content_type="multipart/form-data")
    db.session.refresh(order)
    assert order.status == "completed" and order.current_completed_file.version == 1

    b = Browser(app)
    b.login(user.email)
    r = b.get(f"/workshop/files/{order.current_completed_file.id}/download")
    assert r.status_code == 200 and r.data == completed
    actions = [a.action for a in db.session.scalars(select(AuditLog))]
    for expected in ("order.submitted", "order.approved", "file.delivered", "file.downloaded"):
        assert expected in actions
    # The customer was notified.
    assert b"ready to download" in b.get("/workshop/notifications").data


def _completed_order(user, admin, data, cal_seed="cal"):
    order = create_order(user, fields(), "a.bin", data)
    approve_order(order, admin)
    deliver_uploaded_file(order, admin, "cal.bin", ecu_bytes(cal_seed, len(data)), None, True)
    return order


def test_library_exact_match_requires_identical_original(app):
    _, user = make_workshop(credits=5)
    admin = make_admin()
    data = ecu_bytes("orig")
    first = _completed_order(user, admin, data)
    entry = add_library_from_order(first, admin, "Road tested", True)

    same = create_order(user, fields(), "b.bin", data)
    assert compatibility_check(same)["exact"] == [entry]

    # Same HW/SW numbers but a different original file: never offered.
    other = create_order(user, fields(), "c.bin", ecu_bytes("different"))
    compat = compatibility_check(other)
    assert compat["exact"] == []
    lib, problems = compat["near"][0]
    assert lib == entry and "original file checksum differs" in problems

    approve_order(other, admin)
    with pytest.raises(BusinessError):
        deliver_library_file(other, admin, entry, confirmed=True)


def test_library_identifier_formatting_is_normalised_but_values_exact(app):
    _, user = make_workshop(credits=5)
    admin = make_admin()
    data = ecu_bytes("orig")
    entry = add_library_from_order(_completed_order(user, admin, data), admin, None, True)
    spaced = create_order(user, fields(hardware_number="04l-907-309-b"), "b.bin", data)
    assert compatibility_check(spaced)["exact"] == [entry]
    wrong_sw = create_order(user, fields(software_number="1037541790"), "c.bin", data)
    assert compatibility_check(wrong_sw)["exact"] == []


def test_library_delivery_needs_human_confirmation(app):
    _, user = make_workshop(credits=5)
    admin = make_admin()
    data = ecu_bytes("orig")
    entry = add_library_from_order(_completed_order(user, admin, data), admin, None, True)
    order = create_order(user, fields(), "b.bin", data)
    approve_order(order, admin)
    with pytest.raises(BusinessError):
        deliver_library_file(order, admin, entry, confirmed=False)
    rev = deliver_library_file(order, admin, entry, confirmed=True)
    assert rev.library_file == entry and rev.sha256 == entry.sha256 and order.status == "completed"
    assert entry.times_used == 1


def test_library_tampered_file_is_not_delivered(app):
    _, user = make_workshop(credits=5)
    admin = make_admin()
    data = ecu_bytes("orig")
    entry = add_library_from_order(_completed_order(user, admin, data), admin, None, True)
    storage.path_for(entry.storage_key).write_bytes(ecu_bytes("tampered", len(data)))
    order = create_order(user, fields(), "b.bin", data)
    approve_order(order, admin)
    with pytest.raises(BusinessError, match="integrity"):
        deliver_library_file(order, admin, entry, confirmed=True)


def test_new_library_version_retires_previous(app):
    _, user = make_workshop(credits=5)
    admin = make_admin()
    data = ecu_bytes("orig")
    v1 = add_library_from_order(_completed_order(user, admin, data, "cal1"), admin, None, True)
    v2 = add_library_from_order(_completed_order(user, admin, data, "cal2"), admin, None, True)
    assert v2.version == 2 and v2.supersedes == v1
    assert v1.status == "retired"
    order = create_order(user, fields(), "x.bin", data)
    assert compatibility_check(order)["exact"] == [v2]
    assert db.session.scalar(select(AuditLog).where(AuditLog.action == "library.retired"))


def test_revisions_keep_history(app):
    _, user = make_workshop()
    admin = make_admin()
    from cfs.services import reopen_for_revision
    data = ecu_bytes()
    order = _completed_order(user, admin, data, "v1")
    reopen_for_revision(order, admin, "Customer asked for a change")
    deliver_uploaded_file(order, admin, "v2.bin", ecu_bytes("v2", len(data)), "Revised", True)
    files = order.completed_files
    assert [f.version for f in files] == [1, 2]
    assert [f.is_current for f in files] == [False, True]
    assert storage.path_for(files[0].storage_key).exists()


def test_admin_credit_adjustment_requires_reason(app, admin_browser):
    ws, _ = make_workshop(credits=1)
    admin_browser.post(f"/admin/workshops/{ws.id}", {"action": "adjust_credits", "delta": "3", "reason": ""})
    db.session.refresh(ws)
    assert ws.credit_balance == 1
    admin_browser.post(f"/admin/workshops/{ws.id}", {"action": "adjust_credits", "delta": "3", "reason": "Goodwill"})
    db.session.refresh(ws)
    assert ws.credit_balance == 4
    admin_browser.post(f"/admin/workshops/{ws.id}", {"action": "adjust_credits", "delta": "-10", "reason": "Mistake"})
    db.session.refresh(ws)
    assert ws.credit_balance == 4  # cannot go negative


def test_admin_activates_pending_workshop(app, admin_browser):
    ws, _ = make_workshop(status="pending")
    admin_browser.post(f"/admin/workshops/{ws.id}", {"action": "activate"})
    db.session.refresh(ws)
    assert ws.status == "active"


def test_offroad_service_needs_offroad_declaration(app):
    ws, user = make_workshop(credits=2)
    b = Browser(app)
    b.login(user.email)
    service = "DPF off (off-road / motorsport only)"
    r = upload(b, ecu_bytes(), service_requested=service)
    assert r.status_code == 400 and b"off-road declaration" in r.data
    assert db.session.scalar(select(Order)) is None
    r = upload(b, ecu_bytes(), service_requested=service, offroad_declaration="1")
    assert r.status_code == 302
    order = db.session.scalar(select(Order))
    assert order.offroad_declaration is True and order.service_requested == service


def test_normal_service_needs_no_offroad_declaration(app):
    _, user = make_workshop()
    b = Browser(app)
    b.login(user.email)
    r = upload(b, ecu_bytes(), service_requested="Pops & bangs / crackle map")
    assert r.status_code == 302
    assert db.session.scalar(select(Order)).offroad_declaration is False
