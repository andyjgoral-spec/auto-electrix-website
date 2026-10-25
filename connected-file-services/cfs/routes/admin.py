import hashlib
from datetime import timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, url_for
from sqlalchemy import func, select

from ..extensions import db
from ..models import (
    ORDER_COMPLETED, ORDER_IN_PROGRESS, ORDER_STATUSES, ORDER_SUBMITTED, AuditLog, CreditTransaction,
    Invoice, LibraryFile, Order, OrderFile, Payment, PricePackage, ServiceOption, Setting, User, Workshop,
    normalise_id, utcnow,
)
from ..security import admin_required
from ..services import (
    BusinessError, add_library_entry, add_library_from_order, approve_order, audit, change_credits,
    compatibility_check, deliver_library_file, deliver_uploaded_file, notify_workshop, reject_order,
    reopen_for_revision,
)
from ..storage import FileRejected, path_for, validate_ecu_file

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _get_or_404(model, ident):
    obj = db.session.get(model, ident)
    if obj is None:
        abort(404)
    return obj


def _pounds_to_pence(text):
    try:
        value = round(float(text.replace("£", "").replace(",", "").strip()) * 100)
    except (AttributeError, ValueError):
        raise BusinessError("Enter the price as a number, for example 70 or 70.00.")
    if value <= 0:
        raise BusinessError("Price must be more than zero.")
    return value


# --- Dashboard -------------------------------------------------------------

@bp.route("/")
@admin_required
def dashboard():
    counts = dict(db.session.execute(select(Order.status, func.count(Order.id)).group_by(Order.status)).all())
    queue = db.session.scalars(
        select(Order).where(Order.status.in_([ORDER_SUBMITTED, ORDER_IN_PROGRESS])).order_by(Order.created_at)
    ).all()
    since = utcnow() - timedelta(days=30)
    revenue_30 = db.session.scalar(
        select(func.coalesce(func.sum(Payment.net_pence), 0)).where(Payment.status == "paid", Payment.paid_at >= since)
    )
    completed_30 = db.session.scalars(
        select(Order).where(Order.status == ORDER_COMPLETED, Order.completed_at >= since)
    ).all()
    avg_turnaround = (sum(o.turnaround_hours for o in completed_30) / len(completed_30)) if completed_30 else None
    pending_workshops = db.session.scalar(select(func.count(Workshop.id)).where(Workshop.status == "pending"))
    return render_template(
        "admin/dashboard.html", counts=counts, queue=queue, revenue_30=revenue_30,
        completed_30=len(completed_30), avg_turnaround=avg_turnaround, pending_workshops=pending_workshops,
        target_hours=float(Setting.get("turnaround_target_hours")), now=utcnow(),
    )


# --- Orders ----------------------------------------------------------------

@bp.route("/orders")
@admin_required
def orders():
    status = request.args.get("status", "open")
    q = request.args.get("q", "").strip()
    query = select(Order).join(Workshop)
    if status == "open":
        query = query.where(Order.status.in_([ORDER_SUBMITTED, ORDER_IN_PROGRESS]))
    elif status in ORDER_STATUSES:
        query = query.where(Order.status == status)
    if q:
        like = f"%{q}%"
        query = query.where(
            Order.reference.ilike(like) | Workshop.name.ilike(like) | Order.vehicle_make.ilike(like)
            | Order.vehicle_model.ilike(like) | Order.hardware_number.ilike(like)
            | Order.software_number.ilike(like) | Order.registration.ilike(like)
        )
    order_by = Order.created_at if status in ("open", ORDER_SUBMITTED, ORDER_IN_PROGRESS) else Order.created_at.desc()
    rows = db.session.scalars(query.order_by(order_by).limit(300)).all()
    return render_template("admin/orders.html", orders=rows, status=status, q=q)


@bp.route("/orders/<int:order_id>")
@admin_required
def order_detail(order_id):
    order = _get_or_404(Order, order_id)
    original = order.original_file
    same_original = []
    if original:
        same_original = db.session.scalars(
            select(Order).join(OrderFile).where(
                OrderFile.kind == "original", OrderFile.sha256 == original.sha256, Order.id != order.id
            ).order_by(Order.created_at.desc()).limit(10)
        ).all()
    history = db.session.scalars(
        select(AuditLog).where(
            ((AuditLog.entity_type == "Order") & (AuditLog.entity_id == order.id))
            | ((AuditLog.entity_type == "OrderFile") & AuditLog.entity_id.in_([f.id for f in order.files] or [0]))
        ).order_by(AuditLog.created_at.desc())
    ).all()
    return render_template(
        "admin/order_detail.html", order=order, compat=compatibility_check(order),
        same_original=same_original, history=history,
    )


