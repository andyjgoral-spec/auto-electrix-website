"""Business rules: credits, orders, payments, invoices, library matching, audit."""
import json
import logging
import smtplib
from email.message import EmailMessage

from flask import current_app, g, has_request_context, request
from sqlalchemy import func, select

from .extensions import db
from .models import (
    ORDER_COMPLETED, ORDER_IN_PROGRESS, ORDER_REJECTED, ORDER_SUBMITTED, ROLE_ADMIN,
    AuditLog, CreditTransaction, Invoice, LibraryFile, Notification, Order, OrderFile,
    Payment, Setting, User, Workshop, normalise_id, utcnow,
)
from . import storage

log = logging.getLogger("cfs")


class BusinessError(ValueError):
    pass


# --- Audit -----------------------------------------------------------------

def audit(action, entity=None, actor=None, **details):
    if actor is None and has_request_context():
        actor = g.get("user")
    ip = None
    if has_request_context():
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    db.session.add(AuditLog(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else "system",
        action=action,
        entity_type=entity.__class__.__name__ if entity is not None else None,
        entity_id=getattr(entity, "id", None),
        details=json.dumps(details, default=str) if details else None,
        ip_address=ip,
    ))


# --- Money -----------------------------------------------------------------

def vat_rate_bp():
    return int(Setting.get("vat_rate_bp"))


def price_breakdown(net_pence, rate_bp=None):
    rate_bp = vat_rate_bp() if rate_bp is None else rate_bp
    vat = (net_pence * rate_bp + 5000) // 10000  # round half up to the penny
    return {"net": net_pence, "vat": vat, "gross": net_pence + vat, "rate_bp": rate_bp}


def money(pence):
    return f"£{pence / 100:,.2f}"


# --- Notifications ---------------------------------------------------------

def send_email(to, subject, body):
    cfg = current_app.config
    if not cfg.get("SMTP_HOST"):
        log.info("EMAIL (not sent, SMTP not configured) to=%s subject=%s\n%s", to, subject, body)
        return
    msg = EmailMessage()
    msg["From"] = cfg["MAIL_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=10) as smtp:
            smtp.starttls()
            if cfg.get("SMTP_USER"):
                smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException):
        log.exception("Email to %s failed", to)


def notify_workshop(workshop, message, order=None, email_subject=None):
    for user in workshop.users:
        if not user.is_enabled:
            continue
        db.session.add(Notification(user_id=user.id, order=order, message=message))
        if email_subject:
            send_email(user.email, email_subject, f"Hello {user.name},\n\n{message}\n\nConnected File Services")


def notify_admins(message, order=None):
    for admin in db.session.scalars(select(User).where(User.role == ROLE_ADMIN, User.is_enabled.is_(True))):
        db.session.add(Notification(user_id=admin.id, order=order, message=message))


# --- Credits ---------------------------------------------------------------

def _lock_workshop(workshop_id):
    # Row lock on PostgreSQL so two simultaneous orders cannot overspend credits.
    return db.session.execute(
        select(Workshop).where(Workshop.id == workshop_id).with_for_update()
    ).scalar_one()


def change_credits(workshop, delta, kind, reason, order=None, payment=None, actor=None):
    ws = _lock_workshop(workshop.id)
    if ws.credit_balance + delta < 0:
        raise BusinessError("Not enough file credits.")
    ws.credit_balance += delta
    db.session.add(CreditTransaction(
        workshop_id=ws.id, delta=delta, balance_after=ws.credit_balance, kind=kind, reason=reason,
        order_id=order.id if order else None, payment_id=payment.id if payment else None,
        created_by_id=actor.id if actor else None,
    ))
    return ws.credit_balance


# --- Orders ----------------------------------------------------------------

