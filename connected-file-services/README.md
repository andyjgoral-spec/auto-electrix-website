# Connected File Services

ECU calibration file-processing portal for **Connected Automotive Technologies (CAT)**.
Workshops upload original ECU reads, pay with prepaid file credits, track their orders and download the finished files. CAT staff review, process and deliver every file by hand.

This is a working prototype: Python (Flask) backend, PostgreSQL, private file storage, Stripe Checkout and a responsive black-and-white interface. It is separate from the Auto Electrix website in the folder above.

---

## Run it locally

You need **Python 3.11+** and **PostgreSQL 14+**. If you have Docker, `docker compose up -d` in this folder starts a PostgreSQL database for you.

```bash
cd connected-file-services

# 1. Python environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Settings
cp .env.example .env               # then set SECRET_KEY to a long random string

# 3. Database (skip if you used docker compose)
createuser cfs --pwprompt          # password: cfs
createdb cfs --owner cfs

# 4. Tables and demo data
flask --app wsgi seed-demo --reset

# 5. Start
flask --app wsgi run --debug
```

Open http://localhost:5000.

### Demo accounts

All demo passwords are `DemoPass2026`.

| Who | Email |
|---|---|
| CAT administrator | `admin@cat-demo.example` |
| Workshop (UK) | `dan@southcoast-demo.example` |
| Workshop (UK) | `priya@midlands-demo.example` |
| Workshop (Sweden) | `erik@nordic-demo.example` |
| Workshop awaiting verification | `lee@newbury-demo.example` |

The demo data includes 12 orders in every status, 6 months of payments and invoices, and 2 validated library files. Order **CFS-100011** and **CFS-100012** use the same original file as earlier orders, so the admin order page shows an **exact library match**.

The demo ECU files are random bytes with a "DEMO" header. They are not real calibration files.

### Things to try

1. Sign in as **dan@…**, then go to **Upload file**, fill in the form and upload any `.bin` file of at least 1 KB. A credit is used.
2. Try uploading a renamed `.exe`, a `.zip` or a file filled with `0xFF`. Each one is refused with a reason, and the credit is kept.
3. **Credits → Buy**: without Stripe keys this opens a demo checkout. Credits and a VAT invoice are created.
4. Sign in as **admin@…**, then open the new order from **Overview**. Download the original, approve it, then upload a completed file of the **same size** and tick the approval box. The workshop sees a notification and can download it.
5. Open **CFS-100011** and look at the **Validated library check**.
6. Change a price under **Pricing**, then look at **Audit** to see every action recorded.

---

## Run the tests

```bash
pytest                                     # uses a temporary SQLite database
TEST_DATABASE_URL=postgresql+psycopg://cfs:cfs@localhost:5432/cfs_test pytest   # against PostgreSQL
```

There are 40 tests. They cover registration, login lockout, CSRF, role and ownership checks, file validation, credit spending and refunds, the full approve → deliver → download flow, revisions, exact library matching (including tampered files and near-misses), versioning, admin pricing and credit changes, the demo checkout, and Stripe webhook signatures, replays and amount checks.

> ⚠️ The test database is wiped on every test run. Never point `TEST_DATABASE_URL` at the real database.

---

## Stripe payments

1. Create a Stripe account and copy the **test** secret key (`sk_test_…`) into `STRIPE_SECRET_KEY` in `.env`.
2. Install the Stripe CLI and forward webhooks:
   ```bash
   stripe listen --forward-to localhost:5000/stripe/webhook
   ```
   Copy the `whsec_…` secret it prints into `STRIPE_WEBHOOK_SECRET`, then restart the app.
3. Buy credits and use the test card `4242 4242 4242 4242`.

Credits are only added when Stripe's **signed webhook** confirms payment (`checkout.session.completed`), never from the browser redirect. Each event is processed once. The amount paid must match the order. An invoice is issued automatically.

When Stripe keys are set, the demo checkout is switched off.

---

## How it works