@bp.route("/orders/<int:order_id>/action", methods=["POST"])
@admin_required
def order_action(order_id):
    order = _get_or_404(Order, order_id)
    action = request.form.get("action")
    try:
        if action == "approve":
            approve_order(order, g.user)
            flash(f"{order.reference} approved and moved to In progress.", "success")
        elif action == "reject":
            reject_order(order, g.user, request.form.get("reason"))
            flash(f"{order.reference} rejected and the credit refunded.", "success")
        elif action == "deliver_upload":
            f = request.files.get("completed_file")
            if f is None or not f.filename:
                raise BusinessError("Choose the completed file to upload.")
            rev = deliver_uploaded_file(order, g.user, f.filename, f.read(), request.form.get("note"),
                                        bool(request.form.get("confirm")))
            flash(f"Revision v{rev.version} delivered. The workshop has been notified.", "success")
        elif action == "deliver_library":
            lib = _get_or_404(LibraryFile, request.form.get("library_file_id", type=int))
            rev = deliver_library_file(order, g.user, lib, bool(request.form.get("confirm")),
                                       request.form.get("note") or None)
            flash(f"Library file delivered as revision v{rev.version}.", "success")
        elif action == "reopen":
            reopen_for_revision(order, g.user, request.form.get("reason"))
            flash("Order reopened. Upload the new revision when ready.", "success")
        elif action == "notes":
            order.admin_notes = request.form.get("admin_notes", "").strip() or None
            audit("order.notes_updated", order)
            db.session.commit()
            flash("Internal notes saved.", "success")
        elif action == "add_to_library":
            entry = add_library_from_order(order, g.user, request.form.get("library_notes"),
                                           bool(request.form.get("confirm")))
            flash(f"Saved to the validated library as entry #{entry.id} (v{entry.version}).", "success")
        else:
            abort(400)
    except (BusinessError, FileRejected) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.order_detail", order_id=order.id))


@bp.route("/files/<int:file_id>/download")
@admin_required
def download_file(file_id):
    file = _get_or_404(OrderFile, file_id)
    audit("file.downloaded", file, order=file.order.reference, kind=file.kind, version=file.version,
          sha256=file.sha256, by_admin=True)
    db.session.commit()
    name = f"{file.order.reference}_{file.kind}_v{file.version}_{file.filename}"
    return send_file(path_for(file.storage_key), as_attachment=True, download_name=name,
                     mimetype="application/octet-stream", max_age=0)


# --- Workshops -------------------------------------------------------------

@bp.route("/workshops")
@admin_required
def workshops():
    status = request.args.get("status")
    q = request.args.get("q", "").strip()
    query = select(Workshop)
    if status in ("pending", "active", "suspended"):
        query = query.where(Workshop.status == status)
    if q:
        like = f"%{q}%"
        query = query.where(Workshop.name.ilike(like) | Workshop.contact_name.ilike(like) | Workshop.postcode.ilike(like))
    rows = db.session.scalars(query.order_by(Workshop.name)).all()
    order_counts = dict(db.session.execute(select(Order.workshop_id, func.count(Order.id)).group_by(Order.workshop_id)).all())
    return render_template("admin/workshops.html", workshops=rows, status=status, q=q, order_counts=order_counts)


