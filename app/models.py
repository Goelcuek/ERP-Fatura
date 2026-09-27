import json
import os
import uuid as uuidlib
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import Money, Qty, db

ZERO = Decimal("0.00")


def now():
    return datetime.now().replace(microsecond=0)


# ---------------------------------------------------------------- users & settings


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(60), unique=True, nullable=False)
    full_name = db.Column(db.String(120), nullable=False, default="")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="staff")  # admin | staff
    is_technician = db.Column(db.Boolean, nullable=False, default=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    lang = db.Column(db.String(5), nullable=False, default="tr")
    created_at = db.Column(db.DateTime, nullable=False, default=now)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def initials(self):
        parts = (self.full_name or self.username).split()
        return "".join(p[0] for p in parts[:2]).upper()


class Setting(db.Model):
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, nullable=False, default="null")

    DEFAULTS = {
        "company.name": "",
        "company.tax_id": "",
        "company.tax_office": "",
        "company.mersis": "",
        "company.trade_registry": "",
        "company.address": "",
        "company.district": "",
        "company.city": "",
        "company.postal_code": "",
        "company.country": "Türkiye",
        "company.phone": "",
        "company.email": "",
        "company.website": "",
        "company.iban": "",
        "company.logo": "",
        "invoice.efatura_prefix": "EFT",
        "invoice.earsiv_prefix": "ARS",
        "invoice.efatura_start": 1,  # first sequence number to use (continue from a previous system)
        "invoice.earsiv_start": 1,
        "invoice.default_profile": "TICARIFATURA",
        "invoice.default_vat": 20,
        "invoice.default_due_days": 30,
        "invoice.footer_note": "",
        "orders.prefix": "SRV",
        "orders.terms": "Cihaz teslim tarihinden itibaren 90 gün içinde teslim alınmayan "
        "ürünlerden firmamız sorumlu değildir. Onarım garantisi değiştirilen parçalar için 6 aydır.",
        "integrator.name": "mock",
        "integrator.config": {},
        "assistant.enabled": True,
        "assistant.use_llm": True,
        "assistant.server": "auto",  # auto: the bundled Ollama when present, else base_url
        "assistant.base_url": os.environ.get("ERP_ASSISTANT_BASE_URL", "http://127.0.0.1:11434/v1"),
        "assistant.auto_download": True,
        "assistant.model": "qwen3.5:2b",
        "assistant.api_key": "",
        "assistant.temperature": 0.2,
        "assistant.timeout": 120,
        "voice.engine": "auto",  # auto | browser | local | off
        "voice.lang": "tr-TR",
        "voice.whisper_model": "small",
        "voice.speak_replies": False,
        "voice.auto_send": True,
        "backup.enabled": True,
        "backup.interval_hours": 24,
        "backup.keep": 30,
        "backup.dir": "",
        "backup.extra_dir": "",
        "backup.last_at": "",
        "backup.last_error": "",
    }

    @classmethod
    def get(cls, key, default=None):
        row = db.session.get(cls, key)
        if row is None:
            return cls.DEFAULTS.get(key, default) if default is None else default
        return json.loads(row.value)

    @classmethod
    def set(cls, key, value):
        row = db.session.get(cls, key)
        if row is None:
            row = cls(key=key)
            db.session.add(row)
        row.value = json.dumps(value, ensure_ascii=False)

    @classmethod
    def group(cls, prefix):
        out = {k[len(prefix) + 1 :]: v for k, v in cls.DEFAULTS.items() if k.startswith(prefix + ".")}
        for row in cls.query.filter(cls.key.like(prefix + ".%")).all():
            out[row.key[len(prefix) + 1 :]] = json.loads(row.value)
        return out


# ---------------------------------------------------------------- contacts


