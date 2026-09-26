"""Fact layer: turns raw contexts into verified, pre-formatted facts + voice/language decisions.

Every number a message can contain is computed here (never by an LLM).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .util import (all_numbers, days_between, first_sentence, fmt_int, human_date,
                   humanize_slug, parse_dt, pct, rupees)

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

# Plain-language labels (never expose raw scope slugs to the merchant)
PEER_LABEL = {
    "dentists": "similar clinics in metros",
    "salons": "similar salons in metros",
    "restaurants": "similar casual-dining places",
    "gyms": "neighbourhood gyms",
    "pharmacies": "neighbourhood pharmacies",
}
PEOPLE = {"dentists": "patients", "salons": "clients", "restaurants": "customers",
          "gyms": "members", "pharmacies": "customers"}
BUSINESS_NOUN = {"dentists": "clinic", "salons": "salon", "restaurants": "restaurant",
                 "gyms": "studio", "pharmacies": "pharmacy"}
EMOJI_CUSTOMER = {"dentists": "🦷", "salons": "✨", "restaurants": "🍽️", "gyms": "💪", "pharmacies": ""}
EMOJI_MERCHANT = {"dentists": "", "salons": "✨", "restaurants": "", "gyms": "", "pharmacies": ""}

GLOBAL_TABOO = ["guaranteed", "guarantee", "miracle", "best in city", "100% safe", "100%",
                "only today", "last chance", "limited spots", "limited slots", "hurry",
                "act now", "don't miss out", "amazing deal", "cure"]
JARGON = ["trigger", "payload", "signal", "suppression", "placeholder", "context_id",
          "peer_median", "ctr_below", "merchant_id", "customer_id", "snake_case", "json",
          "urgency", "digest item", "category_slug"]


def month_in_range(month_idx: int, rng: str) -> bool:
    """month_idx 0-11; rng like 'Oct-Dec', 'Jan', 'Feb 14', 'Mar-Apr', 'Nov-Feb'."""
    toks = [t for t in re.findall(r"[a-z]{3}", (rng or "").lower()) if t in MONTHS]
    if not toks:
        return False
    a = MONTHS.index(toks[0])
    b = MONTHS.index(toks[-1])
    if a <= b:
        return a <= month_idx <= b
    return month_idx >= a or month_idx <= b  # wraps year end


def parse_price(title: str) -> int | None:
    m = re.search(r"₹\s?([\d,]+)", title or "")
    return int(m.group(1).replace(",", "")) if m else None


@dataclass
class Facts:
    category: dict
    merchant: dict
    trigger: dict
    customer: dict | None
    now: datetime
    derived: set[str] = field(default_factory=set)  # extra allowed numbers computed in code

    # ---------------- identity & voice ----------------
    @property
    def slug(self) -> str:
        return self.merchant.get("category_slug") or self.category.get("slug") or ""

    @property
    def ident(self) -> dict:
        return self.merchant.get("identity") or {}

    @property
    def biz(self) -> str:
        return self.ident.get("name") or "your business"

    @property
    def possessive_biz(self) -> str:
        b = self.biz
        return f"{b}'" if b.endswith("s") else f"{b}'s"

    @property
    def owner(self) -> str:
        return (self.ident.get("owner_first_name") or "").strip()

    @property
    def salutation(self) -> str:
        o = self.owner
        if not o:
            return f"{self.biz} team"
        if self.slug == "dentists" and not o.lower().startswith("dr"):
            return f"Dr. {o}"
        return o

    @property
    def owner_short(self) -> str:
        """Owner name as used to sign customer messages ('Dr. Meera', 'Lakshmi')."""
        return self.salutation if self.owner else self.biz

    @property
    def locality(self) -> str:
        return self.ident.get("locality") or self.ident.get("city") or ""

    @property
    def city(self) -> str:
        return self.ident.get("city") or ""

    @property
    def people(self) -> str:
        return PEOPLE.get(self.slug, "customers")

    @property
    def voice(self) -> dict:
        return self.category.get("voice") or {}

    @property
    def taboos(self) -> list[str]:
        v = self.voice
        return [t.split(" (")[0].lower() for t in (v.get("vocab_taboo") or v.get("taboos") or [])]

    def merchant_lang(self) -> str:
        """'hinglish' (light code-mix) or 'en'."""
        langs = [str(x).lower() for x in (self.ident.get("languages") or [])]
        code_mix = str(self.voice.get("code_mix", ""))
        if "hi" in langs and "english_primary" not in code_mix:
            return "hinglish"
        return "en"

    def customer_lang(self) -> str:
        if not self.customer:
            return "en"
        pref = str((self.customer.get("identity") or {}).get("language_pref", "")).lower()
        if pref in ("hi", "hindi"):
            return "hindi_roman"
        if pref.startswith("hi"):
            return "hinglish"
        return "en"

    # ---------------- customer ----------------
    @property
    def cust_ident(self) -> dict:
        return (self.customer or {}).get("identity") or {}

    def customer_names(self) -> tuple[str, str | None]:
        """(addressee, subject). 'Aanya (parent: Sneha)' -> ('Sneha', 'Aanya')."""
        raw = (self.cust_ident.get("name") or "").strip()
        m = re.match(r"^(.*?)\s*\(parent:\s*([^)]+)\)", raw)
        if m:
            return m.group(2).strip(), m.group(1).strip()
        if raw.startswith("(") or not raw:
            return "", None
        return raw, None

    def customer_via_relative(self) -> bool:
        ch = str(((self.customer or {}).get("preferences") or {}).get("channel", ""))
        return "via_son" in ch or "via_daughter" in ch

    def customer_last_visit(self) -> datetime | None:
        return parse_dt(((self.customer or {}).get("relationship") or {}).get("last_visit"))

    # ---------------- performance ----------------
    @property
    def perf(self) -> dict:
        return self.merchant.get("performance") or {}

    @property
    def peer(self) -> dict:
        return self.category.get("peer_stats") or {}

    def peer_label(self) -> str:
        return PEER_LABEL.get(self.slug, "similar businesses")

    def delta(self, metric: str) -> float | None:
        d = (self.perf.get("delta_7d") or {})
        v = d.get(f"{metric}_pct")
        return float(v) if isinstance(v, (int, float)) else None

    def ctr_gap(self) -> tuple[str, str, float] | None:
        c, p = self.perf.get("ctr"), self.peer.get("avg_ctr")
        if isinstance(c, (int, float)) and isinstance(p, (int, float)) and p:
            return pct(c, digits=1), pct(p, digits=1), (c - p)
        return None

    # ---------------- offers ----------------
    def active_offers(self) -> list[str]:
        return [o.get("title") for o in (self.merchant.get("offers") or [])
                if o.get("status") == "active" and o.get("title")]

    def catalog(self) -> list[dict]:
        return [o for o in (self.category.get("offer_catalog") or []) if o.get("title")]

    def best_offer(self, prefer: tuple[str, ...] = ()) -> tuple[str | None, bool]:
        """(title, is_merchants_own). Prefers the merchant's active offers; else a catalog
        service@price suggestion (clearly framed as a suggestion by the caller)."""
        act = self.active_offers()
        for kw in prefer:
            for t in act:
                if kw.lower() in t.lower():
                    return t, True
        if act:
            return act[0], True
        cat = self.catalog()
        for kw in prefer:
            for o in cat:
                if kw.lower() in o["title"].lower():
                    return o["title"], False
        for o in cat:
            if o.get("type") == "service_at_price":
                return o["title"], False
        return (cat[0]["title"], False) if cat else (None, False)

    # ---------------- category knowledge ----------------
    def digest_item(self, item_id: str | None = None, kinds: tuple[str, ...] = (),
                    seen: dict | None = None) -> dict | None:
        items = [d for d in (self.category.get("digest") or []) if isinstance(d, dict)]
        if item_id:
            for d in items:
                if d.get("id") == item_id:
                    return d
        pool = [d for d in items if not kinds or d.get("kind") in kinds] or items
        if not pool:
            return None
        if seen:  # freshness first: items first seen in the newest category version
            slug = self.category.get("slug") or self.slug
            pool = sorted(pool, key=lambda d: -(seen.get(f"{slug}:{d.get('id')}", 0)))
        return pool[0]

    def seasonal_beat(self) -> dict | None:
        m = self.now.month - 1
        for b in self.category.get("seasonal_beats") or []:
            if month_in_range(m, b.get("month_range", "")):
                return b
        return None

    def top_trend(self, keywords: tuple[str, ...] = ()) -> dict | None:
        ts = [t for t in (self.category.get("trend_signals") or []) if isinstance(t, dict)]
        for kw in keywords:
            for t in ts:
                if kw in str(t.get("query", "")).lower():
                    return t
        return max(ts, key=lambda t: t.get("delta_yoy", 0), default=None)

    def upcoming_festive_beat(self) -> dict | None:
        """Nearest seasonal beat (current or upcoming) that mentions festivals/weddings."""
        best, best_gap = None, 99
        for b in self.category.get("seasonal_beats") or []:
            note = str(b.get("note", "")).lower()
            if not any(k in note for k in ("festival", "diwali", "wedding", "festive", "christmas", "new year")):
                continue
            toks = [t for t in re.findall(r"[a-z]{3}", str(b.get("month_range", "")).lower()) if t in MONTHS]
            if not toks:
                continue
            gap = (MONTHS.index(toks[0]) - (self.now.month - 1)) % 12
            if month_in_range(self.now.month - 1, b.get("month_range", "")):
                gap = 0
            if gap < best_gap:
                best, best_gap = b, gap
        return best


    # ---------------- time ----------------
    def days_until(self, value) -> int | None:
        dt = parse_dt(value) if isinstance(value, str) else value
        if not dt:
            return None
        d = days_between(self.now, dt)
        self.derived.add(str(abs(d)))
        return d

    def days_since(self, value) -> int | None:
        dt = parse_dt(value) if isinstance(value, str) else value
        if not dt:
            return None
        d = days_between(dt, self.now)
        self.derived.add(str(abs(d)))
        return d

    def date_label(self, value) -> str:
        dt = parse_dt(value) if isinstance(value, str) else value
        return human_date(dt) if dt else str(value)

    # ---------------- evidence for the validator ----------------
    def allowed_numbers(self) -> set[str]:
        nums = set()
        for obj in (self.category, self.merchant, self.trigger, self.customer or {}):
            nums |= all_numbers(obj)
        nums |= self.derived
        # effort / option whitelist
        nums |= {"1", "2", "3", "5", "10", "24", "30", "48"}
        # current year and date parts are always fair
        nums |= {str(self.now.year), str(self.now.day)}
        return nums


def register(f: Facts, *values) -> None:
    """Mark computed numbers as allowed (e.g. a difference or a count we derived)."""
    for v in values:
        if v is None:
            continue
        for tok in re.findall(r"\d[\d,]*(?:\.\d+)?", str(v)):
            t = tok.replace(",", "")
            if "." in t:
                t = t.rstrip("0").rstrip(".")
            f.derived.add(t.lstrip("0") or "0")


def clean_query(q: str) -> str:
    """'balayage near me' -> 'balayage'; 'teeth whitening price' -> 'teeth whitening'."""
    q = re.sub(r"\b(near me|price|cost|offer|delhi|mumbai|bangalore|chennai|pune|hyderabad|jaipur|lucknow)\b",
               "", q or "", flags=re.I)
    return re.sub(r"\s+", " ", q).strip()


# Offer keywords that suit a category's customer-facing comeback messages
COMEBACK_PREFS = {
    "dentists": ("cleaning", "check", "consultation"),
    "salons": ("haircut", "spa", "threading"),
    "gyms": ("trial", "demo", "body composition"),
    "pharmacies": ("bp", "delivery", "consultation"),
    "restaurants": ("thali", "brunch", "delivery"),
}
FESTIVE_PREFS = {
    "dentists": ("whitening", "smile", "cleaning"),
    "salons": ("bridal", "spa", "facial", "mani"),
    "gyms": ("couple", "family", "month", "trial"),
    "pharmacies": ("health card", "bp", "delivery"),
    "restaurants": ("family", "brunch", "party", "birthday", "starter"),
}
PLURAL_BIZ = {"dentists": "clinics", "salons": "salons", "restaurants": "restaurants",
              "gyms": "gyms", "pharmacies": "pharmacies"}


__all__ = ["Facts", "register", "fmt_int", "rupees", "pct", "first_sentence", "humanize_slug",
           "human_date", "parse_price", "GLOBAL_TABOO", "JARGON", "PEOPLE", "BUSINESS_NOUN",
           "EMOJI_CUSTOMER", "EMOJI_MERCHANT", "month_in_range"]
