"""Default settings and demonstration data.

Demo ECU files are random bytes with a "DEMO" header. They are not real
calibration files and must never be written to a vehicle.
"""
import random
from datetime import timedelta

import click
from sqlalchemy import select

from . import storage
from .extensions import db
from .models import (
    ROLE_ADMIN, ROLE_CUSTOMER, AuditLog, CreditTransaction, Notification, PricePackage, ServiceOption,
    Setting, User, Workshop, utcnow,
)
from . import services

DEFAULT_PACKAGES = [
    ("Single file", "One ECU file service", 7000, 1, 1),
    ("5+1 package", "Pay for five files, get six", 35000, 6, 2),
    ("10+2 package", "Pay for ten files, get twelve", 70000, 12, 3),
]

# (name, off-road only). Emissions-system removal is illegal on road vehicles in
# the UK and EU, so those services require a signed off-road declaration.
DEFAULT_SERVICES = [
    ("Stage 1 performance calibration", False),
    ("Stage 2 performance calibration", False),
    ("Economy calibration", False),
    ("Pops & bangs / crackle map", False),
    ("Speed limiter adjustment (fleet)", False),
    ("Gearbox (TCU) calibration", False),
    ("Start/stop default setting", False),
    ("DTC (fault code) removal", False),
    ("DPF off (off-road / motorsport only)", True),
    ("EGR off (off-road / motorsport only)", True),
    ("AdBlue / SCR off (off-road / motorsport only)", True),
    ("Original file review / diagnostic", False),
    ("Other (describe in notes)", False),
]

DEMO_PASSWORD = "DemoPass2026"
ADMIN_EMAIL = "admin@cat-demo.example"


def ensure_defaults():
    for key, value in Setting.DEFAULTS.items():
        if db.session.get(Setting, key) is None:
            db.session.add(Setting(key=key, value=value))
    if not db.session.scalar(select(PricePackage).limit(1)):
        for name, desc, price, credits, order in DEFAULT_PACKAGES:
            db.session.add(PricePackage(name=name, description=desc, price_pence=price, credits=credits, sort_order=order))
    if not db.session.scalar(select(ServiceOption).limit(1)):
        for i, (name, offroad) in enumerate(DEFAULT_SERVICES):
            db.session.add(ServiceOption(name=name, sort_order=i, offroad_only=offroad))
    db.session.commit()


def demo_bytes(seed, size=512 * 1024):
    rng = random.Random(seed)
    header = f"DEMO ECU READ {seed} - NOT REAL CALIBRATION DATA".encode().ljust(64, b"\x00")
    return header + rng.randbytes(size - len(header))