@bp.route("/workshops/<int:workshop_id>", methods=["GET", "POST"])
@admin_required
def workshop_detail(workshop_id):
    ws = _get_or_404(Workshop, workshop_id)
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action in ("activate", "suspend"):
                new_status = "active" if action == "activate" else "suspended"
                old = ws.status
                ws.status = new_status
                audit("workshop.status_changed", ws, old=old, new=new_status)
                if new_status == "active" and old == "pending":
                    notify_workshop(ws, "Your workshop account has been verified. You can now buy credits and upload files.",
                                    email_subject="Your Connected File Services account is active")
                db.session.commit()
                flash(f"{ws.name} is now {new_status}.", "success")
            elif action == "adjust_credits":
                delta = request.form.get("delta", type=int)
                reason = request.form.get("reason", "").strip()
                if not delta:
                    raise BusinessError("Enter a whole number of credits to add (e.g. 2) or remove (e.g. -1).")
                if len(reason) < 5:
                    raise BusinessError("Record a reason for the credit adjustment.")
                balance = change_credits(ws, delta, "adjustment", reason, actor=g.user)
                audit("credits.adjusted", ws, delta=delta, reason=reason, balance_after=balance)
                notify_workshop(ws, f"CAT adjusted your file credits by {delta:+d}. New balance: {balance}.")
                db.session.commit()
                flash(f"Credits adjusted by {delta:+d}. New balance {balance}.", "success")
            elif action in ("disable_user", "enable_user"):
                user = _get_or_404(User, request.form.get("user_id", type=int))
                if user.workshop_id != ws.id:
                    abort(400)
                user.is_enabled = action == "enable_user"
                audit("user.enabled" if user.is_enabled else "user.disabled", user)
                db.session.commit()
                flash(f"{user.email} {'enabled' if user.is_enabled else 'disabled'}.", "success")
            else:
                abort(400)
        except BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        return redirect(url_for("admin.workshop_detail", workshop_id=ws.id))
    ledger = db.session.scalars(
        select(CreditTransaction).where(CreditTransaction.workshop_id == ws.id)
        .order_by(CreditTransaction.created_at.desc()).limit(100)
    ).all()
    payments = db.session.scalars(
        select(Payment).where(Payment.workshop_id == ws.id).order_by(Payment.created_at.desc()).limit(50)
    ).all()
    return render_template("admin/workshop_detail.html", ws=ws, ledger=ledger, payments=payments)


# --- Pricing & settings ----------------------------------------------------

@bp.route("/pricing", methods=["GET", "POST"])
@admin_required
def pricing():
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "save_package":
                pkg_id = request.form.get("package_id", type=int)
                pkg = _get_or_404(PricePackage, pkg_id) if pkg_id else PricePackage()
                name = request.form.get("name", "").strip()
                credits = request.form.get("credits", type=int)
                if not name:
                    raise BusinessError("Package name is required.")
                if not credits or credits < 1:
                    raise BusinessError("Credits must be a whole number of 1 or more.")
                before = {"name": pkg.name, "price_pence": pkg.price_pence, "credits": pkg.credits, "active": pkg.active}
                pkg.name = name[:80]
                pkg.description = request.form.get("description", "").strip()[:255] or None
                pkg.price_pence = _pounds_to_pence(request.form.get("price", ""))
                pkg.credits = credits
                pkg.active = bool(request.form.get("active"))
                pkg.sort_order = request.form.get("sort_order", type=int) or 0
                if not pkg_id:
                    db.session.add(pkg)
                db.session.flush()
                audit("pricing.package_saved", pkg, before=before,
                      after={"name": pkg.name, "price_pence": pkg.price_pence, "credits": pkg.credits, "active": pkg.active})
                db.session.commit()
                flash(f"Package “{pkg.name}” saved.", "success")
            elif action == "save_settings":
                vat = request.form.get("vat_rate", type=float)
                target = request.form.get("turnaround_target_hours", type=float)
                if vat is None or not 0 <= vat <= 50:
                    raise BusinessError("VAT rate must be between 0 and 50 percent.")
                if target is None or target <= 0:
                    raise BusinessError("Turnaround target must be more than zero hours.")
                approval = "1" if request.form.get("require_account_approval") else "0"
                Setting.set("vat_rate_bp", round(vat * 100))
                Setting.set("turnaround_target_hours", f"{target:g}")
                Setting.set("require_account_approval", approval)
                audit("settings.updated", None, vat_rate_bp=round(vat * 100), turnaround_target_hours=target,
                      require_account_approval=approval)
                db.session.commit()
                flash("Settings saved.", "success")
            elif action == "save_service":
                svc_id = request.form.get("service_id", type=int)
                svc = _get_or_404(ServiceOption, svc_id) if svc_id else ServiceOption()
                name = request.form.get("name", "").strip()
                if not name:
                    raise BusinessError("Service name is required.")
                svc.name = name[:120]
                svc.active = bool(request.form.get("active"))
                svc.sort_order = request.form.get("sort_order", type=int) or 0
                if not svc_id:
                    db.session.add(svc)
                db.session.flush()
                audit("settings.service_saved", svc, name=svc.name, active=svc.active)
                db.session.commit()
                flash("Service saved.", "success")
            else:
                abort(400)
        except BusinessError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        return redirect(url_for("admin.pricing"))
    packages = db.session.scalars(select(PricePackage).order_by(PricePackage.sort_order, PricePackage.id)).all()
    services = db.session.scalars(select(ServiceOption).order_by(ServiceOption.sort_order, ServiceOption.id)).all()
    return render_template(
        "admin/pricing.html", packages=packages, services=services,
        vat_rate=int(Setting.get("vat_rate_bp")) / 100,
        target=Setting.get("turnaround_target_hours"),
        require_approval=Setting.get("require_account_approval") == "1",
    )


