"""Deterministic parser for the most common spoken/typed workshop commands.

Handled without any AI model, instantly and predictably:

    "bu işi bitirdim"                          -> current/only job -> ready for pickup
    "SRV-2026-00240 hazır, kömürleri değiştirdim" -> that job -> ready, work note saved
    "240 numaralı iş parça bekliyor"           -> awaiting parts
    "onarıma başladım" / "müşteri onayladı" / "teslim ettim"
    "not ekle: müşteri cuma alacak"            -> activity note

Anything that looks like a question, contains a negation, or is not recognised with
confidence returns None and is left to the language model (or a "please rephrase").
"""

import re
from dataclasses import dataclass, field

# Order matters: more specific intents first.
INTENTS = [
    ("approved", [r"müşteri onayladı", r"\bonayladı", r"onay verdi", r"teklifi kabul etti", r"\bapproved\b"]),
    ("delivered", [r"teslim ettim", r"teslim ettik", r"teslim edildi", r"müşteri(ye)? teslim", r"müşteri (aldı|götürdü)",
                   r"\bdelivered\b", r"picked up", r"handed over"]),
    ("cancelled", [r"iptal (et|edil|oldu)", r"vazgeçti", r"\bcancel"]),
    ("awaiting_parts", [r"parça bekl", r"parça (sipariş|gelmedi|yok|lazım)", r"parçaya bekl", r"waiting for parts?",
                        r"awaiting parts?"]),
    ("awaiting_approval", [r"onay bekl", r"onaya (gönderdim|sundum)", r"teklif (verdim|gönderdim|ilettim|iletildi)",
                           r"müşteriye (fiyat|teklif) (verdim|ilettim)", r"awaiting approval"]),
    ("ready", [r"bitirdim", r"bitirdik", r"\bbitti\b", r"tamamla(dım|dık|ndı)", r"\bhazır\b", r"onardım", r"onarıldı",
               r"tamir (ettim|ettik|edildi)", r"\bhalloldu\b", r"\bhallettim\b",
               r"\b(completed|finished|done|ready|fixed|repaired)\b"]),
    ("in_repair", [r"onarıma (aldım|aldık|başladım|başladık|alındı)", r"tamire (başladım|aldım)", r"onarımdayım",
                   r"\bbaşladım\b", r"started (the )?(work|repair)", r"\bin repair\b"]),
    ("diagnosing", [r"arıza tespit", r"incelemeye aldım", r"inceliyorum", r"\bdiagnos"]),
]
NOTE = re.compile(r"^\s*(not ekle|not al|not düş|notu|not|add (a )?note|note)\s*[:,-]?\s+(?P<text>.+)$")
QUESTION = re.compile(r"\?|\b(m[ıiuü]|m[ıiuü]s[ıi]n|m[ıiuü]yd[ıi]|ne zaman|kaç|hangi|nerede|kim|nasıl|neden|niye|"
                      r"what|when|which|how|who|why|is it|are there)\b")
NEGATION = re.compile(r"\b(değil|olmadı|bitmedi|bitmemiş|hazır değil|yapamadım|yapılmadı|not|isn't|wasn't)\b")
# SRV-2026-00240, "srv 2026 240", "SRV2026-240"
FULL_REF = re.compile(r"\b(?P<prefix>[a-zçğıöşü]{2,6})[\s\-]*(?P<year>20\d{2})[\s\-]+(?P<seq>\d{1,5})\b")
SEQ_REF = re.compile(r"\b(?P<seq>\d{1,5})\s*(numaral[ıi]|nolu|no'?lu|no\b|numara)|"
                     r"\b(kay[ıi]t|iş|işi|servis|fiş|fişi|order|job)\s*(no|numara(s[ıi])?)?\s*(?P<seq2>\d{1,5})\b")
THIS_REF = re.compile(r"\b(bu işi?|bunu|bu cihaz[ıi]?|bu kayd[ıi]|this (job|one|order)|it)\b")
FILLER = re.compile(r"^\s*((ve|ayrıca|tamam|şimdi|az önce|ben|biz|evet|and|also)\b[\s,]*)+", re.I)


@dataclass
class Command:
    intent: str  # a ServiceOrder status, "approved" or "note"
    ref: tuple = None  # ("number", "SRV-2026-00240") | ("seq", 240) | ("this",) | None
    extra: str = ""  # free text to keep (work done / note)
    raw: str = ""
    matched: list = field(default_factory=list)


def normalize(text):
    """Turkish-aware lower-casing that keeps string length (so positions map back to the original)."""
    out = []
    for ch in text:
        if ch == "İ":
            out.append("i")
        elif ch == "I":
            out.append("ı")
        else:
            low = ch.lower()
            out.append(low if len(low) == 1 else ch)
        # punctuation other than hyphen and question mark becomes a space (same length)
        if not (out[-1].isalnum() or out[-1] in "-?' "):
            out[-1] = " "
    return "".join(out)


def _find_ref(norm):
    m = FULL_REF.search(norm)
    if m:
        return ("number", f"{m.group('prefix').upper()}-{m.group('year')}-{int(m.group('seq')):05d}"), m.span()
    m = SEQ_REF.search(norm)
    if m:
        seq = m.group("seq") or m.group("seq2")
        return ("seq", int(seq)), m.span()
    m = THIS_REF.search(norm)
    if m:
        return ("this",), m.span()
    return None, None


def _extra_text(raw, spans):
    """Text the user said besides the command itself, e.g. what was repaired."""
    clauses = re.split(r"[,.;\n]+", raw)
    pieces = []
    pos = 0
    for clause in clauses:
        start = raw.find(clause, pos)
        end = start + len(clause)
        pos = end
        hit = [s for s in spans if s[0] < end and s[1] > start]
        if not hit:
            pieces.append(clause.strip())
            continue
        # command clause: keep what follows the last matched phrase inside it (voice has no punctuation)
        tail_from = max(s[1] for s in hit)
        tail = raw[tail_from:end].strip()
        if len(tail.split()) >= 2:
            pieces.append(tail)
    text = " ".join(p for p in pieces if p)
    text = FILLER.sub("", text).strip(" ,.-")
    if len(text.split()) < 2:
        return ""
    return text[0].upper() + text[1:]


def parse(text):
    raw = (text or "").strip()
    if not raw or len(raw) > 400:
        return None
    norm = normalize(raw)
    m = NOTE.match(norm)
    if m:
        ref, _span = _find_ref(norm[: m.start("text")])
        note = raw[m.start("text"):].strip()
        return Command("note", ref=ref, extra=note, raw=raw) if note else None
    if QUESTION.search(norm) or NEGATION.search(norm):
        return None
    for intent, patterns in INTENTS:
        spans = []
        for p in patterns:
            spans += [mm.span() for mm in re.finditer(p, norm)]
        if spans:
            ref, ref_span = _find_ref(norm)
            all_spans = spans + ([ref_span] if ref_span else [])
            return Command(intent, ref=ref, extra=_extra_text(raw, all_spans), raw=raw, matched=spans)
    return None