| Area | What it does |
|---|---|
| **Credits** | 1 credit = 1 file. Credits are taken when a file is submitted and refunded if CAT rejects it. Every change is written to a credit ledger. |
| **Orders** | Awaiting review → In progress (approved) → Completed, or Rejected. Completed orders can be reopened to issue a new revision. Older revisions are kept. |
| **File validation** | Allowed extensions, 1 KB–16 MB, refuses programs, archives, PDFs, images, scripts and web pages, checks Intel HEX and S-record formats, and refuses blank reads. A SHA-256 checksum is recorded for every file. |
| **Private storage** | Files are saved under random names in `STORAGE_DIR`, outside the web folder, readable only by the app. Downloads go through permission checks and are logged. |
| **No automatic changes** | The app never edits calibration data. Completed files are uploaded by a technician, who must tick a box confirming the file has been checked and approved. A completed file must be the same size as the original. |
| **Validated library** | Library files are tied to the exact original they were made from. A library file is only offered when the ECU manufacturer, hardware number, software number, original file size **and** original SHA-256 all match. Near-misses are shown as warnings and cannot be delivered. A technician must still approve each delivery, and the stored file's checksum is re-checked first. New versions replace older ones, and the history is kept. |
| **Audit log** | Sign-ins, failed sign-ins, uploads, downloads, approvals, rejections, deliveries, library changes, payments, credit adjustments and settings changes are recorded with user, time and IP address. |
| **Security** | Hashed passwords (scrypt), 10-character minimum, account lockout after 5 failed attempts, CSRF tokens on every form, a new session at sign-in, sign-out of other sessions after a password change, HttpOnly/SameSite cookies, strict Content-Security-Policy, and separate customer and admin roles. Workshops can only see their own orders, files and invoices. |
| **Pricing** | Packages, VAT rate, services list, turnaround target and whether new workshops need verifying can all be changed under **Pricing**. Invoices copy the price and billing details, so old invoices never change. |

### Folder layout

```
cfs/
  models.py        database tables
  services.py      business rules (credits, orders, library, payments, audit)
  storage.py       private storage and file validation
  security.py      sign-in, roles, CSRF, security headers
  routes/          auth, customer (workshop), admin, payments (Stripe)
  templates/       pages
  static/          CSS and a little JavaScript (everything works without it)
  seed.py          default prices and demo data
tests/             automated tests
```

Admin commands:

```bash
flask --app wsgi init-db                         # create tables and default prices
flask --app wsgi create-admin you@cat.co.uk "Your Name"
flask --app wsgi seed-demo --reset               # wipe and reload demo data
```

---

## Build stages

**Stage 1: done (this prototype)**
- Customer dashboard, upload workflow, order tracking, downloads, credits, invoices and notifications
- CAT admin area: queue, approve or reject, deliver, revisions, workshops, credits, pricing, reports, validated library and audit log
- Stripe Checkout with webhooks, plus a demo checkout

**Stage 2: before going live**
- Move file storage to private S3-compatible storage (for example Cloudflare R2 or AWS S3) with encryption and backups
- Database migrations with Alembic (the prototype uses `create_all`)
- Password reset and email verification by email, and two-factor sign-in for CAT staff
- Real email sending (set `SMTP_*`; without it emails are printed to the console)
- Rate limiting at the web server or proxy, HTTPS, and `SESSION_COOKIE_SECURE=1`
- Run with `gunicorn wsgi:app` behind a reverse proxy
- Legal review of terms of service, privacy policy (UK GDPR) and invoice details (`SELLER_*`)

**Stage 3: later**
- PDF invoices and Xero/QuickBooks export
- Several users per workshop, with roles
- Customer messages on orders
- Virus scanning of uploads
- Stripe Tax for EU reverse-charge VAT

---

## Important

- **Emissions services:** DPF, EGR and AdBlue/SCR off are in the default list but marked **off-road only**. Workshops must tick an off-road / motorsport declaration to order them, and it is saved on the order and in the audit log. Using a road vehicle with emissions equipment removed is an offence in the UK and EU and fails the MOT. The declaration is evidence of what the customer told you; it is not legal protection on its own. Get a solicitor to check your terms. You can mark any service as off-road only, or switch it off, under **Pricing**.
- **Not yet production-ready:** see Stage 2 above. In particular, add migrations, off-site file backups and HTTPS before taking real customer files or payments.