# --- Reports ---------------------------------------------------------------

def _month_key(dt):
    return dt.strftime("%Y-%m")


@bp.route("/reports")
@admin_required
def reports():
    months = int(request.args.get("months", 6))
    months = max(1, min(months, 24))
    now = utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for _ in range(months - 1):
        start = (start - timedelta(days=1)).replace(day=1)
    keys = []
    cursor = start
    while cursor <= now:
        keys.append(_month_key(cursor))
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    stats = {k: {"net": 0, "gross": 0, "credits": 0, "payments": 0, "orders": 0, "completed": 0, "tat": []} for k in keys}

    for p in db.session.scalars(select(Payment).where(Payment.status == "paid", Payment.paid_at >= start)):
        s = stats.get(_month_key(p.paid_at))
        if s:
            s["net"] += p.net_pence
            s["gross"] += p.gross_pence
            s["credits"] += p.credits
            s["payments"] += 1
    for o in db.session.scalars(select(Order).where(Order.created_at >= start)):
        s = stats.get(_month_key(o.created_at))
        if s:
            s["orders"] += 1
    for o in db.session.scalars(select(Order).where(Order.completed_at >= start)):
        s = stats.get(_month_key(o.completed_at))
        if s:
            s["completed"] += 1
            s["tat"].append(o.turnaround_hours)
    rows = []
    for k in keys:
        s = stats[k]
        s["avg_tat"] = sum(s["tat"]) / len(s["tat"]) if s["tat"] else None
        rows.append((k, s))
    max_net = max([s["net"] for _, s in rows] + [1])

    totals = {
        "net": sum(s["net"] for _, s in rows), "gross": sum(s["gross"] for _, s in rows),
        "credits": sum(s["credits"] for _, s in rows), "orders": sum(s["orders"] for _, s in rows),
        "completed": sum(s["completed"] for _, s in rows),
    }
    all_tat = [t for _, s in rows for t in s["tat"]]
    totals["avg_tat"] = sum(all_tat) / len(all_tat) if all_tat else None

    top_workshops = db.session.execute(
        select(Workshop.name, func.count(Order.id).label("n"))
        .join(Order).where(Order.created_at >= start).group_by(Workshop.name).order_by(func.count(Order.id).desc()).limit(10)
    ).all()
    by_service = db.session.execute(
        select(Order.service_requested, func.count(Order.id)).where(Order.created_at >= start)
        .group_by(Order.service_requested).order_by(func.count(Order.id).desc())
    ).all()
    by_ecu = db.session.execute(
        select(Order.ecu_manufacturer, func.count(Order.id)).where(Order.created_at >= start)
        .group_by(Order.ecu_manufacturer).order_by(func.count(Order.id).desc())
    ).all()
    credits_outstanding = db.session.scalar(select(func.coalesce(func.sum(Workshop.credit_balance), 0)))
    return render_template(
        "admin/reports.html", rows=rows, totals=totals, max_net=max_net, months=months,
        top_workshops=top_workshops, by_service=by_service, by_ecu=by_ecu,
        credits_outstanding=credits_outstanding,
        target_hours=float(Setting.get("turnaround_target_hours")),
    )


@bp.route("/payments")
@admin_required
def payments():
    rows = db.session.scalars(select(Payment).order_by(Payment.created_at.desc()).limit(200)).all()
    return render_template("admin/payments.html", payments=rows)


# --- Validated file library -----------------------------------------------

LIBRARY_FIELDS = ["vehicle_make", "vehicle_model", "engine", "ecu_manufacturer", "ecu_model",
                  "hardware_number", "software_number", "service", "notes"]


