"""Invoice arithmetic. All money is Decimal, rounded half-up to kuruş at line level."""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def r2(v):
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


def to_decimal(value, default="0"):
    """Parse user input like '1.234,56', '1234.56' or '1234,5' into a Decimal."""
    if value is None:
        return Decimal(default)
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    s = str(value).strip().replace(" ", "").replace("₺", "")
    if not s:
        return Decimal(default)
    if "," in s and "." in s:
        # whichever separator comes last is the decimal separator
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except Exception:
        return Decimal(default)


@dataclass
class LineCalc:
    gross: Decimal
    discount: Decimal
    net: Decimal
    vat: Decimal
    withholding: Decimal
    total: Decimal


@dataclass
class Totals:
    gross: Decimal = ZERO
    discount: Decimal = ZERO
    net: Decimal = ZERO
    vat: Decimal = ZERO
    withholding: Decimal = ZERO
    payable: Decimal = ZERO
    lines: list = field(default_factory=list)
    vat_breakdown: dict = field(default_factory=dict)  # rate -> {"base": x, "vat": y}

    @property
    def total_with_vat(self):
        return self.net + self.vat


def compute_line(qty, unit_price, discount_rate=0, vat_rate=0, withholding_rate=0):
    qty = to_decimal(qty)
    unit_price = to_decimal(unit_price)
    gross = r2(qty * unit_price)
    discount = r2(gross * to_decimal(discount_rate) / 100)
    net = gross - discount
    vat = r2(net * Decimal(vat_rate) / 100)
    withholding = r2(vat * Decimal(withholding_rate) / 100)
    return LineCalc(gross, discount, net, vat, withholding, net + vat)


def compute_totals(lines, withholding_rate=0):
    """Accepts any objects with qty, unit_price, discount_rate, vat_rate attributes."""
    t = Totals()
    for ln in lines:
        c = compute_line(ln.qty, ln.unit_price, ln.discount_rate or 0, ln.vat_rate or 0, withholding_rate)
        t.lines.append(c)
        t.gross += c.gross
        t.discount += c.discount
        t.net += c.net
        t.vat += c.vat
        t.withholding += c.withholding
        b = t.vat_breakdown.setdefault(int(ln.vat_rate or 0), {"base": ZERO, "vat": ZERO, "withholding": ZERO})
        b["base"] += c.net
        b["vat"] += c.vat
        b["withholding"] += c.withholding
    t.payable = t.net + t.vat - t.withholding
    t.vat_breakdown = dict(sorted(t.vat_breakdown.items()))
    return t


def split_gross(total, vat_rate):
    """Given a VAT-inclusive amount, return (net, vat)."""
    total = to_decimal(total)
    net = r2(total / (1 + Decimal(vat_rate) / 100))
    return net, total - net
