"""Realistic demo data for a power-tool repair shop. All names and tax numbers are fictional."""

import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from .extensions import db
from .models import (
    Account,
    Contact,
    Expense,
    Invoice,
    InvoiceLine,
    Product,
    ServiceOrder,
    ServiceOrderEvent,
    ServiceOrderLine,
    Setting,
    Transaction,
    User,
)
from .services import invoicing
from .services.calc import r2


def fake_tckn(rng):
    while True:
        d = [rng.randint(1, 9)] + [rng.randint(0, 9) for _ in range(8)]
        d10 = ((sum(d[0:9:2]) * 7) - sum(d[1:8:2])) % 10
        d.append(d10)
        d.append(sum(d) % 10)
        return "".join(map(str, d))


def seed(rng_seed=42):
    rng = random.Random(rng_seed)
    today = date.today()

    company = {
        "name": "Güven Elektrikli El Aletleri Servis Ltd. Şti.",
        "tax_id": "3890412257",
        "tax_office": "İkitelli",
        "mersis": "0389041225700015",
        "trade_registry": "812345",
        "address": "İkitelli OSB, Demirciler Sitesi 4. Cad. No: 18",
        "district": "Başakşehir",
        "city": "İstanbul",
        "postal_code": "34490",
        "country": "Türkiye",
        "phone": "0212 555 18 18",
        "email": "servis@guvenalet.example",
        "website": "",
        "iban": "TR12 0006 4000 0011 2345 6789 01",
    }
    for k, v in company.items():
        Setting.set(f"company.{k}", v)
    Setting.set("invoice.footer_note", "Ödemelerinizi IBAN numaramıza açıklama kısmına fatura numarasını yazarak yapınız.")

    if not User.query.filter_by(username="demo").first():
        admin = User(username="demo", full_name="Demo Yönetici", role="admin", is_technician=False)
        admin.set_password("demo1234")
        db.session.add(admin)
    else:
        admin = User.query.filter_by(username="demo").first()
    techs = []
    for uname, name in (("mehmet", "Mehmet Yılmaz"), ("ayse", "Ayşe Demir")):
        u = User.query.filter_by(username=uname).first() or User(username=uname, full_name=name, role="staff")
        u.set_password("demo1234")
        db.session.add(u)
        techs.append(u)

    cash = Account(name="Merkez Kasa", kind="cash", opening_balance=Decimal("2500"))
    bank = Account(name="İş Bankası TL", kind="bank", iban="TR120006400000112345678901",
                   opening_balance=Decimal("85000"))
    db.session.add_all([cash, bank])

    companies = [
        ("Yıldız İnşaat Taahhüt A.Ş.", "9870011223", "Kozyatağı", "Ataşehir", True),
        ("Anadolu Metal Sanayi Ltd. Şti.", "0680045512", "İkitelli", "Başakşehir", False),
        ("Kartal Mobilya Dekorasyon", "5230098765", "Kartal", "Kartal", False),
        ("Deniz Yapı Market Ltd. Şti.", "2950033441", "Beylikdüzü", "Beylikdüzü", False),
        ("Ege Tesisat Mühendislik", "3310077889", "Kadıköy", "Kadıköy", False),
        ("Boğaziçi Belediyesi Fen İşleri", "1760012345", "Beşiktaş", "Beşiktaş", True),
        ("Marmara Otomotiv Servis", "6120054321", "Esenyurt", "Esenyurt", False),
    ]
    contacts = []
    for name, vkn, office, district, wh in companies:
        c = Contact(kind="customer", is_company=True, name=name, tax_id=vkn, tax_office=office,
                    address=f"{rng.choice(['Atatürk', 'Cumhuriyet', 'İstiklal', 'Fatih'])} Cad. No: {rng.randint(1, 200)}",
                    district=district, city="İstanbul", phone=f"0212 {rng.randint(200, 999)} {rng.randint(10, 99)} {rng.randint(10, 99)}",
                    email=f"muhasebe@{name.split()[0].lower().replace('ı', 'i').replace('ğ', 'g').replace('ö', 'o').replace('ü', 'u').replace('ş', 's').replace('ç', 'c')}.example",
                    efatura_user=True, efatura_alias=f"urn:mail:defaultpk@{vkn}.test",
                    efatura_checked_at=datetime.now().replace(microsecond=0), withholding_buyer=wh)
        contacts.append(c)
    people = [("Hasan", "Kaya"), ("Elif", "Şahin"), ("Murat", "Çelik"), ("Zeynep", "Arslan"), ("Emre", "Koç"),
              ("Fatma", "Öztürk"), ("Burak", "Aydın")]
    for fn, ln in people:
        contacts.append(Contact(kind="customer", is_company=False, name=f"{fn} {ln}", first_name=fn, last_name=ln,
                                tax_id=fake_tckn(rng), address=f"{rng.choice(['Lale', 'Gül', 'Menekşe'])} Sok. No: {rng.randint(1, 60)}",
                                district=rng.choice(["Bağcılar", "Küçükçekmece", "Bahçelievler"]), city="İstanbul",
                                phone=f"05{rng.randint(30, 59)} {rng.randint(100, 999)} {rng.randint(10, 99)} {rng.randint(10, 99)}"))
    suppliers = [
        Contact(kind="supplier", is_company=True, name="Bosch Yetkili Parça Dağıtım A.Ş.", tax_id="1840056677",
                tax_office="Maslak", district="Sarıyer", city="İstanbul"),
        Contact(kind="supplier", is_company=True, name="Teknik Rulman Ticaret", tax_id="8210034455",
                tax_office="Karaköy", district="Beyoğlu", city="İstanbul"),
        Contact(kind="supplier", is_company=True, name="Demirciler Sitesi Yönetimi", tax_id="4410099887",
                tax_office="İkitelli", district="Başakşehir", city="İstanbul"),
    ]
    db.session.add_all(contacts + suppliers)

    parts = [
        ("KF-01", "Kömür fırça seti (çift)", "280", "95", 40), ("RT-18", "Rotor 18V (Bosch GSB)", "1850", "1100", 4),
        ("ST-230", "Stator 230V taşlama", "1450", "880", 3), ("SL-10", "Tetik şalter", "420", "180", 12),
        ("RL-608", "Rulman 608 2RS", "65", "22", 60), ("RL-6201", "Rulman 6201", "95", "35", 30),
        ("MD-13", "Anahtarlı mandren 13 mm", "520", "260", 6), ("DS-SET", "Dişli seti (kırıcı)", "2200", "1350", 2),
        ("KB-3M", "Güç kablosu 3 m", "240", "90", 15), ("BT-18", "Akü 18V 4.0Ah", "3450", "2400", 3),
        ("SC-18", "Şarj cihazı 18V", "1650", "1050", 1), ("YG-01", "Özel gres yağı 100 g", "180", "60", 20),
    ]
    products = {}
    for code, name, price, cost, stock in parts:
        p = Product(code=code, name=name, kind="part", unit="C62", price=Decimal(price), cost=Decimal(cost),
                    vat_rate=20, track_stock=True, stock_qty=Decimal(stock), min_stock=Decimal(3))
        products[code] = p
    for code, name, price, unit in (("ISC", "İşçilik", "750", "HUR"), ("ART", "Arıza tespiti", "350", "C62"),
                                    ("BKM", "Periyodik bakım", "900", "C62")):
        products[code] = Product(code=code, name=name, kind="service", unit=unit, price=Decimal(price), vat_rate=20,
                                 track_stock=False)
    db.session.add_all(products.values())
    db.session.flush()

    devices = [("Darbeli matkap", "Bosch", "GSB 18V-55"), ("Avuç taşlama", "Makita", "GA9020"),
               ("Kırıcı delici", "Bosch", "GBH 5-40"), ("Şarjlı vidalama", "DeWalt", "DCD796"),
               ("Daire testere", "Makita", "HS7601"), ("Dekupaj testere", "Metabo", "STEB 65"),
               ("Kaynak makinesi", "Magmaweld", "Monostick 200i"), ("Kompresör", "Einhell", "TE-AC 270"),
               ("Hilti kırıcı", "Hilti", "TE 1000-AVR"), ("Planya", "Makita", "KP0800")]
    complaints = ["Çalışmıyor, düğmeye basınca ses yok.", "Kıvılcım atıyor ve güç kaybı var.",
                  "Mandren boşluk yapıyor.", "Aşırı ısınıyor, yanık kokusu var.", "Darbe fonksiyonu çalışmıyor.",
                  "Şarj etmiyor.", "Kablo kopmuş, fiş değişecek.", "Rulmandan ses geliyor."]

    def order_lines(o):
        picks = rng.sample(["KF-01", "SL-10", "RL-608", "RL-6201", "KB-3M", "YG-01", "MD-13", "RT-18"],
                           k=rng.randint(1, 3))
        lines = []
        for code in picks:
            p = products[code]
            qty = Decimal(2 if code.startswith("RL") else 1)
            lines.append(ServiceOrderLine(product=p, description=p.name, qty=qty, unit=p.unit, unit_price=p.price,
                                          vat_rate=20, position=len(lines)))
        hrs = Decimal(rng.choice(["1", "1.5", "2", "3"]))
        lab = products["ISC"]
        lines.append(ServiceOrderLine(product=lab, description="İşçilik", qty=hrs, unit="HUR", unit_price=lab.price,
                                      vat_rate=20, position=len(lines)))
        o.lines = lines

    # history: invoices over the past ~11 months, then open orders
    seq = 0
    start = today - timedelta(days=345)
    invoice_dates = sorted(start + timedelta(days=rng.randint(0, 344)) for _ in range(230))
    for d in invoice_dates:
        seq += 1
        c = rng.choice(contacts)
        dev = rng.choice(devices)
        received = datetime.combine(d - timedelta(days=rng.randint(2, 7)), time(rng.randint(9, 17), rng.choice([0, 15, 30, 45])))
        o = ServiceOrder(number=f"SRV-{received.year}-{seq:05d}", contact=c, status="ready", received_at=received,
                         device_type=dev[0], brand=dev[1], model=dev[2], serial_no=f"{rng.randint(100000, 999999)}",
                         complaint=rng.choice(complaints), technician=rng.choice(techs),
                         diagnosis="Parçalar kontrol edildi, arızalı parça değiştirildi.",
                         work_done="Değişim ve genel bakım yapıldı, test edildi.",
                         promised_date=d)
        db.session.add(o)
        order_lines(o)
        o.events.append(ServiceOrderEvent(kind="status", message="received", at=received))
        db.session.add(o)
        inv = Invoice(contact=c, issue_date=d, due_date=d + timedelta(days=30 if c.is_company else 0),
                      profile="TICARIFATURA" if c.efatura_user else "EARSIVFATURA", type_code="SATIS",
                      created_by_id=None, notes=f"{dev[0]} {dev[1]} {dev[2]}")
        db.session.add(inv)
        if c.withholding_buyer:
            inv.type_code, inv.withholding_code, inv.withholding_rate = "TEVKIFAT", "603", 70
        inv.lines = [InvoiceLine(position=l.position, product=l.product, description=l.description, qty=l.qty,
                                 unit=l.unit, unit_price=l.unit_price, vat_rate=l.vat_rate, discount_rate=0)
                     for l in o.lines]
        if c.is_company and rng.random() < 0.35:
            # companies often send several tools at once: add a maintenance line
            bkm = products["BKM"]
            inv.lines.append(InvoiceLine(position=len(inv.lines), product=bkm, description="Periyodik bakım",
                                         qty=Decimal(rng.randint(2, 6)), unit="C62", unit_price=bkm.price,
                                         vat_rate=20, discount_rate=Decimal(10)))
        inv.recompute()
        db.session.add(inv)
        db.session.flush()
        o.invoice_id = inv.id
        invoicing.issue(inv)
        invoicing.send(inv)
        if inv.status == "sent" and d < today - timedelta(days=10):
            invoicing.refresh_status(inv)
        # individuals pay at the counter; companies pay later, some are overdue
        age = (today - d).days
        paid = (not c.is_company) or (age > 45 and rng.random() < 0.93) or (age <= 45 and rng.random() < 0.35)
        if paid:
            acct = bank if c.is_company else rng.choice([cash, bank])
            pay_date = min(today, d + timedelta(days=rng.randint(5, 40) if c.is_company else 0))
            db.session.add(Transaction(date=pay_date, account=acct, direction="in", amount=inv.payable,
                                       kind="collection", contact=c, invoice=inv, description=inv.number,
                                       category="Satış"))
        db.session.commit()

    # open repair orders in different stages
    stages = ["received", "received", "diagnosing", "diagnosing", "awaiting_approval", "in_repair", "in_repair",
              "awaiting_parts", "ready", "ready", "in_repair", "received"]
    for i, st in enumerate(stages):
        seq += 1
        c = rng.choice(contacts)
        dev = rng.choice(devices)
        received = datetime.combine(today - timedelta(days=rng.randint(0, 9)), time(rng.randint(9, 17), 0))
        o = ServiceOrder(number=f"SRV-{today.year}-{seq:05d}", contact=c, status=st, received_at=received,
                         device_type=dev[0], brand=dev[1], model=dev[2], serial_no=f"{rng.randint(100000, 999999)}",
                         accessories=rng.choice(["Çanta, 2 akü", "Yan kol", "Yok", "Şarj cihazı"]),
                         complaint=rng.choice(complaints), technician=rng.choice(techs),
                         priority=rng.choice(["normal", "normal", "high", "low"]),
                         promised_date=today + timedelta(days=rng.randint(-2, 5)),
                         under_warranty=rng.random() < 0.15)
        db.session.add(o)
        if st in ("awaiting_approval", "in_repair", "awaiting_parts", "ready"):
            o.diagnosis = "Rotor sargısı yanmış, kömürler bitmiş."
            order_lines(o)
            o.estimate = o.totals().payable
        if st == "ready":
            o.work_done = "Rotor ve kömür değişti, test edildi."
        o.events.append(ServiceOrderEvent(kind="status", message="received", at=received))
        if st != "received":
            o.events.append(ServiceOrderEvent(kind="status", message=st, at=received + timedelta(hours=20)))
        db.session.add(o)

    # expenses: monthly fixed costs + parts purchases
    m = date(start.year, start.month, 1)
    while m <= today:
        for cat, amount, sup in (("Kira", "14000", suppliers[2]), ("Elektrik / Su / Doğalgaz", "3200", None),
                                 ("İnternet / Telefon", "850", None), ("Muhasebe / Danışmanlık", "4500", None)):
            d = m + timedelta(days=rng.randint(0, 9))
            if d > today:
                continue
            net = Decimal(amount) * Decimal(rng.uniform(0.9, 1.15)).quantize(Decimal("0.01"))
            e = Expense(date=d, category=cat, contact=sup, description=cat, net=r2(net), vat_rate=20)
            e.vat = r2(e.net * Decimal("0.2"))
            e.total = e.net + e.vat
            db.session.add(e)
            db.session.flush()
            db.session.add(Transaction(date=d, account=bank, direction="out", amount=e.total, kind="expense",
                                       contact=sup, expense_id=e.id, category=cat, description=cat))
        for _ in range(1):
            d = m + timedelta(days=rng.randint(5, 26))
            if d > today:
                continue
            sup = rng.choice(suppliers[:2])
            e = Expense(date=d, category="Parça / Malzeme", contact=sup, doc_no=f"ALF{rng.randint(10000, 99999)}",
                        description="Yedek parça alımı", net=Decimal(rng.randint(40, 110) * 100), vat_rate=20)
            e.vat = r2(e.net * Decimal("0.2"))
            e.total = e.net + e.vat
            db.session.add(e)
            db.session.flush()
            if d < today - timedelta(days=20):
                db.session.add(Transaction(date=d + timedelta(days=15), account=bank, direction="out",
                                           amount=e.total, kind="payment", contact=sup, description=e.doc_no))
        dep = m.replace(day=27)
        if dep <= today:
            grp = f"demo-dep-{dep:%Y%m}"
            db.session.add(Transaction(date=dep, account=cash, direction="out", amount=Decimal("14000"),
                                       kind="transfer", description="Bankaya nakit yatırıldı", transfer_group=grp))
            db.session.add(Transaction(date=dep, account=bank, direction="in", amount=Decimal("14000"),
                                       kind="transfer", description="Bankaya nakit yatırıldı", transfer_group=grp))
        m = (m + timedelta(days=32)).replace(day=1)
    # one draft invoice waiting
    c = contacts[1]
    draft = Invoice(contact=c, issue_date=today, profile="TICARIFATURA", type_code="SATIS",
                    due_date=today + timedelta(days=30))
    draft.lines = [InvoiceLine(position=0, product=products["BKM"], description="Periyodik bakım - 6 adet taşlama",
                               qty=Decimal(6), unit="C62", unit_price=Decimal("900"), vat_rate=20, discount_rate=10)]
    draft.recompute()
    db.session.add(draft)
    # realistic current stock levels (a few below minimum so the warning shows)
    for i, p in enumerate(sorted((p for p in products.values() if p.kind == "part"), key=lambda p: p.code)):
        p.stock_qty = Decimal(rng.randint(1, 3) if i % 4 == 0 else rng.randint(6, 40))
    db.session.commit()
