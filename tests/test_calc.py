from decimal import Decimal as D
from types import SimpleNamespace as L

from app.services.calc import compute_line, compute_totals, split_gross, to_decimal
from app.services.words import amount_in_words


def test_to_decimal_accepts_turkish_and_english_formats():
    assert to_decimal("1.234,56") == D("1234.56")
    assert to_decimal("1,234.56") == D("1234.56")
    assert to_decimal("12,5") == D("12.5")
    assert to_decimal(" 750 ₺") == D("750")
    assert to_decimal("") == D("0")
    assert to_decimal("abc") == D("0")


def test_line_with_discount_and_vat():
    c = compute_line("3", "99.99", 10, 20)
    assert c.gross == D("299.97")
    assert c.discount == D("30.00")
    assert c.net == D("269.97")
    assert c.vat == D("53.99")
    assert c.total == D("323.96")


def test_totals_with_withholding_603():
    lines = [L(qty=D("1"), unit_price=D("1000"), discount_rate=0, vat_rate=20),
             L(qty=D("2"), unit_price=D("250"), discount_rate=0, vat_rate=20)]
    t = compute_totals(lines, withholding_rate=70)
    assert t.net == D("1500.00")
    assert t.vat == D("300.00")
    assert t.withholding == D("210.00")
    assert t.payable == D("1590.00")
    assert t.vat_breakdown[20]["base"] == D("1500.00")


def test_mixed_rates_breakdown():
    lines = [L(qty=1, unit_price=D("100"), discount_rate=0, vat_rate=20),
             L(qty=1, unit_price=D("100"), discount_rate=0, vat_rate=10)]
    t = compute_totals(lines)
    assert list(t.vat_breakdown) == [10, 20]
    assert t.vat == D("30.00")


def test_split_gross():
    assert split_gross("120", 20) == (D("100.00"), D("20.00"))
    net, vat = split_gross("99.99", 20)
    assert net + vat == D("99.99")


def test_amount_in_words():
    assert amount_in_words(D("1812.00")) == "Yalnız BinSekizYüzOnİki TL"
    assert amount_in_words(D("1001.50")) == "Yalnız BinBir TL Elli Kr"
    assert amount_in_words(D("2345678.05")) == "Yalnız İkiMilyonÜçYüzKırkBeşBinAltıYüzYetmişSekiz TL Beş Kr"
    assert amount_in_words(D("0.99")) == "Yalnız Sıfır TL DoksanDokuz Kr"