def create_order(user, fields, filename, data):
    workshop = user.workshop
    if workshop is None or not workshop.is_active:
        raise BusinessError("Your workshop account is not active yet, so files cannot be uploaded.")
    if workshop.credit_balance < 1:
        raise BusinessError("You have no file credits left. Buy credits to upload a file.")
    safe_name = storage.validate_ecu_file(filename, data)

    order = Order(workshop=workshop, submitted_by=user, status=ORDER_SUBMITTED, credits_charged=1, **fields)
    db.session.add(order)
    db.session.flush()
    order.reference = f"CFS-{order.id + 100000}"
    change_credits(workshop, -1, "order", f"File service {order.reference}", order=order, actor=user)

    stored = storage.save_bytes(data, safe_name, "originals")
    original = OrderFile(order=order, kind="original", version=1, uploaded_by=user, **stored.__dict__)
    db.session.add(original)
    db.session.flush()
    audit("order.submitted", order, actor=user, reference=order.reference, sha256=stored.sha256, size=stored.size,
          service=order.service_requested, offroad_declaration=order.offroad_declaration)
    audit("file.uploaded", original, actor=user, order=order.reference, kind="original", sha256=stored.sha256)
    notify_admins(f"New order {order.reference} from {workshop.name}", order=order)
    db.session.commit()
    return order


def approve_order(order, admin):
    if order.status != ORDER_SUBMITTED:
        raise BusinessError("Only orders awaiting review can be approved.")
    order.status = ORDER_IN_PROGRESS
    order.approved_at = utcnow()
    order.approved_by = admin
    audit("order.approved", order, actor=admin, reference=order.reference)
    notify_workshop(order.workshop, f"Order {order.reference} has been accepted and is being worked on.",
                    order=order, email_subject=f"{order.reference} accepted")
    db.session.commit()


def reject_order(order, admin, reason):
    if not order.is_open:
        raise BusinessError("This order is already closed.")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise BusinessError("Please give the workshop a reason for the rejection.")
    order.status = ORDER_REJECTED
    order.rejected_at = utcnow()
    order.rejection_reason = reason
    change_credits(order.workshop, order.credits_charged, "refund",
                   f"Refund for rejected order {order.reference}", order=order, actor=admin)
    audit("order.rejected", order, actor=admin, reference=order.reference, reason=reason,
          credits_refunded=order.credits_charged)
    notify_workshop(order.workshop,
                    f"Order {order.reference} was rejected: {reason}. Your file credit has been returned.",
                    order=order, email_subject=f"{order.reference} rejected")
    db.session.commit()


def _add_completed_file(order, admin, stored, note, library_file=None):
    if order.status != ORDER_IN_PROGRESS:
        raise BusinessError("Approve the order before delivering a file.")
    previous = order.completed_files
    for f in previous:
        f.is_current = False
    revision = OrderFile(
        order=order, kind="completed", version=len(previous) + 1, is_current=True,
        uploaded_by=admin, approved_by=admin, note=(note or "").strip()[:255] or None,
        library_file=library_file, **stored.__dict__,
    )
    db.session.add(revision)
    db.session.flush()
    order.status = ORDER_COMPLETED
    order.completed_at = utcnow()
    return revision


def deliver_uploaded_file(order, admin, filename, data, note, confirmed):
    if not confirmed:
        raise BusinessError("Tick the box to confirm you have checked and approved this file for delivery.")
    safe_name = storage.validate_ecu_file(filename, data)
    original = order.original_file
    if original and len(data) != original.size:
        raise BusinessError(
            f"The completed file is {len(data):,} bytes but the original is {original.size:,} bytes. "
            "Sizes must match exactly. Check you exported the full file."
        )
    stored = storage.save_bytes(data, safe_name, "completed")
    revision = _add_completed_file(order, admin, stored, note)
    audit("file.delivered", revision, actor=admin, order=order.reference, version=revision.version,
          sha256=stored.sha256, approved=True)
    _notify_completed(order, revision)
    db.session.commit()
    return revision


def reopen_for_revision(order, admin, reason):
    """Let an admin issue a new revision after an order was completed."""
    if order.status != ORDER_COMPLETED:
        raise BusinessError("Only completed orders can be reopened for a revision.")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise BusinessError("Please record why a revision is needed.")
    order.status = ORDER_IN_PROGRESS
    order.completed_at = None
    audit("order.reopened", order, actor=admin, reference=order.reference, reason=reason)
    db.session.commit()


