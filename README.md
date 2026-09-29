# Atölye — repair shop management, e-Fatura & bookkeeping

A self-hosted web application for a medium-sized **power-tool repair workshop** in Türkiye:

- **Service orders** (servis kayıtları): intake → diagnosis → customer approval → repair → pickup, with a printable
  intake/delivery receipt, technician assignment, parts & labour, and an activity log.
- **Customers & suppliers** (cari hesaplar): VKN/TCKN validation, one-click GİB e-Fatura registration check, account
  statements (ekstre) with running balance.
- **e-Fatura / e-Arşiv**: generates **UBL-TR 1.2** XML (TEMELFATURA, TICARIFATURA, EARSIVFATURA; SATIŞ, İADE,
  TEVKİFAT, İSTİSNA), GİB-format numbering (`EFT2026000000001`), and sends it through the integrator of your choice
  (EDM, İzibiz, Logo, Nilvera, QNB eFinans, Sovos, Uyumsoft, or manual XML upload), adapting the XML to each one.
- **Bookkeeping** (ön muhasebe): cash/bank/POS accounts, collections and payments, transfers, expenses with deductible
  VAT, monthly profit & VAT report, CSV exports for the accountant (mali müşavir).
- **Parts & stock**: parts catalogue with stock that is deducted automatically when an invoice is issued, low-stock
  warnings.
- **Backups**: everything is one folder. One-click and automatic backups to a zip file, a second copy to a
  USB/NAS/OneDrive folder, and a one-step restore.
- **Assistant with voice**: technicians say "bu işi bitirdim" and the job is updated; other questions go to a free
  local AI model (Gemma 4 via Ollama). Invoices and payments always ask for confirmation.
- Turkish UI (English available per user), light & dark theme, works on phones and tablets in the workshop.

| Dashboard | Service order |
|---|---|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Service order](docs/screenshots/service-order.png) |
| **Invoice** | **Backups** |
| ![Invoice](docs/screenshots/invoice.png) | ![Backups](docs/screenshots/backups.png) |

Dark mode and mobile: [dashboard-dark.png](docs/screenshots/dashboard-dark.png), [mobile.png](docs/screenshots/mobile.png)

---

## Windows package for the shop (recommended)

Build a single zip that contains everything — portable Python, all packages, the app and Ollama:

```sh
pip install requests
python scripts/build_bundle.py --platform windows            # → dist/Atolye-windows-x64.zip
python scripts/build_bundle.py --platform windows --cpu-only # smaller: without the GPU runtimes
python scripts/build_bundle.py --platform windows --with-voice  # include offline speech recognition
```

It can be built on Windows, Linux or macOS. The latest Ollama release is downloaded from GitHub and checked
against its published SHA-256 checksum (`--ollama-version v0.X.Y` pins one).

**Ready-made package:** GitHub Actions builds it on a Windows machine, installs and tests it there (with Windows
Defender running) and publishes the zip as a **Release** — Actions → *Windows package* → *Run workflow*, or push a
tag like `v1.2.0`. The zip is then on the repository's Releases page.

On the shop PC: unzip to a permanent folder (e.g. `C:\Atolye`) and double-click **Kur.bat**. It needs **no
administrator rights** and does only what an ordinary program does, so antivirus software has nothing to object to
(no PowerShell, no certificate store, no firewall changes, no self-elevation). For the current Windows user it

