"""Small pure helpers: time, number formatting, hashing, text normalisation."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


def parse_dt(value) -> datetime | None:
    """Parse ISO-8601 (with or without Z / offset) or YYYY-MM-DD. Returns aware UTC or None."""
    if not value or not isinstance(value, str):
        return None
    s = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def days_between(a: datetime, b: datetime) -> int:
    """Whole days from a to b, using IST calendar dates (b - a)."""
    return (b.astimezone(IST).date() - a.astimezone(IST).date()).days


def human_date(dt: datetime) -> str:
    """'8 Nov' style, IST."""
    d = dt.astimezone(IST)
    return f"{d.day} {d.strftime('%b')}"


def human_time(dt: datetime) -> str:
    d = dt.astimezone(IST)
    h = d.hour % 12 or 12
    ampm = "am" if d.hour < 12 else "pm"
    return f"{h}:{d.minute:02d}{ampm}" if d.minute else f"{h}{ampm}"


def weekday_name(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%A")


def fmt_int(n) -> str:
    """Indian digit grouping: 1234567 -> 12,34,567."""
    try:
        n = int(round(float(n)))
    except (TypeError, ValueError):
        return str(n)
    neg = n < 0
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if neg else "") + s


def rupees(n) -> str:
    return "₹" + fmt_int(n)


def pct(frac, signed: bool = False, digits: int = 0) -> str:
    """0.021 -> '2.1%' (digits=1); 0.18 -> '18%'; signed adds + for positives."""
    try:
        v = float(frac) * 100
    except (TypeError, ValueError):
        return str(frac)
    txt = f"{abs(v):.{digits}f}".rstrip("0").rstrip(".") if digits else f"{abs(round(v)):.0f}"
    sign = "-" if v < 0 else ("+" if signed and v > 0 else "")
    return f"{sign}{txt}%"


def stable_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def short_hash(obj, n: int = 6) -> str:
    return stable_hash(obj)[:n]


NUM_RE = re.compile(r"(?<![A-Za-z])[-+−]?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    """Canonical numeric tokens in text: '2,410' -> '2410', '2.10' -> '2.1', '-60' -> '60'."""
    out = set()
    for m in NUM_RE.findall(text or ""):
        t = m.replace(",", "").replace("−", "-").lstrip("+-")
        if not t:
            continue
        if "." in t:
            t = t.rstrip("0").rstrip(".")
        t = t.lstrip("0") or "0"
        out.add(t)
    return out


def all_numbers(obj) -> set[str]:
    """Every number that appears anywhere in a JSON-like object (keys excluded)."""
    acc: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
        elif isinstance(o, bool) or o is None:
            return
        elif isinstance(o, (int, float)):
            acc.update(numbers_in(str(o)))
            # fractions are often shown as percentages
            if isinstance(o, float) and -1.5 < o < 1.5:
                acc.update(numbers_in(f"{abs(o) * 100:.1f}"))
                acc.update(numbers_in(f"{abs(o) * 100:.0f}"))
        else:
            acc.update(numbers_in(str(o)))

    walk(obj)
    return acc


def norm_text(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^\w\sऀ-ॿ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_ABBR = ("dr", "mr", "mrs", "ms", "no", "st", "vs", "approx", "e.g", "i.e", "etc")


def first_sentence(s: str) -> str:
    """First sentence, without splitting on abbreviations like 'Dr.' or decimals like '1.5'."""
    s = (s or "").strip()
    for m in re.finditer(r"[.!?](\s|$)", s):
        end = m.start()
        prev = re.search(r"([A-Za-z.]+)$", s[:end])
        if prev and (prev.group(1).lower().rstrip(".") in _ABBR or len(prev.group(1).rstrip(".")) == 1):
            continue
        return s[: end + 1].strip()
    return s


def sentences(s: str) -> list[str]:
    out, rest = [], (s or "").strip()
    while rest:
        fs = first_sentence(rest)
        if not fs:
            break
        out.append(fs)
        rest = rest[len(fs):].strip()
        if fs == rest:
            break
    return out


def possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def humanize_slug(s: str) -> str:
    return (s or "").replace("_", " ").strip()
