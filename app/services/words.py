"""Turkish amount-in-words ("Yalnız ... TL ... Kr"), customarily printed on invoices."""

from decimal import Decimal

ONES = ["", "Bir", "İki", "Üç", "Dört", "Beş", "Altı", "Yedi", "Sekiz", "Dokuz"]
TENS = ["", "On", "Yirmi", "Otuz", "Kırk", "Elli", "Altmış", "Yetmiş", "Seksen", "Doksan"]
GROUPS = ["", "Bin", "Milyon", "Milyar", "Trilyon"]


def _three(n):
    h, rest = divmod(n, 100)
    t, o = divmod(rest, 10)
    out = ""
    if h:
        out += ("" if h == 1 else ONES[h]) + "Yüz"
    return out + TENS[t] + ONES[o]


def int_to_words(n):
    if n == 0:
        return "Sıfır"
    parts = []
    gi = 0
    while n > 0:
        n, chunk = divmod(n, 1000)
        if chunk:
            word = "" if (gi == 1 and chunk == 1) else _three(chunk)  # "Bin", not "BirBin"
            parts.append(word + GROUPS[gi])
        gi += 1
    return "".join(reversed(parts))


def amount_in_words(amount, currency="TL"):
    amount = Decimal(amount).quantize(Decimal("0.01"))
    lira = int(amount)
    kurus = int((amount - lira) * 100)
    text = f"Yalnız {int_to_words(lira)} {currency}"
    if kurus:
        text += f" {int_to_words(kurus)} Kr"
    return text