- starts the app at every log-in, hidden (a shortcut in the user's Startup folder; log in `data\logs\server.log`),
- puts an **Atölye** shortcut on the desktop: `http://localhost:8080` — on the PC itself browsers treat localhost as
  secure, so the microphone and the app install work without any certificate,
- starts the app and opens it in the browser.

When the app first listens on the network, Windows Firewall asks once whether to allow Python — **Allow** lets the
phones connect. If that question was missed or refused, right-click **Telefon-Izni.bat** → *Run as administrator*
(adds a rule for ports 8080/8443, local network only). **Durdur.bat** stops the background app (e.g. before copying a
new version over it), **Kaldir.bat** undoes what Kur.bat did (run it as administrator to also remove the firewall
rule); the `data` folder is never touched. Without installing, **Atolye.bat** / **Atolye-HTTPS.bat** run the
app while their console window stays open. The first start downloads the AI model in the background. All data stays
in the `data` folder. Third-party licences are listed in `THIRD_PARTY_NOTICES.txt`.

To add the bundled Ollama to a normal checkout instead (Linux/macOS development, or `start.sh`/`start-windows.bat`):
`python scripts/build_bundle.py --platform linux --only-ollama` puts it in `vendor/ollama`, where the app finds it.

## Quick start

Requires **Python 3.10+**. No database server — data is a single SQLite file.

**Windows (typical workshop PC):** double-click `start-windows.bat`. The first run installs everything into `.venv`.

**Linux / macOS:**

```sh
./start.sh                     # or: pip install -r requirements.txt && python run.py
```

Open <http://localhost:8080>. On first launch a setup wizard asks for everything the shop needs (see
[First-run setup](#first-run-setup--no-files-to-edit)). Other computers,
tablets and phones on the same network use the address printed in the console, e.g. `http://192.168.1.20:8080`
(for phones see [Phones and tablets](#phones-and-tablets)).

**Docker:**

```sh
docker compose up -d           # data is kept in ./data; an Ollama container serves the assistant
```

**Try it with demo data** (a fictional workshop, ~230 invoices, 12 open repair jobs):

```sh
flask --app app seed-demo      # then log in with  demo / demo1234
```

## First-run setup — no files to edit

The first start opens a setup wizard ([screenshot](docs/screenshots/setup-wizard.png)); every step can be skipped
and changed later in Settings:

1. **Your account** — the administrator. *Moving to a new computer?* The same screen restores everything from a
   backup zip instead.
2. **Company & logo** — the legal title exactly as registered with GİB (e.g. "Çağ-Tek Makina San. ve Tic. Ltd.
   Şti."; the menu shows the short name automatically), VKN/TCKN, tax office, address, IBAN and the logo (PNG/JPG,
   up to 300 KB, previewed immediately). The logo appears in the app, on receipts and printouts, as the phone app
   icon and inside every e-invoice. Without one the initials are used ("ÇT").
3. **Invoice numbers** — type the last number the previous program issued this year (e.g. `CGT2026000000123`);
   the app takes the series from it and continues with …124. A number from an earlier year means this year starts
   at 1, and starting numbers only ever apply to the year they were set for.
4. **e-Fatura connection** — Uyumsoft web service username/password, test or production, with a *Save & test
   connection* button; or *decide later* (practice mode, nothing reaches GİB).
5. **Backups** — second copy folder (USB disk or OneDrive/Google Drive folder; a OneDrive folder on the PC is
   suggested automatically) and a first backup that proves the folder works.
6. **Summary** — what is done, what is missing, the next invoice numbers, and links to add users and connect phones.

Until the wizard is finished, administrators see a *Continue setup* banner.

`customer.json` is optional: it only pre-fills the company name in step 2 (and the login screen before setup). This
installation ships with `{ "company_name": "Çağ-Tek Makina", "short_name": "Çağ-Tek Makina" }`; without the file
the wizard simply starts empty.

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
creates the invoice XML; the integrator signs it with the company's seal (mali mühür) and delivers it. Pick the
integrator in **Settings → e-Invoice integrator**:

| Integrator | Transport | e-Fatura | e-Arşiv | GİB lookup | Status | e-Arşiv cancel |
|---|---|:-:|:-:|:-:|:-:|:-:|
| **Sandbox** (default) | none — simulated, for training | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Manual XML export** | writes XML to a folder, upload in any integrator's portal | ✓ | ✓ | – | manual | ✓ |
| **EDM Bilişim** | SOAP `EFaturaEDM.svc`, session login | ✓ | ✓ | ✓ | ✓ | – |
| **İzibiz** | SOAP `AuthenticationWS` / `EInvoiceWS` / `EIArchiveWS` | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Logo eLogo** | SOAP `PostBoxService`, session login, zipped UBL | ✓ | ✓ | ✓ | ✓ | – |
| **Nilvera** | REST, API key | ✓ | ✓ | ✓ | ✓ | ✓ |
| **QNB eSolutions (eFinans)** | SOAP `connectorService` + `EarsivWebService`, WS-Security | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Sovos (Foriba)** | SOAP `ClientEInvoiceServices` (`sendUBL`), HTTP Basic | ✓ | – | – | ✓ | – |
| **Uyumsoft** | SOAP `Services/Integration`, WS-Security | ✓ | ✓ | ✓ | ✓ | ✓ |

A "–" means the adapter does not implement it yet; the app says so clearly (and e.g. suggests manual export for
Sovos e-Arşiv invoices) instead of failing silently. Other integrators (Turkcell e-Şirket, Mysoft, Digital Planet,
Veriban, Kolaysoft…) work today through **Manual XML export**; a direct adapter is one small file (see below).

### How the XML is adapted per integrator

Every integrator accepts UBL-TR, but they differ in details. The app therefore keeps two files per invoice:

1. **Canonical XML** — built once when the invoice is issued. This is the business record and never changes.
2. **Sent XML** — produced right before sending by applying the chosen integrator's *XML profile*, and stored
   exactly as sent (`data/files/invoices/<number>_<uuid>.<integrator>.xml`). The invoice's *XML* button downloads it.

The profile covers the differences that matter in practice, each preset per integrator and overridable in
Settings under *XML adjustments*:

| Option | Why it differs |
|---|---|
| Embed invoice display template (XSLT) | GİB, portals and the receiver show the invoice through an XSLT embedded in the XML. The app ships its own template (`app/services/xslt/invoice.xslt`); integrators that apply their own design can skip it. |
| e-Arşiv delivery type in the XML | Some integrators read the e-Arşiv *gönderim şekli* from the XML (`AdditionalDocumentReference` / `SendingType`), others from API fields (Uyumsoft `EArchiveInvoiceInfo`, İzibiz `EARSIV_PROPERTIES`). |
| ETTN upper case | Case of the invoice UUID. |
| Indented XML | Some parsers dislike whitespace between elements. |

Adapters can also change the XML tree in code (`customize_xml`) and choose the packaging (raw, base64,
zipped + MD5 hash, or embedded in the SOAP body), which each adapter does according to its API.
*As the recipient sees it* on an invoice renders the sent XML with its embedded template.

### Before going live — please read

The adapters were written from the integrators' published method names and common usage, **not tested against their
live test services** (this development environment could not reach them). They are marked **Unverified** in the
settings. Their request building and response parsing are covered by tests with a simulated server, but real
services may name a field or namespace differently. For the integrator the shop uses:

1. Get a **test account** from the integrator (Uyumsoft publishes one: `Uyumsoft` / `Uyumsoft`).
2. In Settings choose the integrator, environment *Test*, enter the credentials, tick *Log requests and responses*,
   and press **Save & test connection**.
3. Check a customer's e-Fatura status and send a few test invoices (e-Fatura and e-Arşiv).
4. If something fails, `data/logs/integrator-YYYY-MM.log` contains every request and response (passwords removed).
   The fix is usually a one-line change in `app/services/integrators/<integrator>.py`; service addresses can be
   changed in Settings without code.
5. Switch to *Production*, turn logging off, and set the invoice series and starting number (Settings → Invoicing).

### Adding an integrator

Subclass `Integrator` in `app/services/integrators/` and register it in `integrators/__init__.py`:

```python
class MyIntegrator(Integrator):
    key, label = "mine", "My Integrator"
    capabilities = {"efatura", "earsiv", "lookup", "status"}
    xml_defaults = XmlOptions(embed_xslt=True, earsiv_sending_type=False)
    URLS = {"test": {"default": "https://…"}, "production": {"default": "https://…"}}
    fields = [ENV_FIELD, ConfigField("username", "Username"), ConfigField("password", "Password", kind="password"),
              DEBUG_FIELD]

    def check_user(self, tax_id): ...          # -> [Alias(...)]
    def send(self, invoice, xml, receiver_alias=""): ...   # -> SendResult
    def get_status(self, invoice): ...         # -> StatusResult
```

`soap.py` provides envelope / WS-Security / fault helpers; `self.soap_call(...)` handles transport and logging.
Settings and translations pick the new adapter up automatically (add its strings to `translations_tr.py`).

## Assistant (text and voice) — runs locally, free

Every page has an **Asistan** button (and there is a full-screen *Asistan* page for a workshop tablet). Staff type or
speak; technicians can just say *"bu işi bitirdim"* and the job is updated.

**How requests are handled**

1. **Built-in workshop commands (no AI needed).** Status updates and notes are understood directly by the app —
   instantly, predictably, even with no model installed:

   | Say or type | Result |
   |---|---|
   | "Bu işi bitirdim, kömürleri değiştirdim" | the job on screen → *Teslime hazır*, "Kömürleri değiştirdim" saved as work done |
   | "SRV-2026-00240 hazır" / "240 numaralı iş parça bekliyor" | that job → ready / awaiting parts |
   | "Onarıma başladım", "Müşteri onayladı", "Teklif verdim", "Teslim ettim" | in repair / approved / awaiting approval / delivered |
   | "Not ekle: müşteri cuma alacak" | note in the job's activity log |

   "This job" means the job open on screen; otherwise the technician's own open job. If several could match, the
   assistant shows them as buttons to tap. Every change has an **Undo** button and is logged as done via the assistant.
   Questions ("240 hazır mı?") and negations ("hazır değil") are never treated as commands.

2. **Local AI model for everything else** — questions ("Yıldız İnşaat'ın borcu ne kadar?", "Bugün hangi işler
   teslim edilecek?"), opening orders, adding parts, draft invoices. The model calls the app's tools
   (`app/services/assistant/tools.py`); lookups and workshop updates run directly, while **sending invoices to GİB,
   payments, expenses, stock corrections and new customers always show a Confirm button** first. Cancelling a job
   also asks for confirmation.

**The local model — nothing to install, only the model is downloaded**

The Windows package (below) ships with [Ollama](https://github.com/ollama/ollama) (MIT license). When the app
starts it launches the bundled Ollama in the background on its own port (11435, so it never clashes with an Ollama
installed separately), keeps models in `data/models/ollama`, and **downloads the configured model automatically on
first start** (`gemma4:e2b`, a few GB, once; any model with tool calling works, e.g. `qwen3.5:2b`, or a GGUF
from Hugging Face as `hf.co/<user>/<repo>:<quantization>`). Settings → Assistant shows the download progress and has a
*Download model now* button; until the model is ready the workshop commands already work, and the assistant says
the model is still downloading. The app stops Ollama when it exits and restarts it if it stopped.

Other set-ups work too: choose *Other server* in Settings → Assistant for an Ollama installed separately, LM Studio
or llama.cpp (any OpenAI-compatible API). With an Ollama server the missing model is still downloaded automatically.

A 2B model needs about 3 GB of free RAM and runs on the CPU (a graphics card is used when present). **Expectations:**
a 2B model is fast and free, but it will sometimes misunderstand longer or unusual Turkish requests or pick the wrong
tool — that is why everyday commands don't depend on it and why risky actions need confirmation. If the PC has 8 GB+
RAM to spare, a 4B model understands noticeably better: change the model name in Settings and it is downloaded.

**Voice**

- *Browser* (default when nothing else is installed): Chrome/Edge speech recognition — nothing to install, but the
  audio is processed by Google/Microsoft and needs internet.
- *On this computer (Whisper, offline)*: `pip install -r requirements-voice.txt`; the Whisper model (~470 MB for
  "small") downloads once into `data/models`, then speech never leaves the shop. Chosen automatically when installed.
- Replies can be read aloud (speaker button in the panel).
- **Phones and tablets only allow the microphone on https** — see below. On the shop PC itself `http://127.0.0.1`
  works without it.

The assistant can be switched off, or limited to the built-in commands, in Settings → Assistant.

## Phones and tablets

The phone is only a screen: the app, the database, the AI model and (with Whisper) speech recognition all run on the
shop PC; phones on the same Wi-Fi connect to it. Nothing is installed from an app store.

**Set-up page with QR codes:** the phone icon at the bottom of the sidebar (or Settings → Phones & tablets, `/connect`)
shows two QR codes — *1. set up the phone* (once per phone) and *2. open the app*. The set-up page opened on the phone
walks through the steps for iPhone and Android.
Screenshots: [the page on the PC](docs/screenshots/phones-connect.png), [set-up on the phone](docs/screenshots/phone-setup.png).

**Certificate (https without warnings):** browsers allow the microphone, installing the app and offline support only
on trusted https pages, and public certificates can't be issued for a PC on a private network. So the app creates
its own small certificate authority in `data/tls/` (`ca.crt`, kept for 10 years) and signs the server certificate
with it; the server certificate is renewed automatically when the PC's address changes, without the phones having to
do anything. Each phone installs `ca.crt` once. The CA is **name-constrained** to private network addresses,
`localhost`, `*.local` and the PC's name, so even if its key were stolen it could not be used to impersonate any
internet site. Nothing is added to Windows: the shop PC itself uses `http://localhost:8080`, which browsers already
treat as secure. Can't install it on a phone? Accept the browser warning instead: everything including the microphone works, only installing it as an
app does not.

**Home-screen app:** a web app manifest, generated icons (company logo, or the monogram such as "ÇT") and a service
worker make it installable: Safari → Share → *Add to Home Screen*, Chrome → ⋮ → *Install app*. It opens full screen,
with shortcuts to the assistant and "New service order". The service worker never caches business data; it only
shows a friendly page when the PC can't be reached.

**Ports:** `python run.py --https` serves the app on 8443 and a small helper on 8080 that serves the set-up page and
certificate over plain http (a phone can't open an https page it doesn't trust yet) and redirects everything else
to https. Reserve a fixed address for the PC in the router (DHCP reservation) so the phones' shortcuts keep working.
In Docker, set `ERP_LAN_IP` to the host's LAN address (see `docker-compose.yml`).

### Network security

The app is meant to run only on the shop's local network — never port-forward it to the internet. Two defences
harden it against the risks that remain when work phones and the PC are also online:

- **Login lockout.** After 5 wrong passwords for an account (or from one device) logins are refused for a growing
  cool-off (30 s, then doubling), so a device that catches malware can't brute-force the login.
- **Host allow-list.** Requests are only served for this PC's own names and LAN addresses. This blocks *DNS
  rebinding*, where a web page a technician visits tries to make their browser reach the app under an
  attacker-controlled hostname. Combined with the certificate, CSRF tokens and `SameSite` cookies, a malicious
  website cannot read or change shop data through a phone that is on the shop Wi-Fi.

The biggest remaining risk is the **PC itself**: it holds the database, backups and integrator credentials, so treat
it as an appliance — don't use it for casual web browsing or email, use a strong Wi-Fi password, and give each person
their own account with a real password.

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
  services/integrators/     # integrator adapters + SOAP helpers
  services/ubl_profile.py   # per-integrator XML adaptation, XSLT embedding and rendering
  services/backup.py        # backup / restore / scheduler
  services/workshop.py      # business operations shared by pages and the assistant
  services/assistant/       # command parser, local model client, tools, conversation logic, speech,
                            # bundled Ollama manager (start/stop, model download)
  services/mobile.py        # phones: addresses, QR codes, app icons, http helper for https mode
  tls.py                    # shop certificate authority + server certificate
  winsetup.py               # Windows set-up used by Kur.bat / Kaldir.bat / Durdur.bat / Telefon-Izni.bat
scripts/smoke_windows.py    # installs and tests the built package on Windows (GitHub Actions)
scripts/build_bundle.py     # Windows package builder (portable Python + app + Ollama)
  routes/                   # Flask blueprints
  templates/, static/       # server-rendered UI, no build step, no CDN (works offline)
  translations_tr.py        # Turkish UI strings
```

UI strings are written in English in the code and translated through `app/translations_tr.py`; the test suite fails
if a string has no Turkish translation.