def _notify_completed(order, revision):
    label = "is ready to download" if revision.version == 1 else f"has a new revision (v{revision.version}) ready"
    notify_workshop(order.workshop, f"Order {order.reference} {label}.", order=order,
                    email_subject=f"{order.reference} ready to download")


def record_download(file, user):
    file.download_count += 1
    audit("file.downloaded", file, actor=user, order=file.order.reference, kind=file.kind,
          version=file.version, sha256=file.sha256)
    db.session.commit()


# --- Validated file library ------------------------------------------------

def compatibility_check(order):
    """Find library files built from exactly this original.

    A library file is only offered as compatible when ECU manufacturer,
    hardware number, software number, file size and SHA-256 checksum of the
    original all match. Near matches are listed separately as warnings and
    can never be delivered.
    """
    original = order.original_file
    if original is None:
        return {"exact": [], "near": []}
    hw, sw = normalise_id(order.hardware_number), normalise_id(order.software_number)
    candidates = db.session.scalars(
        select(LibraryFile).where(
            LibraryFile.status == "validated",
            (LibraryFile.original_sha256 == original.sha256)
            | ((LibraryFile.hardware_key == hw) & (LibraryFile.software_key == sw)),
        ).order_by(LibraryFile.service, LibraryFile.version.desc())
    ).all()
    exact, near = [], []
    for lib in candidates:
        problems = []
        if lib.ecu_manufacturer.strip().lower() != order.ecu_manufacturer.strip().lower():
            problems.append("ECU manufacturer differs")
        if lib.hardware_key != hw:
            problems.append("hardware number differs")
        if lib.software_key != sw:
            problems.append("software number differs")
        if lib.original_size != original.size:
            problems.append("original file size differs")
        if lib.original_sha256 != original.sha256:
            problems.append("original file checksum differs")
        if problems:
            near.append((lib, problems))
        else:
            exact.append(lib)
    return {"exact": exact, "near": near}


def deliver_library_file(order, admin, library_file, confirmed, note=None):
    if not confirmed:
        raise BusinessError("Tick the box to confirm you have reviewed this library file for this order.")
    if library_file not in compatibility_check(order)["exact"]:
        raise BusinessError("That library file is not an exact match for this order's original file.")
    if storage.sha256_of(library_file.storage_key) != library_file.sha256:
        raise BusinessError("The stored library file failed its integrity check. It has not been delivered.")
    stored = storage.copy_stored(library_file.storage_key, library_file.filename, "completed")
    revision = _add_completed_file(
        order, admin, stored, note or f"Validated library file #{library_file.id} v{library_file.version}",
        library_file=library_file,
    )
    library_file.times_used += 1
    audit("file.delivered", revision, actor=admin, order=order.reference, version=revision.version,
          sha256=stored.sha256, library_file_id=library_file.id, library_version=library_file.version,
          approved=True)
    _notify_completed(order, revision)
    db.session.commit()
    return revision


def _latest_library_version(original_sha256, service):
    previous = db.session.scalars(
        select(LibraryFile).where(LibraryFile.original_sha256 == original_sha256, LibraryFile.service == service)
        .order_by(LibraryFile.version.desc())
    ).first()
    return previous


def add_library_entry(admin, fields, original_sha256, original_size, filename, data, confirmed, source_order=None):
    if not confirmed:
        raise BusinessError("Tick the box to confirm this file has been validated by CAT staff.")
    safe_name = storage.validate_ecu_file(filename, data)
    if len(data) != original_size:
        raise BusinessError("The calibration file must be exactly the same size as the original it was built from.")
    previous = _latest_library_version(original_sha256, fields["service"])
    stored = storage.save_bytes(data, safe_name, "library")
    entry = LibraryFile(
        **fields,
        hardware_key=normalise_id(fields["hardware_number"]),
        software_key=normalise_id(fields["software_number"]),
        original_sha256=original_sha256, original_size=original_size,
        storage_key=stored.storage_key, filename=stored.filename, size=stored.size, sha256=stored.sha256,
        version=(previous.version + 1) if previous else 1, supersedes=previous,
        validated_by=admin, source_order=source_order,
    )
    if previous and previous.status == "validated":
        previous.status = "retired"
        audit("library.retired", previous, actor=admin, reason="Superseded by new version")
    db.session.add(entry)
    db.session.flush()
    audit("library.added", entry, actor=admin, version=entry.version, sha256=entry.sha256,
          original_sha256=original_sha256, source_order=source_order.reference if source_order else None)
    db.session.commit()
    return entry