@bp.route("/library")
@admin_required
def library():
    q = request.args.get("q", "").strip()
    show = request.args.get("show", "validated")
    query = select(LibraryFile)
    if show != "all":
        query = query.where(LibraryFile.status == "validated")
    if q:
        like = f"%{q}%"
        key = f"%{normalise_id(q)}%"
        query = query.where(
            LibraryFile.vehicle_make.ilike(like) | LibraryFile.vehicle_model.ilike(like)
            | LibraryFile.ecu_model.ilike(like) | LibraryFile.hardware_key.ilike(key)
            | LibraryFile.software_key.ilike(key) | LibraryFile.original_sha256.ilike(like)
        )
    rows = db.session.scalars(query.order_by(LibraryFile.vehicle_make, LibraryFile.vehicle_model, LibraryFile.version.desc())).all()
    services = db.session.scalars(select(ServiceOption).order_by(ServiceOption.sort_order)).all()
    return render_template("admin/library.html", entries=rows, q=q, show=show, services=services, form={})


@bp.route("/library/new", methods=["POST"])
@admin_required
def library_new():
    form = request.form
    original = request.files.get("original_file")
    calibrated = request.files.get("calibrated_file")
    try:
        fields = {k: (form.get(k, "").strip() or None) for k in LIBRARY_FIELDS}
        for k in ("vehicle_make", "vehicle_model", "ecu_manufacturer", "ecu_model", "hardware_number",
                  "software_number", "service"):
            if not fields[k]:
                raise BusinessError("Fill in all required vehicle, ECU and service fields.")
        if not original or not original.filename or not calibrated or not calibrated.filename:
            raise BusinessError("Upload both the unmodified original and the validated calibration file.")
        original_bytes = original.read()
        validate_ecu_file(original.filename, original_bytes)
        entry = add_library_entry(
            g.user, fields, hashlib.sha256(original_bytes).hexdigest(), len(original_bytes),
            calibrated.filename, calibrated.read(), bool(form.get("confirm")),
        )
    except (BusinessError, FileRejected) as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("admin.library"))
    flash(f"Library entry #{entry.id} v{entry.version} added.", "success")
    return redirect(url_for("admin.library_detail", entry_id=entry.id))


@bp.route("/library/<int:entry_id>", methods=["GET", "POST"])
@admin_required
def library_detail(entry_id):
    entry = _get_or_404(LibraryFile, entry_id)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "retire" and entry.status == "validated":
            entry.status = "retired"
            audit("library.retired", entry, reason=request.form.get("reason", "").strip() or "Retired by admin")
            db.session.commit()
            flash("Library entry retired. It will no longer be offered as a match.", "success")
        elif action == "download":
            audit("library.downloaded", entry, sha256=entry.sha256)
            db.session.commit()
            return send_file(path_for(entry.storage_key), as_attachment=True,
                             download_name=f"LIB{entry.id}_v{entry.version}_{entry.filename}",
                             mimetype="application/octet-stream", max_age=0)
        return redirect(url_for("admin.library_detail", entry_id=entry.id))
    versions = db.session.scalars(
        select(LibraryFile).where(LibraryFile.original_sha256 == entry.original_sha256, LibraryFile.service == entry.service)
        .order_by(LibraryFile.version.desc())
    ).all()
    used_in = db.session.scalars(select(OrderFile).where(OrderFile.library_file_id == entry.id)).all()
    history = db.session.scalars(
        select(AuditLog).where(AuditLog.entity_type == "LibraryFile", AuditLog.entity_id == entry.id)
        .order_by(AuditLog.created_at.desc())
    ).all()
    return render_template("admin/library_detail.html", entry=entry, versions=versions, used_in=used_in, history=history)


# --- Audit log -------------------------------------------------------------

@bp.route("/audit")
@admin_required
def audit_log():
    action = request.args.get("action", "").strip()
    entity = request.args.get("entity", "").strip()
    actor = request.args.get("actor", "").strip()
    page = max(request.args.get("page", 1, type=int), 1)
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action.ilike(f"{action}%"))
    if entity:
        query = query.where(AuditLog.entity_type == entity)
    if actor:
        query = query.where(AuditLog.actor_email.ilike(f"%{actor}%"))
    per_page = 100
    rows = db.session.scalars(query.order_by(AuditLog.id.desc()).offset((page - 1) * per_page).limit(per_page + 1)).all()
    actions = [a for (a,) in db.session.execute(select(AuditLog.action).distinct().order_by(AuditLog.action))]
    return render_template("admin/audit.html", rows=rows[:per_page], has_more=len(rows) > per_page, page=page,
                           action=action, entity=entity, actor=actor, actions=actions)


@bp.route("/invoices")
@admin_required
def invoices():
    rows = db.session.scalars(select(Invoice).order_by(Invoice.issued_at.desc()).limit(300)).all()
    return render_template("admin/invoices.html", invoices=rows)