class Contact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False, default="customer")  # customer | supplier | both
    is_company = db.Column(db.Boolean, nullable=False, default=True)
    name = db.Column(db.String(250), nullable=False)  # ünvan or full name
    first_name = db.Column(db.String(100), default="")
    last_name = db.Column(db.String(100), default="")
    tax_id = db.Column(db.String(11), default="", index=True)  # VKN (10) or TCKN (11)
    tax_office = db.Column(db.String(120), default="")
    address = db.Column(db.String(400), default="")
    district = db.Column(db.String(100), default="")
    city = db.Column(db.String(100), default="")
    postal_code = db.Column(db.String(10), default="")
    country = db.Column(db.String(60), default="Türkiye")
    phone = db.Column(db.String(40), default="")
    email = db.Column(db.String(160), default="")
    contact_person = db.Column(db.String(120), default="")
    notes = db.Column(db.Text, default="")
    efatura_user = db.Column(db.Boolean, nullable=False, default=False)
    efatura_alias = db.Column(db.String(200), default="")
    efatura_checked_at = db.Column(db.DateTime)
    withholding_buyer = db.Column(db.Boolean, nullable=False, default=False)  # belirlenmiş alıcı (KDV tevkifatı)
    archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=now)

    @property
    def id_scheme(self):
        return "TCKN" if len(self.tax_id or "") == 11 else "VKN"

    @property
    def display_address(self):
        parts = [self.address, " ".join(p for p in [self.district, self.city] if p)]
        return ", ".join(p for p in parts if p)

    def balance(self):
        return contact_balance(self.id)


def contact_balance(contact_id):
    """Positive: contact owes us. Negative: we owe the contact."""
    sales = (
        db.session.query(func.coalesce(func.sum(Invoice.payable), 0))
        .filter(Invoice.contact_id == contact_id, Invoice.status.in_(Invoice.POSTED), Invoice.type_code != "IADE")
        .scalar()
    )
    returns = (
        db.session.query(func.coalesce(func.sum(Invoice.payable), 0))
        .filter(Invoice.contact_id == contact_id, Invoice.status.in_(Invoice.POSTED), Invoice.type_code == "IADE")
        .scalar()
    )
    purchases = (
        db.session.query(func.coalesce(func.sum(Expense.total), 0)).filter(Expense.contact_id == contact_id).scalar()
    )
    money_in = (
        db.session.query(func.coalesce(func.sum(Transaction.amount), 0))
        .filter(Transaction.contact_id == contact_id, Transaction.direction == "in")
        .scalar()
    )
    money_out = (
        db.session.query(func.coalesce(func.sum(Transaction.amount), 0))
        .filter(Transaction.contact_id == contact_id, Transaction.direction == "out")
        .scalar()
    )
    d = lambda v: Decimal(v or 0)  # noqa: E731
    return (d(sales) - d(returns) - d(purchases) - d(money_in) + d(money_out)).quantize(Decimal("0.01"))


# ---------------------------------------------------------------- products & parts


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(60), default="", index=True)
    name = db.Column(db.String(250), nullable=False)
    kind = db.Column(db.String(10), nullable=False, default="part")  # part | service
    unit = db.Column(db.String(5), nullable=False, default="C62")  # UN/ECE Rec 20: C62 adet, HUR saat
    price = db.Column(Money(), nullable=False, default=ZERO)
    cost = db.Column(Money(), nullable=False, default=ZERO)
    vat_rate = db.Column(db.Integer, nullable=False, default=20)
    track_stock = db.Column(db.Boolean, nullable=False, default=True)
    stock_qty = db.Column(Qty(), nullable=False, default=Decimal("0"))
    min_stock = db.Column(Qty(), nullable=False, default=Decimal("0"))
    archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=now)

    @property
    def low_stock(self):
        return self.track_stock and self.kind == "part" and self.stock_qty <= self.min_stock


# UN/ECE Recommendation 20 unit codes used in UBL-TR, with display names (translated in the UI)
UNITS = {
    "C62": "Piece",
    "HUR": "Hour",
    "KGM": "Kg",
    "MTR": "Metre",
    "LTR": "Litre",
    "SET": "Set",
    "PA": "Pack",
}

VAT_RATES = [0, 1, 10, 20]

# KDV istisna (exemption) codes most relevant for a repair shop. Full list is published by GİB.
EXEMPTION_CODES = {
    "301": "11/1-a Mal ihracatı",
    "302": "11/1-a Hizmet ihracatı",
    "350": "Diğerleri",
    "351": "KDV - İstisna Olmayan Diğer",
}

