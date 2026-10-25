import json
import re
from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalise_id(value):
    """Normalise a hardware/software identifier for exact comparison.

    Only whitespace, dashes, dots and letter case are ignored. Every other
    character must match exactly.
    """
    return re.sub(r"[\s\-.]", "", (value or "")).upper()


ROLE_CUSTOMER = "customer"
ROLE_ADMIN = "admin"

ORDER_SUBMITTED = "submitted"
ORDER_IN_PROGRESS = "in_progress"
ORDER_COMPLETED = "completed"
ORDER_REJECTED = "rejected"
ORDER_STATUSES = [ORDER_SUBMITTED, ORDER_IN_PROGRESS, ORDER_COMPLETED, ORDER_REJECTED]
ORDER_STATUS_LABELS = {
    ORDER_SUBMITTED: "Awaiting review",
    ORDER_IN_PROGRESS: "In progress",
    ORDER_COMPLETED: "Completed",
    ORDER_REJECTED: "Rejected",
}


class Workshop(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    contact_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(40))
    address = db.Column(db.String(255))
    city = db.Column(db.String(120))
    postcode = db.Column(db.String(20))
    country = db.Column(db.String(80), default="United Kingdom")
    vat_number = db.Column(db.String(40))
    credit_balance = db.Column(db.Integer, nullable=False, default=0)
    # pending -> active, or suspended
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    users = db.relationship("User", back_populates="workshop")
    orders = db.relationship("Order", back_populates="workshop", order_by="Order.created_at.desc()")

    @property
    def is_active(self):
        return self.status == "active"


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_CUSTOMER)
    workshop_id = db.Column(db.Integer, db.ForeignKey("workshop.id"))
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)
    failed_logins = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime)
    last_login_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    workshop = db.relationship("Workshop", back_populates="users")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == ROLE_ADMIN


class CreditTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    workshop_id = db.Column(db.Integer, db.ForeignKey("workshop.id"), nullable=False, index=True)
    delta = db.Column(db.Integer, nullable=False)
    balance_after = db.Column(db.Integer, nullable=False)
    # purchase | order | refund | adjustment
    kind = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"))
    payment_id = db.Column(db.Integer, db.ForeignKey("payment.id"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    created_by = db.relationship("User")


class PricePackage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(255))
    price_pence = db.Column(db.Integer, nullable=False)  # excluding VAT
    credits = db.Column(db.Integer, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Setting(db.Model):
    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.String(255), nullable=False)

    DEFAULTS = {
        "vat_rate_bp": "2000",  # 20.00% in basis points
        "require_account_approval": "1",
        "turnaround_target_hours": "4",
    }

    @classmethod
    def get(cls, key):
        row = db.session.get(cls, key)
        return row.value if row else cls.DEFAULTS.get(key)

    @classmethod
    def set(cls, key, value):
        row = db.session.get(cls, key)
        if row:
            row.value = str(value)
        else:
            db.session.add(cls(key=key, value=str(value)))


class ServiceOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    workshop_id = db.Column(db.Integer, db.ForeignKey("workshop.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    package_id = db.Column(db.Integer, db.ForeignKey("price_package.id"))
    package_name = db.Column(db.String(80), nullable=False)
    credits = db.Column(db.Integer, nullable=False)
    net_pence = db.Column(db.Integer, nullable=False)
    vat_pence = db.Column(db.Integer, nullable=False)
    gross_pence = db.Column(db.Integer, nullable=False)
    vat_rate_bp = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="gbp")
    # stripe | demo
    provider = db.Column(db.String(20), nullable=False)
    # pending | paid | failed
    status = db.Column(db.String(20), nullable=False, default="pending")
    stripe_session_id = db.Column(db.String(255), unique=True)
    stripe_payment_intent = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    paid_at = db.Column(db.DateTime)

    workshop = db.relationship("Workshop")
    invoice = db.relationship("Invoice", back_populates="payment", uselist=False)


class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(30), unique=True, nullable=False)
    workshop_id = db.Column(db.Integer, db.ForeignKey("workshop.id"), nullable=False, index=True)
    payment_id = db.Column(db.Integer, db.ForeignKey("payment.id"), nullable=False, unique=True)
    description = db.Column(db.String(255), nullable=False)
    net_pence = db.Column(db.Integer, nullable=False)
    vat_pence = db.Column(db.Integer, nullable=False)
    gross_pence = db.Column(db.Integer, nullable=False)
    vat_rate_bp = db.Column(db.Integer, nullable=False)
    # Billing details are copied so the invoice never changes afterwards.
    bill_to = db.Column(db.Text, nullable=False)
    issued_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    workshop = db.relationship("Workshop")
    payment = db.relationship("Payment", back_populates="invoice")


class StripeEvent(db.Model):
    """Stripe events already processed, so a replayed webhook is ignored."""

    id = db.Column(db.String(255), primary_key=True)
    type = db.Column(db.String(120), nullable=False)
    received_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(20), unique=True)
    workshop_id = db.Column(db.Integer, db.ForeignKey("workshop.id"), nullable=False, index=True)
    submitted_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default=ORDER_SUBMITTED, index=True)

    vehicle_make = db.Column(db.String(60), nullable=False)
    vehicle_model = db.Column(db.String(80), nullable=False)
    vehicle_year = db.Column(db.String(10))
    engine = db.Column(db.String(80))
    registration = db.Column(db.String(20))
    vin = db.Column(db.String(17))
    ecu_manufacturer = db.Column(db.String(60), nullable=False)
    ecu_model = db.Column(db.String(60), nullable=False)
    hardware_number = db.Column(db.String(60), nullable=False)
    software_number = db.Column(db.String(60), nullable=False)
    read_tool = db.Column(db.String(60))
    read_method = db.Column(db.String(30))
    service_requested = db.Column(db.String(120), nullable=False)
    customer_notes = db.Column(db.Text)
    declaration_accepted = db.Column(db.Boolean, nullable=False, default=False)

    credits_charged = db.Column(db.Integer, nullable=False, default=1)
    admin_notes = db.Column(db.Text)
    rejection_reason = db.Column(db.Text)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    approved_at = db.Column(db.DateTime)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    completed_at = db.Column(db.DateTime)
    rejected_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    workshop = db.relationship("Workshop", back_populates="orders")
    submitted_by = db.relationship("User", foreign_keys=[submitted_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])
    files = db.relationship("OrderFile", back_populates="order", order_by="OrderFile.created_at")

    @property
    def status_label(self):
        return ORDER_STATUS_LABELS.get(self.status, self.status)

    @property
    def is_open(self):
        return self.status in (ORDER_SUBMITTED, ORDER_IN_PROGRESS)

    @property
    def original_file(self):
        return next((f for f in self.files if f.kind == "original"), None)

    @property
    def completed_files(self):
        return [f for f in self.files if f.kind == "completed"]

    @property
    def current_completed_file(self):
        return next((f for f in reversed(self.files) if f.kind == "completed" and f.is_current), None)

    @property
    def turnaround_hours(self):
        if self.completed_at:
            return (self.completed_at - self.created_at).total_seconds() / 3600
        return None


class OrderFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False, index=True)
    # original | completed
    kind = db.Column(db.String(20), nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    is_current = db.Column(db.Boolean, nullable=False, default=True)
    storage_key = db.Column(db.String(255), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False, index=True)
    note = db.Column(db.String(255))
    library_file_id = db.Column(db.Integer, db.ForeignKey("library_file.id"))
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    download_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    order = db.relationship("Order", back_populates="files")
    uploaded_by = db.relationship("User", foreign_keys=[uploaded_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])
    library_file = db.relationship("LibraryFile")


class LibraryFile(db.Model):
    """A calibration file that CAT staff have validated for one exact original."""

    id = db.Column(db.Integer, primary_key=True)
    vehicle_make = db.Column(db.String(60), nullable=False)
    vehicle_model = db.Column(db.String(80), nullable=False)
    engine = db.Column(db.String(80))
    ecu_manufacturer = db.Column(db.String(60), nullable=False)
    ecu_model = db.Column(db.String(60), nullable=False)
    hardware_number = db.Column(db.String(60), nullable=False)
    software_number = db.Column(db.String(60), nullable=False)
    hardware_key = db.Column(db.String(60), nullable=False, index=True)
    software_key = db.Column(db.String(60), nullable=False, index=True)
    service = db.Column(db.String(120), nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    # Fingerprint of the unmodified original this file was built from
    original_sha256 = db.Column(db.String(64), nullable=False, index=True)
    original_size = db.Column(db.Integer, nullable=False)
    # The validated calibration file itself
    storage_key = db.Column(db.String(255), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    notes = db.Column(db.Text)
    # validated | retired
    status = db.Column(db.String(20), nullable=False, default="validated")
    supersedes_id = db.Column(db.Integer, db.ForeignKey("library_file.id"))
    source_order_id = db.Column(db.Integer, db.ForeignKey("order.id"))
    validated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    validated_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    times_used = db.Column(db.Integer, nullable=False, default=0)

    validated_by = db.relationship("User")
    supersedes = db.relationship("LibraryFile", remote_side=[id])
    source_order = db.relationship("Order")


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"))
    message = db.Column(db.String(255), nullable=False)
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    order = db.relationship("Order")


class AuditLog(db.Model):
    """Append-only record of who did what. Rows are never edited or deleted by the app."""

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    actor_email = db.Column(db.String(255))
    action = db.Column(db.String(60), nullable=False, index=True)
    entity_type = db.Column(db.String(40), index=True)
    entity_id = db.Column(db.Integer, index=True)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    @property
    def details_dict(self):
        try:
            return json.loads(self.details) if self.details else {}
        except ValueError:
            return {"raw": self.details}