def seed_demo_data(reset=False):
    if reset:
        db.drop_all()
        storage.remove_all()
    db.create_all()
    ensure_defaults()
    if db.session.scalar(select(User).where(User.email == ADMIN_EMAIL)):
        click.echo("Demo data already loaded. Use --reset to start again.")
        return

    admin = User(email=ADMIN_EMAIL, name="Sam Carter (CAT)", role=ROLE_ADMIN)
    admin.set_password(DEMO_PASSWORD)
    tech = User(email="tech@cat-demo.example", name="Jo Patel (CAT)", role=ROLE_ADMIN)
    tech.set_password(DEMO_PASSWORD)
    db.session.add_all([admin, tech])

    workshop_specs = [
        ("Southcoast Auto Electrics", "Dan Hughes", "dan@southcoast-demo.example", "Poole", "BH15 1AA", "United Kingdom", "GB123456789"),
        ("Midlands Mobile Diagnostics", "Priya Shah", "priya@midlands-demo.example", "Coventry", "CV1 2AB", "United Kingdom", None),
        ("Nordic Tuning Partners", "Erik Lund", "erik@nordic-demo.example", "Malmö", "211 20", "Sweden", "SE556677889901"),
    ]
    workshops = []
    for name, contact, email, city, postcode, country, vat in workshop_specs:
        ws = Workshop(name=name, contact_name=contact, phone="01234 567890", address="Unit 4, Demo Industrial Estate",
                      city=city, postcode=postcode, country=country, vat_number=vat, status="active")
        user = User(email=email, name=contact, role=ROLE_CUSTOMER, workshop=ws)
        user.set_password(DEMO_PASSWORD)
        db.session.add_all([ws, user])
        workshops.append((ws, user))
    pending = Workshop(name="Newbury Garage Services", contact_name="Lee Morgan", phone="01635 000000",
                       address="1 Demo Road", city="Newbury", postcode="RG14 1AA", country="United Kingdom",
                       status="pending")
    pending_user = User(email="lee@newbury-demo.example", name="Lee Morgan", role=ROLE_CUSTOMER, workshop=pending)
    pending_user.set_password(DEMO_PASSWORD)
    db.session.add_all([pending, pending_user])
    db.session.commit()

    now = utcnow()
    packages = db.session.scalars(select(PricePackage).order_by(PricePackage.sort_order)).all()
    single, five, ten = packages[0], packages[1], packages[2]

    # Credit purchases spread over recent months
    purchases = [(0, ten, 150), (0, five, 40), (1, five, 120), (1, single, 20), (1, single, 3), (2, ten, 90), (2, five, 10)]
    for idx, pkg, days_ago in purchases:
        ws, user = workshops[idx]
        payment = services.start_payment(user, pkg, "demo")
        services.fulfil_payment(payment, actor=user)
        when = now - timedelta(days=days_ago, hours=random.Random(days_ago).randint(1, 8))
        payment.created_at = payment.paid_at = when
        payment.invoice.issued_at = when
        for tx in db.session.scalars(select(CreditTransaction).where(CreditTransaction.payment_id == payment.id)):
            tx.created_at = when
    db.session.commit()

    vehicles = [
        ("Volkswagen", "Golf 2.0 TDI", "2018", "2.0 TDI 150", "Bosch", "EDC17C64", "04L907309B", "1037541789", "Stage 1 performance calibration"),
        ("Ford", "Transit Custom 2.0 EcoBlue", "2020", "2.0 EcoBlue 130", "Continental", "SID212", "KK2A-12A650-AXB", "KK2A-14C204-AXC", "Economy calibration"),
        ("BMW", "320d F30", "2016", "B47D20", "Bosch", "EDC17C50", "8579323", "9353488", "Stage 1 performance calibration"),
        ("Mercedes-Benz", "Sprinter 316 CDI", "2019", "OM651", "Delphi", "CRD3.10", "A6519000600", "A6519024900", "Speed limiter adjustment (fleet)"),
        ("Audi", "A4 B9 2.0 TFSI", "2019", "2.0 TFSI 190", "Bosch", "MG1CS001", "8W0907115D", "8W0906259A", "Stage 2 performance calibration"),
        ("Toyota", "Hilux 2.4 D-4D", "2021", "2GD-FTV", "Denso", "SH7059", "89661-0KQ70", "175400-0510", "Economy calibration"),
        ("Peugeot", "Partner 1.5 BlueHDi", "2020", "DV5RD", "Bosch", "EDC17C60", "9823404380", "1037566012", "Start/stop default setting"),
        ("Land Rover", "Defender 3.0 D250", "2022", "AJ300", "Bosch", "MD1CP001", "L8B2-14C204-AA", "L8B2-12C520-BB", "Original file review / diagnostic"),
        ("Volvo", "XC60 D4", "2017", "D4204T14", "Denso", "SH72546", "31452966", "31459122", "Stage 1 performance calibration"),
        ("Fiat", "Ducato 2.3 MultiJet", "2019", "F1AGL411", "Marelli", "MJD8F3", "55274568", "55285421", "Gearbox (TCU) calibration"),
    ]

    # (vehicle index, workshop index, final state, days ago, hours to complete)
    plan = [
        (0, 0, "completed", 140, 2.5), (1, 0, "completed", 110, 3.0), (2, 1, "completed", 100, 1.2),
        (3, 2, "completed", 80, 5.5), (4, 0, "completed", 60, 2.0), (5, 1, "rejected", 45, None),
        (6, 2, "completed", 30, 0.8), (7, 0, "completed", 12, 3.8), (8, 1, "completed", 5, 1.5),
        (9, 2, "in_progress", 0.2, None), (2, 0, "submitted", 0.1, None), (0, 1, "submitted", 0.05, None),
    ]
    created_orders = []
    for n, (v_idx, ws_idx, state, days_ago, tat) in enumerate(plan):
        make, model, year, engine, ecu_mfr, ecu_model, hw, sw, service = vehicles[v_idx]
        ws, user = workshops[ws_idx]
        db.session.refresh(ws)
        # Orders 10 and 11 re-use originals from orders 2 and 0, so the library has exact matches.
        seed = {10: 2, 11: 0}.get(n, n)
        data = demo_bytes(f"orig-{seed}")
        fields = dict(vehicle_make=make, vehicle_model=model, vehicle_year=year, engine=engine,
                      registration=f"DM{n:02d} CFS", ecu_manufacturer=ecu_mfr, ecu_model=ecu_model,
                      hardware_number=hw, software_number=sw, read_tool="Demo read tool", read_method="OBD",
                      service_requested=service, customer_notes="Demonstration order.", declaration_accepted=True)
        order = services.create_order(user, fields, f"{make.split()[0].lower()}_{ecu_model.lower()}_original.bin", data)
        created = now - timedelta(days=days_ago)
        if state in ("in_progress", "completed"):
            services.approve_order(order, admin)
        if state == "completed":
            services.deliver_uploaded_file(order, tech, f"{order.reference}_calibrated.bin",
                                           demo_bytes(f"cal-{seed}", len(data)), "Demo calibration file", True)
            order.completed_at = created + timedelta(hours=tat)
            order.approved_at = created + timedelta(minutes=10)
        if state == "rejected":
            services.reject_order(order, admin, "The read is incomplete (checksum block missing). Please re-read on the bench and resubmit.")
            order.rejected_at = created + timedelta(minutes=40)
        order.created_at = created
        for f in order.files:
            f.created_at = order.completed_at if f.kind == "completed" else created
        created_orders.append(order)
        db.session.commit()

    # Validated library entries from two completed orders (orders 0 and 2)
    for order in (created_orders[0], created_orders[2]):
        services.add_library_from_order(order, admin, "Validated on the demo rolling road.", True)

    # Make the timeline of audit records and notifications consistent with backdated orders.
    for order in created_orders:
        for row in db.session.scalars(select(AuditLog).where(AuditLog.entity_type == "Order", AuditLog.entity_id == order.id)):
            row.created_at = {"order.approved": order.approved_at, "order.rejected": order.rejected_at}.get(row.action) or order.created_at
        for f in order.files:
            for row in db.session.scalars(select(AuditLog).where(AuditLog.entity_type == "OrderFile", AuditLog.entity_id == f.id)):
                row.created_at = f.created_at
        for note in db.session.scalars(select(Notification).where(Notification.order_id == order.id)):
            note.created_at = order.completed_at or order.created_at
        for tx in db.session.scalars(select(CreditTransaction).where(CreditTransaction.order_id == order.id)):
            tx.created_at = order.created_at
    db.session.commit()

    click.echo("Demo data loaded.\n")
    click.echo(f"  CAT admin:     {ADMIN_EMAIL} / {DEMO_PASSWORD}")
    for ws, user in workshops:
        click.echo(f"  Workshop:      {user.email} / {DEMO_PASSWORD}  ({ws.name})")
    click.echo(f"  Pending:       {pending_user.email} / {DEMO_PASSWORD}  (awaiting approval)")