# KDV tevkifat codes. 603 is the one that applies to tool/machine repair.
WITHHOLDING_CODES = {
    "603": ("Makine, teçhizat, demirbaş ve taşıtlara ait tadil, bakım ve onarım hizmetleri", 70),
    "601": ("Yapım işleri ile bu işlerle birlikte ifa edilen mühendislik-mimarlık ve etüt-proje hizmetleri", 40),
    "606": ("İşgücü temin hizmetleri", 90),
    "620": ("Bakır, çinko, alüminyum ve kurşun ürünlerinin teslimi", 70),
    "624": ("Yük taşımacılığı hizmeti", 20),
    "627": ("Demir-çelik ürünlerinin teslimi", 50),
}


# ---------------------------------------------------------------- repair / service orders


class ServiceOrder(db.Model):
    STATUSES = [
        "received",
        "diagnosing",
        "awaiting_approval",
        "in_repair",
        "awaiting_parts",
        "ready",
        "delivered",
        "cancelled",
    ]
    OPEN = ["received", "diagnosing", "awaiting_approval", "in_repair", "awaiting_parts", "ready"]

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(30), unique=True, nullable=False)
    contact_id = db.Column(db.Integer, db.ForeignKey("contact.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="received", index=True)
    priority = db.Column(db.String(10), nullable=False, default="normal")  # low | normal | high
    received_at = db.Column(db.DateTime, nullable=False, default=now)
    promised_date = db.Column(db.Date)
    delivered_at = db.Column(db.DateTime)
    device_type = db.Column(db.String(120), default="")  # e.g. Darbeli matkap
    brand = db.Column(db.String(80), default="")
    model = db.Column(db.String(120), default="")
    serial_no = db.Column(db.String(120), default="")
    accessories = db.Column(db.String(400), default="")
    complaint = db.Column(db.Text, default="")
    diagnosis = db.Column(db.Text, default="")
    work_done = db.Column(db.Text, default="")
    internal_notes = db.Column(db.Text, default="")
    under_warranty = db.Column(db.Boolean, nullable=False, default=False)
    estimate = db.Column(Money())
    customer_approved = db.Column(db.Boolean)
    technician_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    updated_at = db.Column(db.DateTime, nullable=False, default=now, onupdate=now)

    contact = db.relationship("Contact", backref="service_orders")
    technician = db.relationship("User")
    invoice = db.relationship("Invoice", foreign_keys=[invoice_id])
    lines = db.relationship(
        "ServiceOrderLine", backref="order", cascade="all, delete-orphan", order_by="ServiceOrderLine.position"
    )
    events = db.relationship(
        "ServiceOrderEvent", backref="order", cascade="all, delete-orphan", order_by="ServiceOrderEvent.at.desc()"
    )

    @property
    def is_open(self):
        return self.status in self.OPEN

    @property
    def device_label(self):
        return " ".join(p for p in [self.brand, self.model] if p) or self.device_type or "—"

    def totals(self):
        from .services.calc import compute_totals

        return compute_totals(self.lines)


class ServiceOrderLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("service_order.id"), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"))
    description = db.Column(db.String(300), nullable=False)
    qty = db.Column(Qty(), nullable=False, default=Decimal("1"))
    unit = db.Column(db.String(5), nullable=False, default="C62")
    unit_price = db.Column(Money(), nullable=False, default=ZERO)
    discount_rate = db.Column(Money(), nullable=False, default=ZERO)
    vat_rate = db.Column(db.Integer, nullable=False, default=20)

    product = db.relationship("Product")


class ServiceOrderEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("service_order.id"), nullable=False)
    at = db.Column(db.DateTime, nullable=False, default=now)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    kind = db.Column(db.String(20), nullable=False, default="note")  # status | note
    message = db.Column(db.Text, nullable=False, default="")
    source = db.Column(db.String(12), nullable=False, default="web")  # web | assistant

    user = db.relationship("User")


# ---------------------------------------------------------------- invoices


class Invoice(db.Model):
    # Statuses that count as issued (affect balances & reports)
    POSTED = ["issued", "sent", "accepted", "exported"]
    PROFILES = ["TEMELFATURA", "TICARIFATURA", "EARSIVFATURA"]
    TYPES = ["SATIS", "IADE", "TEVKIFAT", "ISTISNA"]

    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuidlib.uuid4()))
    number = db.Column(db.String(16), unique=True)  # assigned when issued: ABC2026000000001
    contact_id = db.Column(db.Integer, db.ForeignKey("contact.id"), nullable=False)
    profile = db.Column(db.String(20), nullable=False, default="TICARIFATURA")
    type_code = db.Column(db.String(20), nullable=False, default="SATIS")
    issue_date = db.Column(db.Date, nullable=False, default=date.today)
    issue_time = db.Column(db.Time)
    due_date = db.Column(db.Date)
    currency = db.Column(db.String(3), nullable=False, default="TRY")
    notes = db.Column(db.Text, default="")
    order_ref = db.Column(db.String(60), default="")  # customer's PO number
    withholding_code = db.Column(db.String(5), default="")
    withholding_rate = db.Column(db.Integer, default=0)  # percent of VAT withheld, e.g. 70 for 7/10
    exemption_code = db.Column(db.String(5), default="")
    exemption_reason = db.Column(db.String(250), default="")
    return_ref_number = db.Column(db.String(16), default="")  # İADE: original invoice number
    return_ref_date = db.Column(db.Date)

    # cached totals (recomputed on every save)
    gross_total = db.Column(Money(), nullable=False, default=ZERO)
    discount_total = db.Column(Money(), nullable=False, default=ZERO)
    net_total = db.Column(Money(), nullable=False, default=ZERO)
    vat_total = db.Column(Money(), nullable=False, default=ZERO)
    withholding_total = db.Column(Money(), nullable=False, default=ZERO)
    payable = db.Column(Money(), nullable=False, default=ZERO)

    status = db.Column(db.String(20), nullable=False, default="draft", index=True)
    # draft | issued | sent | accepted | rejected | error | exported | cancelled
    integrator = db.Column(db.String(30), default="")
    integrator_ref = db.Column(db.String(120), default="")
    status_message = db.Column(db.Text, default="")
    sent_at = db.Column(db.DateTime)
    xml_path = db.Column(db.String(300), default="")  # canonical UBL-TR, frozen when issued
    sent_xml_path = db.Column(db.String(300), default="")  # as adapted for and sent to the integrator
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    updated_at = db.Column(db.DateTime, nullable=False, default=now, onupdate=now)

    contact = db.relationship("Contact", backref="invoices")
    lines = db.relationship(
        "InvoiceLine", backref="invoice", cascade="all, delete-orphan", order_by="InvoiceLine.position"
    )
    events = db.relationship(
        "InvoiceEvent", backref="invoice", cascade="all, delete-orphan", order_by="InvoiceEvent.at.desc()"
    )
    service_orders = db.relationship("ServiceOrder", foreign_keys="ServiceOrder.invoice_id", viewonly=True)

    def __init__(self, **kw):
        kw.setdefault("uuid", str(uuidlib.uuid4()))
        super().__init__(**kw)

    @property
    def is_draft(self):
        return self.status == "draft"

    @property
    def is_editable(self):
        return self.status == "draft"

    @property
    def is_earsiv(self):
        return self.profile == "EARSIVFATURA"

    @property
    def paid_amount(self):
        v = (
            db.session.query(func.coalesce(func.sum(Transaction.amount), 0))
            .filter(Transaction.invoice_id == self.id)
            .scalar()
        )
        return Decimal(v or 0).quantize(Decimal("0.01"))

    @property
    def open_amount(self):
        if self.status not in self.POSTED:
            return ZERO
        return max(self.payable - self.paid_amount, ZERO)

    @property
    def is_overdue(self):
        return self.open_amount > 0 and self.due_date is not None and self.due_date < date.today()

    def recompute(self):
        from .services.calc import compute_totals

        t = compute_totals(self.lines, self.withholding_rate if self.type_code == "TEVKIFAT" else 0)
        self.gross_total = t.gross
        self.discount_total = t.discount
        self.net_total = t.net
        self.vat_total = t.vat
        self.withholding_total = t.withholding
        self.payable = t.payable
        return t

    def log(self, kind, message, user_id=None):
        self.events.append(InvoiceEvent(kind=kind, message=message, user_id=user_id))


class InvoiceLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"))
    description = db.Column(db.String(300), nullable=False)
    qty = db.Column(Qty(), nullable=False, default=Decimal("1"))
    unit = db.Column(db.String(5), nullable=False, default="C62")
    unit_price = db.Column(Money(), nullable=False, default=ZERO)
    discount_rate = db.Column(Money(), nullable=False, default=ZERO)
    vat_rate = db.Column(db.Integer, nullable=False, default=20)

    product = db.relationship("Product")


class InvoiceEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"), nullable=False)
    at = db.Column(db.DateTime, nullable=False, default=now)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    kind = db.Column(db.String(20), nullable=False, default="info")  # info | success | error
    message = db.Column(db.Text, nullable=False, default="")
    source = db.Column(db.String(12), nullable=False, default="web")  # web | assistant


# ---------------------------------------------------------------- bookkeeping


class Account(db.Model):
    """A place money lives: cash box, bank account, POS/credit card."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    kind = db.Column(db.String(10), nullable=False, default="cash")  # cash | bank | pos
    iban = db.Column(db.String(40), default="")
    opening_balance = db.Column(Money(), nullable=False, default=ZERO)
    archived = db.Column(db.Boolean, nullable=False, default=False)

    def balance(self):
        rows = (
            db.session.query(Transaction.direction, func.coalesce(func.sum(Transaction.amount), 0))
            .filter(Transaction.account_id == self.id)
            .group_by(Transaction.direction)
            .all()
        )
        sums = {d: Decimal(v) for d, v in rows}
        return (self.opening_balance + sums.get("in", ZERO) - sums.get("out", ZERO)).quantize(Decimal("0.01"))


class Transaction(db.Model):
    """A single money movement in or out of an account."""

    KINDS = ["collection", "payment", "income", "expense", "transfer"]

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False)
    direction = db.Column(db.String(3), nullable=False)  # in | out
    amount = db.Column(Money(), nullable=False)
    kind = db.Column(db.String(12), nullable=False)
    category = db.Column(db.String(80), default="")
    description = db.Column(db.String(300), default="")
    contact_id = db.Column(db.Integer, db.ForeignKey("contact.id"))
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"))
    expense_id = db.Column(db.Integer, db.ForeignKey("expense.id"))
    transfer_group = db.Column(db.String(36))
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=now)

    account = db.relationship("Account", backref="transactions")
    contact = db.relationship("Contact")
    invoice = db.relationship("Invoice", backref="payments")
    expense = db.relationship("Expense", backref="payments")

    @property
    def signed_amount(self):
        return self.amount if self.direction == "in" else -self.amount


class Expense(db.Model):
    """A purchase / cost document (gider, alış faturası). Its VAT is deductible (indirilecek KDV)."""

    CATEGORIES = [
        "Parça / Malzeme",
        "Kira",
        "Elektrik / Su / Doğalgaz",
        "İnternet / Telefon",
        "Personel / Maaş",
        "SGK / Vergi",
        "Kargo / Nakliye",
        "Yakıt / Araç",
        "Muhasebe / Danışmanlık",
        "Banka / POS Masrafı",
        "Ofis / Kırtasiye",
        "Diğer",
    ]

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    contact_id = db.Column(db.Integer, db.ForeignKey("contact.id"))
    doc_no = db.Column(db.String(60), default="")
    category = db.Column(db.String(80), nullable=False, default="Diğer")
    description = db.Column(db.String(300), default="")
    net = db.Column(Money(), nullable=False, default=ZERO)
    vat_rate = db.Column(db.Integer, nullable=False, default=20)
    vat = db.Column(Money(), nullable=False, default=ZERO)
    total = db.Column(Money(), nullable=False, default=ZERO)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=now)

    contact = db.relationship("Contact")

    @property
    def paid_amount(self):
        return sum((t.amount for t in self.payments), ZERO)


# ---------------------------------------------------------------- assistant


class AssistantConversation(db.Model):
    """One chat with the in-app assistant. `messages` is the model-facing history,
    `display` what the chat panel shows, `pending` actions waiting for the user's decision."""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    messages = db.Column(db.Text, nullable=False, default="[]")
    display = db.Column(db.Text, nullable=False, default="[]")
    pending = db.Column(db.Text, nullable=False, default="null")
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    updated_at = db.Column(db.DateTime, nullable=False, default=now, onupdate=now)

    def get(self, field):
        return json.loads(getattr(self, field))

    def put(self, field, value):
        setattr(self, field, json.dumps(value, ensure_ascii=False, default=str))
