# Atölye — repair shop management, e-Fatura & bookkeeping

A self-hosted web application for a medium-sized **power-tool repair workshop** in Türkiye:

- **Service orders** (servis kayıtları): intake → diagnosis → customer approval → repair → pickup, with a printable
  intake/delivery receipt, technician assignment, parts & labour, and an activity log.
- **Customers & suppliers** (cari hesaplar): VKN/TCKN validation, one-click GİB e-Fatura registration check, account
  statements (ekstre) with running balance.
- **e-Fatura / e-Arşiv**: generates **UBL-TR 1.2** XML (TEMELFATURA, TICARIFATURA, EARSIVFATURA; SATIŞ, İADE,
  TEVKİFAT, İSTİSNA), GİB-format numbering (`EFT2026000000001`), sends through a pluggable **integrator adapter**.
- **Bookkeeping** (ön muhasebe): cash/bank/POS accounts, collections and payments, transfers, expenses with deductible
  VAT, monthly profit & VAT report, CSV exports for the accountant (mali müşavir).
- **Parts & stock**: parts catalogue with stock that is deducted automatically when an invoice is issued, low-stock
  warnings.
- **Backups**: everything is one folder. One-click and automatic backups to a zip file, a second copy to a
  USB/NAS/OneDrive folder, and a one-step restore.
- Turkish UI (English available per user), light & dark theme, works on phones and tablets in the workshop.

| Dashboard | Service order |
|---|---|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Service order](docs/screenshots/service-order.png) |
| **Invoice** | **Backups** |
| ![Invoice](docs/screenshots/invoice.png) | ![Backups](docs/screenshots/backups.png) |

Dark mode and mobile: [dashboard-dark.png](docs/screenshots/dashboard-dark.png), [mobile.png](docs/screenshots/mobile.png)

---

## Quick start

Requires **Python 3.10+**. No database server — data is a single SQLite file.

**Windows (typical workshop PC):** double-click `start-windows.bat`. The first run installs everything into `.venv`.

**Linux / macOS:**

```sh
./start.sh                     # or: pip install -r requirements.txt && python run.py
```

Open <http://localhost:8080>. On first launch a setup page creates the administrator account. Other computers,
tablets and phones on the same network use the address printed in the console, e.g. `http://192.168.1.20:8080`.

**Docker:**

```sh
docker compose up -d           # data is kept in ./data
```

**Try it with demo data** (a fictional workshop, ~230 invoices, 12 open repair jobs):

```sh
flask --app app seed-demo      # then log in with  demo / demo1234
```

## Where the data lives — and backing it up

Everything the application stores is in **one folder** (`data/` next to the app, or `ERP_DATA_DIR`):

```
data/
  erp.db            # the database (customers, orders, invoices, bookkeeping, settings)
  files/invoices/   # the UBL-TR XML of every issued invoice
  secret.key        # session signing key
  backups/          # automatic and manual backups (default location)
```

**Settings → Backups** gives you:

- **Automatic backups** every N hours while the app runs (default: daily, keep the last 30).
- **Second copy folder**: point it at a USB disk, a NAS share or a OneDrive / Google Drive synced folder so a copy
  always leaves the PC. *Recommended.*
- **Back up now / Download backup now**: one zip file containing a consistent database snapshot (taken with SQLite's
  online backup API, safe while people are working), all invoice XML files and a manifest with a checksum.
- **Restore**: from the list or by uploading a zip. The archive is validated (checksum + SQLite integrity check) and a
  *pre-restore* safety backup is taken first, so a restore can itself be undone.

From the command line: `flask --app app backup [--dest D:\Yedek]`, `flask --app app restore file.zip`,
`flask --app app info`.

**Moving to a new computer:** install the app there, then Settings → Backups → *Restore from a file*.

## e-Fatura integration

Turkish e-invoices must be delivered to GİB through a licensed **special integrator** (özel entegratör). This app
creates the invoice XML; the integrator signs it with the company's seal (mali mühür) and delivers it. The
integrator is chosen in **Settings → e-Invoice integrator**:

| Adapter | What it does |
|---|---|
| **Sandbox** | Simulates an integrator, sends nothing. For training and demos. Default. |
| **Manual XML export** | Writes each issued invoice as UBL-TR XML to a folder. Upload it in *any* integrator's web portal (including the one the shop uses today), then mark the invoice as sent. Works immediately, no API needed. |
| **Nilvera (REST API)** | Sends e-Fatura / e-Arşiv invoices through Nilvera's REST API with an API key; checks GİB registration of customers; queries status; cancels e-Arşiv. |

### Before going live — please read

- **Verify the API adapter with the integrator's test account first.** The Nilvera endpoint paths are gathered in one
  place (`app/services/integrators/nilvera.py`, `PATH_*` constants) and are covered by unit tests with a fake HTTP
  session, but they have **not** been exercised against Nilvera's live test service. Run *Save & test connection* and
  send a few test invoices on `apitest.nilvera.com` before switching to production.
- **Which integrator does the shop use today?** If it is not Nilvera, either use *Manual XML export* right away, or
  add an adapter: subclass `Integrator` in `app/services/integrators/` (implement `check_user`, `send`, `get_status`,
  `cancel`) and register it in `integrators/__init__.py`. Most integrators (Uyumsoft, QNB eFinans, Logo, İzibiz,
  EDM, Sovos…) accept the same UBL-TR XML; only the transport differs.
- **Invoice series.** Set the 3-character e-Fatura / e-Arşiv series in *Settings → Invoicing* to what is registered
  at the integrator. If the shop switches mid-year and keeps its series, enter the next sequence number after the last
  invoice the old program issued ("start from number"), so numbering continues without gaps or duplicates.
- The generated XML follows the UBL 2.1 element order and UBL-TR conventions (VKN/TCKN party identification, KDV
  0015 tax scheme, withholding codes, e-Arşiv sending type, İADE billing reference). It is not run through GİB's
  official schematron here — the integrator validates it on submission and returns readable errors, which the app
  shows on the invoice together with a *Rebuild XML* action.

## Accounting scope

This is **pre-accounting (ön muhasebe)**: it tracks sales, purchases, receivables, payables, cash and bank, and
estimates monthly KDV (hesaplanan − tevkif edilen − indirilecek). The official books, e-Defter and tax returns remain
with the accountant — the *Reports → For your accountant* menu exports invoices, invoice lines, expenses,
cash/bank movements and contact balances as Excel-friendly CSV (UTF-8 with BOM, `;` separated, decimal comma).

## Everyday flow in the workshop

1. **Customer drops off a tool** → *New service order* → pick/create the customer, record device, serial number,
   accessories and complaint → *Save & print receipt* (customer signs the intake receipt).
2. **Technician** diagnoses, adds parts & labour (from the catalogue), sets status *Awaiting approval* → customer
   approves → *In repair* → *Ready for pickup*.
3. **Create invoice** from the order → the app picks e-Fatura or e-Arşiv from the customer's GİB registration and
   applies 7/10 KDV tevkifat (code 603, repair services) for designated buyers → *Issue & send*.
4. **Record the payment** on the invoice (cash, bank, POS). Unpaid invoices show up as receivables and overdue alerts.
5. Enter **expenses** (rent, parts purchases…) as they come in. At month end, check **Reports** and send the CSV
   exports to the accountant.

## Development

```sh
pip install -r requirements-dev.txt
pytest                          # unit + end-to-end tests
python scripts/i18n_check.py    # lists UI strings missing a Turkish translation
flask --app app run --debug     # dev server with auto reload
```

```
app/
  models.py                 # SQLAlchemy models (money stored as exact integer kuruş)
  services/calc.py          # invoice arithmetic (Decimal, half-up rounding)
  services/ubl.py           # UBL-TR XML builder + business validation
  services/invoicing.py     # draft → issued → sent/accepted lifecycle, numbering, stock
  services/integrators/     # integrator adapters (sandbox, manual export, Nilvera)
  services/backup.py        # backup / restore / scheduler
  routes/                   # Flask blueprints
  templates/, static/       # server-rendered UI, no build step, no CDN (works offline)
  translations_tr.py        # Turkish UI strings
```

UI strings are written in English in the code and translated through `app/translations_tr.py`; the test suite fails
if a string has no Turkish translation.