def add_library_from_order(order, admin, notes, confirmed):
    completed = order.current_completed_file
    original = order.original_file
    if order.status != ORDER_COMPLETED or completed is None or original is None:
        raise BusinessError("Only completed orders with a delivered file can be added to the library.")
    with open(storage.path_for(completed.storage_key), "rb") as fh:
        data = fh.read()
    fields = dict(
        vehicle_make=order.vehicle_make, vehicle_model=order.vehicle_model, engine=order.engine,
        ecu_manufacturer=order.ecu_manufacturer, ecu_model=order.ecu_model,
        hardware_number=order.hardware_number, software_number=order.software_number,
        service=order.service_requested, notes=notes,
    )
    return add_library_entry(admin, fields, original.sha256, original.size, completed.filename, data,
                             confirmed, source_order=order)


# --- Payments --------------------------------------------------------------

def start_payment(user, package, provider):
    workshop = user.workshop
    if workshop is None or not workshop.is_active:
        raise BusinessError("Your workshop account must be active before buying credits.")
    if not package.active:
        raise BusinessError("That package is no longer available.")
    amounts = price_breakdown(package.price_pence)
    payment = Payment(
        workshop=workshop, user_id=user.id, package_id=package.id, package_name=package.name,
        credits=package.credits, net_pence=amounts["net"], vat_pence=amounts["vat"],
        gross_pence=amounts["gross"], vat_rate_bp=amounts["rate_bp"], provider=provider,
    )
    db.session.add(payment)
    db.session.flush()
    audit("payment.started", payment, actor=user, package=package.name, gross=amounts["gross"], provider=provider)
    return payment


def fulfil_payment(payment, payment_intent=None, actor=None):
    """Mark a payment paid, add credits and issue the invoice. Safe to call twice."""
    payment = db.session.execute(
        select(Payment).where(Payment.id == payment.id).with_for_update()
    ).scalar_one()
    if payment.status == "paid":
        return payment
    payment.status = "paid"
    payment.paid_at = utcnow()
    if payment_intent:
        payment.stripe_payment_intent = payment_intent
    change_credits(payment.workshop, payment.credits, "purchase",
                   f"Purchased {payment.package_name}", payment=payment, actor=actor)
    invoice = issue_invoice(payment)
    audit("payment.paid", payment, actor=actor, credits=payment.credits, gross=payment.gross_pence,
          invoice=invoice.number, provider=payment.provider)
    notify_workshop(payment.workshop,
                    f"Payment received: {payment.credits} file credits added. Invoice {invoice.number}.")
    db.session.commit()
    return payment


def issue_invoice(payment):
    ws = payment.workshop
    bill_to = "\n".join(filter(None, [
        ws.name, ws.contact_name, ws.address, " ".join(filter(None, [ws.city, ws.postcode])), ws.country,
        f"VAT number: {ws.vat_number}" if ws.vat_number else None,
    ]))
    invoice = Invoice(
        number=f"PENDING-{payment.id}", workshop=ws, payment=payment,
        description=f"{payment.package_name}: {payment.credits} ECU file service credit(s)",
        net_pence=payment.net_pence, vat_pence=payment.vat_pence, gross_pence=payment.gross_pence,
        vat_rate_bp=payment.vat_rate_bp, bill_to=bill_to,
    )
    db.session.add(invoice)
    db.session.flush()
    # Numbered from the database id, so numbers are unique and sequential.
    invoice.number = f"CFS-INV-{invoice.id:06d}"
    return invoice


def fail_payment(payment, reason):
    if payment.status == "pending":
        payment.status = "failed"
        audit("payment.failed", payment, reason=reason)
        db.session.commit()
