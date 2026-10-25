from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, url_for
from sqlalchemy import func, select

from ..extensions import db
from ..models import (
    ORDER_COMPLETED, ORDER_IN_PROGRESS, ORDER_STATUSES, ORDER_SUBMITTED, CreditTransaction, Invoice,
    Notification, Order, OrderFile, PricePackage, ServiceOption, Workshop,
)
from ..security import customer_required, login_required
from ..services import BusinessError, audit, create_order, price_breakdown, record_download
from ..storage import FileRejected, path_for

bp = Blueprint("customer", __name__, url_prefix="/workshop")

ECU_MANUFACTURERS = ["Bosch", "Continental", "Delphi", "Denso", "Marelli", "Siemens", "Visteon", "Valeo",
                     "Hitachi", "Mitsubishi", "Kefico", "Transtron", "Other"]
READ_METHODS = ["OBD", "Bench", "Boot", "BDM", "JTAG", "Other"]

ORDER_FIELDS = {
    "vehicle_make": ("Vehicle make", 60, True), "vehicle_model": ("Vehicle model", 80, True),
    "vehicle_year": ("Year", 10, False), "engine": ("Engine", 80, False),
    "registration": ("Registration", 20, False), "vin": ("VIN", 17, False),
    "ecu_manufacturer": ("ECU manufacturer", 60, True), "ecu_model": ("ECU model", 60, True),
    "hardware_number": ("Hardware number", 60, True), "software_number": ("Software number", 60, True),
    "read_tool": ("Read tool", 60, False), "read_method": ("Read method", 30, False),
    "service_requested": ("Service requested", 120, True), "customer_notes": ("Notes", 2000, False),
}


def _workshop_order_or_404(order_id):
    order = db.session.get(Order, order_id)
    if order is None or order.workshop_id != g.user.workshop_id:
        abort(404)
    return order


@bp.route("/")
@customer_required
def dashboard():
    ws = g.user.workshop
    counts = dict(db.session.execute(
        select(Order.status, func.count(Order.id)).where(Order.workshop_id == ws.id).group_by(Order.status)
    ).all())
    recent = db.session.scalars(
        select(Order).where(Order.workshop_id == ws.id).order_by(Order.created_at.desc()).limit(6)
    ).all()
    notes = db.session.scalars(
        select(Notification).where(Notification.user_id == g.user.id).order_by(Notification.created_at.desc()).limit(5)
    ).all()
    return render_template(
        "customer/dashboard.html", ws=ws, recent=recent, notifications=notes,
        active_count=counts.get(ORDER_SUBMITTED, 0) + counts.get(ORDER_IN_PROGRESS, 0),
        completed_count=counts.get(ORDER_COMPLETED, 0),
    )


@bp.route("/upload", methods=["GET", "POST"])
@customer_required
def upload():
    ws = g.user.workshop
    services = db.session.scalars(
        select(ServiceOption).where(ServiceOption.active.is_(True)).order_by(ServiceOption.sort_order)
    ).all()
    form = request.form
    if request.method == "POST":
        errors, fields = [], {}
        for key, (label, max_len, required) in ORDER_FIELDS.items():
            value = form.get(key, "").strip()
            if required and not value:
                errors.append(f"{label} is required.")
            if len(value) > max_len:
                errors.append(f"{label} must be {max_len} characters or fewer.")
            fields[key] = value or None
        if fields["vin"]:
            fields["vin"] = fields["vin"].upper()
            if len(fields["vin"]) != 17:
                errors.append("A VIN is 17 characters. Leave it blank if you do not have it.")
        chosen = next((s for s in services if s.name == fields["service_requested"]), None)
        if fields["service_requested"] and chosen is None:
            errors.append("Choose a service from the list.")
        if chosen and chosen.offroad_only and not form.get("offroad_declaration"):
            errors.append(f"“{chosen.name}” is for off-road or motorsport use only. Please confirm the off-road declaration.")
        if not form.get("declaration"):
            errors.append("Please confirm the declaration about this file.")
        upload_file = request.files.get("ecu_file")
        if upload_file is None or not upload_file.filename:
            errors.append("Choose the original ECU file to upload.")
        if not errors:
            fields["declaration_accepted"] = True
            fields["offroad_declaration"] = bool(chosen and chosen.offroad_only)
            try:
                order = create_order(g.user, fields, upload_file.filename, upload_file.read())
            except (FileRejected, BusinessError) as exc:
                db.session.rollback()
                errors.append(str(exc))
            else:
                flash(f"Order {order.reference} submitted. One file credit has been used.", "success")
                return redirect(url_for("customer.order_detail", order_id=order.id))
        for e in errors:
            flash(e, "error")
        return render_template("customer/upload.html", ws=ws, services=services, form=form,
                               manufacturers=ECU_MANUFACTURERS, read_methods=READ_METHODS), 400
    return render_template("customer/upload.html", ws=ws, services=services, form={},
                           manufacturers=ECU_MANUFACTURERS, read_methods=READ_METHODS)


