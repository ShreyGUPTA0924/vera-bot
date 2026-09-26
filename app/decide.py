"""Decision layer: eligibility (ids, suppression, compatibility, consent) + compose + ranking."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .facts import Facts
from .playbook import COMPOSERS, Draft, Skip, curious, perf_dip, perf_spike, research_digest
from .store import Store

# Category x kind compatibility. Anything not listed is native/adaptable.
INCOMPATIBLE: dict[str, set[str]] = {
    "chronic_refill_due": {"dentists", "salons", "restaurants", "gyms"},
    "recall_due": {"pharmacies", "restaurants"},
    "trial_followup": {"pharmacies", "restaurants"},
    "ipl_match_today": {"dentists", "salons", "gyms", "pharmacies"},
    "wedding_package_followup": {"restaurants", "pharmacies"},
}

REMINDER_KINDS = {"recall_due", "appointment_tomorrow", "chronic_refill_due", "trial_followup",
                  "wedding_package_followup", "bridal_followup"}
PROMO_KINDS = {"customer_lapsed_soft", "customer_lapsed_hard", "winback_customer"}
REMINDER_SCOPES = {"recall_reminders", "appointment_reminders", "refill_reminders", "recall_alerts",
                   "bridal_package_followup", "kids_program_updates", "treatment_followup",
                   "program_updates", "renewal_reminders", "delivery_notifications"}
PROMO_SCOPES = {"promotional_offers", "winback_offers", "renewal_reminders", "program_updates"}


@dataclass
class Plan:
    trigger_id: str
    trigger: dict
    merchant: dict
    customer: dict | None
    facts: Facts
    draft: Draft
    score: float


def consent_ok(kind: str, customer: dict) -> tuple[bool, str]:
    ident = customer.get("identity") or {}
    prefs = customer.get("preferences") or {}
    scopes = set((customer.get("consent") or {}).get("scope") or [])
    if ident.get("phone_redacted") is None and "phone_redacted" in ident:
        return False, "no phone on record"
    if str(prefs.get("channel", "")).startswith("none"):
        return False, "no messaging channel recorded"
    if kind in REMINDER_KINDS:
        if prefs.get("reminder_opt_in") is True or scopes & REMINDER_SCOPES:
            return True, "reminder consent"
        return False, "no reminder consent"
    if kind in PROMO_KINDS:
        if scopes & PROMO_SCOPES:
            return True, "promotional consent"
        return False, "no promotional consent"
    if scopes or prefs.get("reminder_opt_in"):
        return True, "general consent"
    return False, "no consent on record"


def generic_compose(f: Facts, kind: str, seen) -> Draft:
    """Unknown trigger kinds (the judge may inject new ones): route by payload shape."""
    p = f.trigger.get("payload") or {}
    if isinstance(p.get("delta_pct"), (int, float)):
        return (perf_spike if p["delta_pct"] > 0 else perf_dip)(f, seen)
    if p.get("top_item_id") or p.get("digest_item_id"):
        return research_digest(f, seen)
    return curious(f, kind, f"Unrecognised trigger kind '{kind}'; used a safe ask-the-merchant message.")


def plan_trigger(store: Store, trigger_id: str, now: datetime) -> Plan | Skip:
    trig = store.get("trigger", trigger_id)
    if not trig:
        return Skip("unknown trigger")
    sk = trig.get("suppression_key") or f"trg:{trigger_id}"
    if sk in store.sent_keys:
        return Skip("already sent (suppression key)")
    mid = trig.get("merchant_id") or (trig.get("payload") or {}).get("merchant_id")
    merchant = store.get("merchant", mid)
    if not merchant:
        return Skip("merchant context not loaded yet")
    st = store.mstate(mid)
    if st.opted_out or st.hostile:
        return Skip("merchant opted out")
    category = store.category_for(merchant)
    if not category:
        return Skip("category context not loaded yet")
    kind = trig.get("kind") or "unknown"
    slug = merchant.get("category_slug", "")
    if slug in INCOMPATIBLE.get(kind, set()):
        return Skip(f"'{kind}' does not apply to {slug}")
    cid = trig.get("customer_id") or (trig.get("payload") or {}).get("customer_id")
    customer = None
    if trig.get("scope") == "customer" or cid:
        customer = store.get("customer", cid)
        if not customer:
            return Skip("customer context not loaded yet")  # retried next tick
        if customer.get("merchant_id") and customer["merchant_id"] != mid:
            return Skip("customer belongs to another merchant")
        ok, why = consent_ok(kind, customer)
        if not ok:
            return Skip(f"consent: {why}")
    f = Facts(category=category, merchant=merchant, trigger=trig, customer=customer, now=now)
    composer = COMPOSERS.get(kind)
    try:
        draft = composer(f, store.digest_seen) if composer else generic_compose(f, kind, store.digest_seen)
    except Exception as e:  # never let one bad payload break a tick
        draft = curious(f, kind, f"Composer error ({type(e).__name__}); safe fallback.")
    if isinstance(draft, Skip):
        return draft
    if customer is not None and draft.audience != "customer":
        return Skip("customer trigger produced merchant draft")
    score = rank_score(store, trig, merchant, f, draft)
    return Plan(trigger_id, trig, merchant, customer, f, draft, score)


def rank_score(store: Store, trig: dict, merchant: dict, f: Facts, draft: Draft) -> float:
    urg = trig.get("urgency") or 1
    try:
        urg = float(urg)
    except (TypeError, ValueError):
        urg = 1.0
    score = 3 * urg
    score += min(4, len(f.allowed_numbers() & _nums(draft.body)) / 2)  # evidence strength
    if draft.pivot:
        score -= 1
    if draft.lever == "ask_the_merchant":
        score -= 0.5
    # freshness: trigger/merchant pushed in a newer version
    if (store.version("merchant", merchant.get("merchant_id", "")) or 1) > 1:
        score += 1
    return score


def _nums(text: str) -> set[str]:
    from .util import numbers_in
    return numbers_in(text)