@bp.route("/orders")
@customer_required
def orders():
    status = request.args.get("status")
    query = select(Order).where(Order.workshop_id == g.user.workshop_id)
    if status in ORDER_STATUSES:
        query = query.where(Order.status == status)
    elif status == "active":
        query = query.where(Order.status.in_([ORDER_SUBMITTED, ORDER_IN_PROGRESS]))
    q = request.args.get("q", "").strip()
    if q:
        like = f"%{q}%"
        query = query.where(Order.reference.ilike(like) | Order.vehicle_make.ilike(like) |
                            Order.vehicle_model.ilike(like) | Order.registration.ilike(like))
    rows = db.session.scalars(query.order_by(Order.created_at.desc()).limit(200)).all()
    return render_template("customer/orders.html", orders=rows, status=status, q=q)


@bp.route("/orders/<int:order_id>")
@customer_required
def order_detail(order_id):
    order = _workshop_order_or_404(order_id)
    # Viewing an order marks its notifications as read.
    for n in db.session.scalars(select(Notification).where(
            Notification.user_id == g.user.id, Notification.order_id == order.id, Notification.is_read.is_(False))):
        n.is_read = True
    db.session.commit()
    return render_template("customer/order_detail.html", order=order)


@bp.route("/files/<int:file_id>/download")
@customer_required
def download(file_id):
    file = db.session.get(OrderFile, file_id)
    if file is None or file.order.workshop_id != g.user.workshop_id:
        abort(404)
    # Workshops may download their own original, and the current completed revision.
    if file.kind == "completed" and not (file.is_current and file.order.status == ORDER_COMPLETED):
        abort(404)
    record_download(file, g.user)
    return send_file(path_for(file.storage_key), as_attachment=True, download_name=file.filename,
                     mimetype="application/octet-stream", max_age=0)


@bp.route("/credits")
@customer_required
def credits():
    packages = db.session.scalars(
        select(PricePackage).where(PricePackage.active.is_(True)).order_by(PricePackage.sort_order)
    ).all()
    priced = [(p, price_breakdown(p.price_pence)) for p in packages]
    ledger = db.session.scalars(
        select(CreditTransaction).where(CreditTransaction.workshop_id == g.user.workshop_id)
        .order_by(CreditTransaction.created_at.desc()).limit(50)
    ).all()
    return render_template("customer/credits.html", ws=g.user.workshop, packages=priced, ledger=ledger)


@bp.route("/invoices")
@customer_required
def invoices():
    rows = db.session.scalars(
        select(Invoice).where(Invoice.workshop_id == g.user.workshop_id).order_by(Invoice.issued_at.desc())
    ).all()
    return render_template("customer/invoices.html", invoices=rows)


@bp.route("/invoices/<int:invoice_id>")
@login_required
def invoice_detail(invoice_id):
    invoice = db.session.get(Invoice, invoice_id)
    if invoice is None or (not g.user.is_admin and invoice.workshop_id != g.user.workshop_id):
        abort(404)
    return render_template("customer/invoice_detail.html", invoice=invoice)


@bp.route("/notifications", methods=["GET", "POST"])
@login_required
def notifications():
    if request.method == "POST":
        for n in db.session.scalars(select(Notification).where(
                Notification.user_id == g.user.id, Notification.is_read.is_(False))):
            n.is_read = True
        db.session.commit()
        return redirect(url_for("customer.notifications"))
    rows = db.session.scalars(
        select(Notification).where(Notification.user_id == g.user.id)
        .order_by(Notification.created_at.desc()).limit(100)
    ).all()
    return render_template("customer/notifications.html", notifications=rows)


@bp.route("/account", methods=["GET", "POST"])
@customer_required
def account():
    ws: Workshop = g.user.workshop
    if request.method == "POST":
        changes = {}
        for key in ("contact_name", "phone", "address", "city", "postcode", "country", "vat_number"):
            value = request.form.get(key, "").strip()[:255]
            if key != "vat_number" and not value:
                flash("Please fill in all required fields.", "error")
                return render_template("customer/account.html", ws=ws), 400
            if getattr(ws, key) != (value or None):
                changes[key] = value
                setattr(ws, key, value or None)
        if changes:
            audit("workshop.updated", ws, fields=sorted(changes))
        db.session.commit()
        flash("Workshop details saved.", "success")
        return redirect(url_for("customer.account"))
    return render_template("customer/account.html", ws=ws)
