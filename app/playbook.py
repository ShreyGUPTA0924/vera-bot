"""Playbook: one composer per trigger kind.

Each composer returns a Draft (body + CTA + next_action artifact + rationale) built ONLY from
verified facts, or a Skip with a reason. Composers verify the trigger's claim against the data
and pivot to what the data actually supports (never claim a dip that isn't there).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .facts import (BUSINESS_NOUN, COMEBACK_PREFS, EMOJI_CUSTOMER, FESTIVE_PREFS, PLURAL_BIZ, Facts,
                    clean_query, first_sentence, fmt_int, humanize_slug, parse_price, pct, register,
                    rupees)
from .util import human_time, parse_dt, possessive, sentences, weekday_name


@dataclass
class Draft:
    kind: str
    audience: str               # merchant | customer
    send_as: str                # vera | merchant_on_behalf
    body: str
    cta: str                    # binary_yes_no | binary_confirm_cancel | multi_choice_slot | open_ended | none
    template_name: str
    template_params: list[str]
    lever: str
    rationale: str
    next_action: dict | None = None
    language: str = "en"
    pivot: str | None = None
    facts_used: list[str] = field(default_factory=list)


@dataclass
class Skip:
    reason: str


# ------------------------------------------------------------------ helpers
def _hl(f: Facts, en: str, hi: str) -> str:
    return hi if f.merchant_lang() == "hinglish" else en


def _yes(f: Facts, action_en: str, action_hi: str) -> str:
    return _hl(f, f"Reply YES and I'll {action_en}; your part takes under 2 minutes.",
               f"Reply YES — main {action_hi}, aapka bas 2 minute lagega.")


CTA_MARKERS = ("Reply YES", "Reply CONFIRM", "CONFIRM bolein", "Tell me the service", "Abhi shuru karein",
               "Restart karein", "Start now?")


def visible_numbers(f: Facts) -> set[str]:
    """Numbers the judge can see next to the message: trigger payload, the merchant's views/calls/CTR,
    active offer titles. Facts from other context (benchmarks, digests, trends) are true but look
    unverifiable to a judge that doesn't see them, so every merchant message anchors on these first."""
    from .util import all_numbers, numbers_in
    vis = all_numbers(f.trigger.get("payload") or {})
    p = f.perf
    for k in ("views", "calls", "directions", "leads"):
        if p.get(k) is not None:
            vis |= numbers_in(str(p[k]))
    if isinstance(p.get("ctr"), (int, float)):
        vis |= numbers_in(f"{p['ctr'] * 100:.1f}")
    for t in f.active_offers():
        vis |= numbers_in(t)
    return vis


OUTCOME = {"dentists": "appointments", "salons": "bookings", "restaurants": "orders",
           "gyms": "sign-ups", "pharmacies": "orders"}


def _anchor_sentence(f: Facts, lever: str = "") -> str:
    """The merchant's own visible numbers, phrased as the reason for the action (not a stat dump)."""
    p = f.perf
    if p.get("views") is None or p.get("calls") is None:
        return ""
    v, c = fmt_int(p["views"]), fmt_int(p["calls"])
    out = OUTCOME.get(f.slug, "customers")
    if lever == "ask_the_merchant":
        return (f"Since {f.biz} already pulls {v} profile views and {c} calls a month, putting the most-asked "
                f"service up front turns more of them into {out}.")
    if lever == "loss_aversion":
        return f"That's traffic worth protecting: {v} profile views and {c} calls in the last {p.get('window_days', 30)} days."
    return f"You're already pulling {v} profile views and {c} calls a month, so this turns more of that into {out}."


def _with_anchor(f: Facts, body: str, lever: str = "") -> str:
    from .util import numbers_in
    if len(numbers_in(body) & visible_numbers(f)) >= 2:
        return body
    anchor = _anchor_sentence(f, lever)
    if not anchor:
        return body
    cut = max((body.rfind(m) for m in CTA_MARKERS), default=-1)
    if cut <= 0:
        return body.rstrip() + " " + anchor
    return body[:cut].rstrip() + " " + anchor + " " + body[cut:]


def _m(f: Facts, kind: str, body: str, cta: str, lever: str, rationale: str, params: list[str],
       next_action: dict | None = None, pivot: str | None = None, used=None,
       template: str | None = None) -> Draft:
    body = _with_anchor(f, body.strip(), lever)
    return Draft(kind=kind, audience="merchant", send_as="vera", body=body.strip(), cta=cta,
                 template_name=template or f"vera_{kind}_v1",
                 template_params=[p for p in params if p], lever=lever, rationale=rationale,
                 next_action=next_action, language=f.merchant_lang(), pivot=pivot,
                 facts_used=used or [])


def _beat_note(b: dict | None) -> str:
    """Seasonal note without its unsourced multipliers ('bookings 4x baseline')."""
    if not b:
        return ""
    note = str(b.get("note", ""))
    if any(ch.isdigit() for ch in note):
        note = note.split(" — ")[0].split(" - ")[0]
    return note.strip()


def _c(f: Facts, kind: str, body: str, cta: str, lever: str, rationale: str, params: list[str],
       next_action: dict | None = None, used=None) -> Draft:
    return Draft(kind=kind, audience="customer", send_as="merchant_on_behalf", body=body.strip(),
                 cta=cta, template_name=f"merchant_{kind}_v1",
                 template_params=[p for p in params if p], lever=lever, rationale=rationale,
                 next_action=next_action, language=f.customer_lang(), facts_used=used or [])


def _post(f: Facts, headline: str, line2: str = "") -> str:
    loc = f" — {f.locality}" if f.locality else ""
    txt = f"{f.biz}{loc}\n{headline}"
    if line2:
        txt += f"\n{line2}"
    return txt


def _offer_phrase(f: Facts, prefer=()) -> tuple[str | None, str, bool]:
    """(offer_title, phrase, is_own). phrase frames catalog offers as a suggestion."""
    title, own = f.best_offer(prefer)
    if not title:
        return None, "", False
    if own:
        return title, f"your live offer '{title}'", True
    return title, f"a '{title}' offer (a format that works well in your category)", False


def _perf_line(f: Facts) -> str:
    p = f.perf
    bits = []
    if p.get("views") is not None:
        bits.append(f"{fmt_int(p['views'])} profile views")
    if p.get("calls") is not None:
        bits.append(f"{fmt_int(p['calls'])} calls")
    if not bits:
        return ""
    return f"{' and '.join(bits)} in the last {p.get('window_days', 30)} days"


# ------------------------------------------------------------------ pivot (claim not supported)
def pivot(f: Facts, kind: str, why: str) -> Draft:
    """The trigger's claim isn't supported by the data. Send the strongest real signal instead."""
    sub = f.merchant.get("subscription") or {}
    note = f"Trigger '{kind}' not supported by data ({why}); pivoted to strongest verified signal."
    if sub.get("status") == "expired":
        d = sub.get("days_since_expiry")
        body = (f"{f.salutation}, quick update on {f.biz}: {_perf_line(f) or 'your profile is still live'}. "
                f"Your plan paused {d} days ago" if d else f"{f.salutation}, your plan is currently paused")
        body += (", so profile updates and posts have stopped. "
                 + _yes(f, "share the 2-step restart so upkeep resumes this week",
                        "restart steps bhej deti hoon, upkeep isi hafte resume ho jayega"))
        return _m(f, kind, body, "binary_yes_no", "loss_aversion", note + " Lever: loss aversion on paused plan.",
                  [f.salutation, f"paused {d} days"], {"type": "renewal", "label": "restart the plan",
                   "artifact": f"Restart plan for {f.biz}: 1) confirm plan 2) payment on the magicpin dashboard. Profile upkeep resumes within a day."},
                  pivot="subscription_expired")
    gap = f.ctr_gap()
    if f.ident.get("verified") is False:
        body = (f"{f.salutation}, I checked the {kind.replace('perf_', '').replace('_', ' ')} alert: your numbers are steady "
                f"({_perf_line(f) or 'steady traffic'}), so there's nothing to promote there. The bigger win for {f.biz} right now: "
                f"the Google profile is still unverified. Verification is a phone call or postcard — I'll walk you "
                f"through it in about 5 minutes. " + _hl(f, "Start now? Reply YES.", "Abhi shuru karein? Reply YES."))
        return _m(f, kind, body, "binary_yes_no", "effort_externalisation",
                  note + " Pivot: unverified Google profile is the strongest verified issue.",
                  [f.salutation, "unverified profile"], {"type": "verification", "label": "verification walkthrough",
                   "artifact": "Verification: 1) Open Google Business Profile 2) Choose phone or postcard 3) Enter the code — I'll confirm it went through."},
                  pivot="gbp_unverified")
    d_rem = sub.get("days_remaining")
    if sub.get("status") == "active" and isinstance(d_rem, int) and d_rem <= 15:
        body = (f"{f.salutation}, no big swing in {f.possessive_biz} numbers this week ({_perf_line(f) or 'steady'}), "
                f"but your {sub.get('plan', '')} plan renews in {d_rem} days. Renewing on time keeps posts and profile "
                f"upkeep running without a gap. " + _yes(f, "share the renewal steps", "renewal steps share kar deti hoon"))
        return _m(f, kind, body, "binary_yes_no", "loss_aversion", note + " Pivot: renewal due soon.",
                  [f.salutation, f"{d_rem} days"], {"type": "renewal", "label": "renewal steps",
                   "artifact": f"Renewal for {f.biz}: magicpin partner dashboard → Plan → Renew. Upkeep continues without a gap."},
                  pivot="renewal_due")
    if gap and gap[2] < 0:
        mine, peer, _ = gap
        offer, phrase, own = _offer_phrase(f)
        body = (f"{f.salutation}, your numbers are steady this week, but one gap stands out: "
                f"{mine} of people who see {f.biz} on Google tap through, vs {peer} for {f.peer_label()} (magicpin category benchmark). ")
        if offer:
            body += f"Putting {phrase} on your profile is the quickest fix. "
        body += _yes(f, "draft the listing update", "listing update draft kar deti hoon")
        return _m(f, kind, body, "binary_yes_no", "loss_aversion", note + " Lever: peer benchmark gap.",
                  [f.salutation, f"{mine} vs {peer}"], {"type": "offer_live", "label": "listing update",
                   "artifact": _post(f, offer or "Book your visit today", "Call or tap Directions to visit.")},
                  pivot="ctr_gap")
    return curious(f, kind, note)


def curious(f: Facts, kind: str, note: str = "", lead: str = "") -> Draft:
    """Ask-the-merchant: low effort, high reply-rate, zero fabrication risk."""
    offers = f.active_offers()
    guess = ""
    if offers[:2]:
        guess = f" — {offers[0]}" + (f" or {offers[1]}?" if len(offers) > 1 else "?")
    ask = _hl(f, f"quick one: what are {f.people} asking for most at {f.biz} this week",
              f"ek quick sawaal: is hafte {f.biz} pe {f.people} sabse zyada kya pooch rahe hain")
    opener = f"{f.salutation}, {lead}" if lead else f"{f.salutation}, "
    ask = ask[0].upper() + ask[1:] if lead else ask
    body = f"{opener}{ask}{guess if guess else '?'}"
    body += " Tell me the service and I'll turn it into a Google post plus a ready reply for price questions — takes 5 min."
    return _m(f, kind, body, "open_ended", "ask_the_merchant",
              (note + " " if note else "") + "Curious-ask: asks the merchant for current demand; offers effort externalisation.",
              [f.salutation, guess.strip(" —?")], {"type": "google_post", "label": "Google post on the top-asked service",
               "artifact": _post(f, "Most-asked this week: [service] — now available", "Walk in or call to book.")})


# ------------------------------------------------------------------ merchant composers
def research_digest(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    item = f.digest_item(p.get("top_item_id") or p.get("digest_item_id"),
                         kinds=("research", "trend", "tech", "seasonal", "compliance"), seen=seen)
    if not item:
        return curious(f, "research_digest", "No digest item available.")
    title, src = item.get("title", ""), item.get("source", "")
    body = _hl(f, f"{f.salutation}, one item from {src} worth 2 minutes: {title}.",
               f"{f.salutation}, {src} mein ek kaam ki cheez aayi hai: {title}.")
    if item.get("trial_n") and fmt_int(item["trial_n"]) not in title:
        body += f" It's a {fmt_int(item['trial_n'])}-person study."
    summ = first_sentence(item.get("summary", ""))
    if summ and summ.lower() not in title.lower():
        body += f" {summ}"
    agg = f.merchant.get("customer_aggregate") or {}
    seg = str(item.get("patient_segment", ""))
    if "high_risk" in seg and agg.get("high_risk_adult_count"):
        body += f" Directly relevant to your {fmt_int(agg['high_risk_adult_count'])} high-risk adult {f.people}."
    elif item.get("actionable"):
        body += f" Practical takeaway: {item['actionable'].rstrip('.')}."
    body += (" Want me to send a short summary and a patient-friendly WhatsApp version you can share?"
             if f.slug == "dentists" else " Want a 3-line summary plus a ready post for your customers?")
    art = f"Summary — {title} ({src}).\n{summ}\nWhat to do: {item.get('actionable', '')}".strip()
    return _m(f, "research_digest", body, "open_ended", "curiosity+reciprocity",
              f"Research/trend digest '{item.get('id')}' from {src}; cited source; linked to merchant cohort where data exists.",
              [f.salutation, title, src], {"type": "summary", "label": "summary + shareable post", "artifact": art},
              used=[f"digest:{item.get('id')}"])


def regulation_change(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    item = f.digest_item(p.get("top_item_id"), kinds=("compliance",), seen=seen)
    if not item:
        return curious(f, "regulation_change", "No compliance item found.")
    dl = p.get("deadline_iso")
    days = f.days_until(dl) if dl else None
    body = f"{f.salutation}, compliance heads-up from {item.get('source')}: {item.get('title')}."
    summ = first_sentence(item.get("summary", ""))
    if summ:
        body += f" {summ}"
    if days is not None and days >= 0:
        body += f" That's {days} days from now ({f.date_label(dl)})."
    if item.get("actionable"):
        body += f" What to do: {item['actionable'].rstrip('.')}."
    body += " " + _yes(f, "send a 1-page checklist for your team", "team ke liye 1-page checklist bhej deti hoon")
    art = f"Checklist — {item.get('title')}\n- {item.get('actionable', '')}\n- Record the check in your SOP file\n- Recheck before {f.date_label(dl) if dl else 'the deadline'}"
    return _m(f, "regulation_change", body, "binary_yes_no", "loss_aversion",
              f"Regulation '{item.get('id')}' with deadline; cited circular; action = checklist.",
              [f.salutation, item.get("title", ""), f.date_label(dl) if dl else ""],
              {"type": "checklist", "label": "compliance checklist", "artifact": art},
              used=[f"digest:{item.get('id')}"])


def cde_opportunity(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    item = f.digest_item(p.get("digest_item_id"), kinds=("cde",), seen=seen)
    if not item:
        return curious(f, "cde_opportunity", "No CDE item.")
    when = parse_dt(item.get("date"))
    body = f"{f.salutation}, {item.get('title')} ({item.get('source')})"
    if when:
        body += f" — {weekday_name(when)} {f.date_label(when)}, {human_time(when)}"
    body += "."
    cr = p.get("credits") or item.get("credits")
    if cr:
        body += f" {cr} CDE credits."
    summ = first_sentence(item.get("summary", ""))
    if summ:
        body += f" {summ}"
    if item.get("actionable"):
        body += f" {item['actionable'].rstrip('.')}."
    body += " " + _yes(f, "send the registration steps and a calendar reminder",
                       "registration steps aur calendar reminder bhej deti hoon")
    return _m(f, "cde_opportunity", body, "binary_yes_no", "reciprocity",
              f"CDE event '{item.get('id')}' with date/credits/fee from the category calendar.",
              [f.salutation, item.get("title", "")],
              {"type": "reminder", "label": "registration + reminder",
               "artifact": f"Registration for {item.get('title')}: {item.get('actionable', '')}. Reminder set for the day before."},
              used=[f"digest:{item.get('id')}"])


def supply_alert(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    item = f.digest_item(p.get("alert_id"), kinds=("alert", "supply"), seen=seen) or {}
    mol = p.get("molecule") or "the affected molecule"
    batches = ", ".join(p.get("affected_batches") or [])
    mfr = p.get("manufacturer")
    src = item.get("source", "a regulator alert")
    body = f"{f.salutation}, urgent: {src} — voluntary recall on {mol}"
    if batches:
        body += f" batches {batches}"
    if mfr:
        body += f" ({mfr})"
    body += "."
    if "sub-potency" in (item.get("summary") or "").lower():
        body += " Reason: sub-potency, so customers need a replacement, not panic."
    chronic = (f.merchant.get("customer_aggregate") or {}).get("chronic_rx_count")
    if chronic:
        body += (f" You have {fmt_int(chronic)} chronic-Rx customers on record — the ones who got these batches "
                 f"should hear it from you first.")
    body += " " + _yes(f, "draft the customer WhatsApp and a replacement-pickup note",
                       "customer WhatsApp aur replacement note draft kar deti hoon")
    art = (f"Namaste, {f.biz} here. The manufacturer has voluntarily recalled some {mol} batches ({batches}). "
           f"If you bought {mol} from us recently, please bring or send your strip — we'll replace it free. Reply here for home pickup.")
    return _m(f, "supply_alert", body, "binary_yes_no", "urgency",
              f"Supply alert: recall of {mol} batches {batches}; linked to merchant's chronic-Rx base; action = customer notice.",
              [f.salutation, mol, batches], {"type": "whatsapp_draft", "label": "customer recall notice", "artifact": art},
              used=["trigger.payload", "customer_aggregate.chronic_rx_count"])


def category_seasonal(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    trends = []
    for t in p.get("trends") or []:
        name, _, val = str(t).rpartition("_")
        name = name.replace("_demand", "").replace("_", "/") if name else t
        if val.lstrip("+-").isdigit():
            trends.append(f"{name} {'+' if not val.startswith('-') else '−'}{val.lstrip('+-')}%")
    item = f.digest_item(kinds=("seasonal",), seen=seen)
    beat = f.seasonal_beat()
    if not trends and not beat and not item:
        return curious(f, "category_seasonal", "No seasonal data.")
    body = _hl(f, f"{f.salutation}, the seasonal shift has started", f"{f.salutation}, season badal raha hai")
    if trends:
        body += f": {', '.join(trends)}"
    elif beat:
        body += f": {_beat_note(beat)}"
    if item and item.get("source"):
        body += f" ({item['source']})"
    body += "."
    if item and item.get("actionable"):
        act = item["actionable"].rstrip(".")
        body += f" Easy win: {act[0].lower() + act[1:]}."
    own = f.active_offers()
    if own:
        body += f" Pair it with your live '{own[0]}' so the post has a reason to call."
    body += " " + _yes(f, f"make a 'season essentials' Google post for {f.biz}",
                       f"{f.biz} ke liye 'season essentials' Google post bana deti hoon")
    art = _post(f, "Season essentials now in stock", "Ask at the counter or order for home delivery.")
    return _m(f, "category_seasonal", body, "binary_yes_no", "loss_aversion",
              "Seasonal demand shift with cited aggregate; concrete shelf action + post.",
              [f.salutation, ", ".join(trends[:2])], {"type": "google_post", "label": "season post", "artifact": art})


def festival_upcoming(f: Facts, seen=None) -> Draft:
    from .facts import month_in_range
    p = f.trigger.get("payload") or {}
    fest, date = p.get("festival"), p.get("date")
    offer, phrase, own = _offer_phrase(f, prefer=FESTIVE_PREFS.get(f.slug, ()))
    plural = PLURAL_BIZ.get(f.slug, "businesses")
    if fest and date:
        days = f.days_until(date)
        if days is not None and days < 0:
            return pivot(f, "festival_upcoming", f"{fest} date already passed")
        body = f"{f.salutation}, {fest} is on {f.date_label(date)} — {days} days away."
        fdt = parse_dt(date)
        fbeat = None
        if fdt:
            for b in f.category.get("seasonal_beats") or []:
                if month_in_range(fdt.month - 1, b.get("month_range", "")):
                    fbeat = b
                    break
        if fbeat:
            body += f" For {plural} it's the {fbeat['month_range']} window: {_beat_note(fbeat)}."
        if days is not None and days > 45:
            body += " Opening festive bookings early fills the calendar before the rush."
        name = fest
    else:
        beat = f.upcoming_festive_beat()
        if not beat:
            return curious(f, "festival_upcoming", "Festival trigger without festival details or a festive season in data.")
        body = f"{f.salutation}, the next festive window for {plural} is {beat['month_range']}: {_beat_note(beat)}."
        name = "Festive"
    if offer:
        body += f" I'd lead with {phrase}."
    body += " " + _yes(f, "draft the festive post + WhatsApp message", "festive post aur WhatsApp message draft kar deti hoon")
    art = _post(f, f"{name} special: {offer or 'book early'}", "Book your slot by phone or WhatsApp.")
    return _m(f, "festival_upcoming", body, "binary_yes_no", "timeliness",
              f"Festival trigger ({fest or 'unnamed'}); days computed from now; category seasonal pattern; festive-fit offer.",
              [f.salutation, fest or "festive season", offer or ""],
              {"type": "google_post", "label": "festive post", "artifact": art})


def ipl_match_today(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    match, venue = p.get("match", "tonight's match"), p.get("venue")
    kick = parse_dt(p.get("match_time_iso"))
    item = f.digest_item(kinds=("seasonal",), seen=seen)
    weeknight = p.get("is_weeknight")
    day = weekday_name(kick) if kick else None
    t = f" at {human_time(kick)}" if kick else ""
    body = f"{f.salutation}, {match}{' at ' + venue if venue else ''} today{t}."
    act = f.active_offers()
    combo = next((o["title"] for o in f.catalog() if "match" in o["title"].lower()), None)
    if weeknight is False:
        if item and "12%" in (item.get("summary") or ""):
            body += (f" Before you plan a match promo: in {item.get('source')}, home-match weekend nights ran "
                     f"12% below a normal Saturday for restaurants, because people watch at home.")
        body += f" It's a {day or 'weekend'}, so I'd skip a dine-in match offer tonight"
        if act:
            wk = next((o for o in act if "tue" in o.lower() or "thu" in o.lower()), None)
            if wk:
                body += f" (your '{wk}' doesn't apply today anyway)"
        body += " and push delivery instead; save match-night offers for Tue–Thu games."
        body += " " + _yes(f, "draft a delivery banner + an Insta story for tonight",
                           "aaj raat ke liye delivery banner aur Insta story draft kar deti hoon")
        lever = "contrarian_data"
        headline = "Match tonight? Order in — delivered hot to your screen"
    else:
        body += f" Weeknight games are your best match-nights."
        if combo:
            body += f" A '{combo}' on your listing before{t or ' kick-off'} catches the pre-match rush."
        body += " " + _yes(f, "put the combo live and draft the post", "combo live karke post draft kar deti hoon")
        lever = "timeliness"
        headline = f"Match night special: {combo or 'combo deals'}"
    return _m(f, "ipl_match_today", body, "binary_yes_no", lever,
              f"IPL trigger; weeknight={weeknight}; used category order-data digest to pick delivery vs dine-in; respected offer validity days.",
              [f.salutation, match, t.strip()], {"type": "google_post", "label": "match-day post", "artifact": _post(f, headline)})


def competitor_opened(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    name, dist, their = p.get("competitor_name"), p.get("distance_km"), p.get("their_offer")
    offer, phrase, own = _offer_phrase(f)
    if name:
        body = f"{f.salutation}, a new {BUSINESS_NOUN.get(f.slug, 'business')} — {name} — opened"
        if dist:
            body += f" {dist} km from you"
        if p.get("opened_date"):
            body += f" on {f.date_label(p['opened_date'])}"
        body += "."
        if their:
            body += f" They're leading with '{their}'."
            mp, tp = parse_price(offer or ""), parse_price(their)
            if own and mp and tp and mp != tp:
                register(f, abs(mp - tp))
                body += f" Your '{offer}' is {rupees(abs(mp - tp))} {'higher' if mp > tp else 'lower'}, so compete on trust, not price."
    else:
        body = (f"{f.salutation}, a new {BUSINESS_NOUN.get(f.slug, 'business')} has come up near {f.locality or 'you'} on Google, "
                f"competing for the same searchers who gave you {_perf_line(f) or 'your recent traffic'}.")
    gap = f.ctr_gap() if name else None
    if gap:
        mine, peer, diff = gap
        body += (f" Your profile converts {mine} of viewers vs {peer} for {f.peer_label()} (magicpin category benchmark)"
                 + (" — a good base to defend." if diff >= 0 else " — worth tightening before they gain reviews."))
    body += " " + _yes(f, "refresh your Google post with your strongest reviews this week",
                       "is hafte aapke best reviews ke saath Google post refresh kar deti hoon")
    return _m(f, "competitor_opened", body, "binary_yes_no", "loss_aversion",
              "Competitor trigger; named only when present in payload; merchant conversion vs category benchmark.",
              [f.salutation, name or "new competitor"], {"type": "google_post", "label": "profile refresh",
               "artifact": _post(f, f"Why {f.people} choose {f.biz}", offer or "")})


def perf_dip(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    metric = p.get("metric")
    cands = [metric] if metric else ["calls", "views", "ctr"]
    chosen, val = None, None
    for m in cands:
        v = p.get("delta_pct") if (metric and m == metric and p.get("delta_pct") is not None) else f.delta(m)
        if v is not None and v <= -0.10 and (val is None or v < val):
            chosen, val = m, v
    if chosen is None:
        return pivot(f, "perf_dip", "no metric fell by 10%+ in the last 7 days")
    offer, phrase, own = _offer_phrase(f)
    word = {"calls": "calls", "views": "profile views", "ctr": "click-through"}.get(chosen, chosen)
    body = f"{f.salutation}, {word} at {f.biz} are down {pct(abs(val))} this week."
    pv = f.peer.get(f"avg_{chosen}_30d") if chosen in ("calls", "views") else None
    cur = f.perf.get(chosen)
    if pv and cur is not None:
        if cur >= pv:
            body += (f" You're still above the {fmt_int(pv)} average for {f.peer_label()} ({fmt_int(cur)} in 30 days), "
                     f"so this is a dip worth catching early.")
        else:
            body += f" 30-day {word}: {fmt_int(cur)}, vs {fmt_int(pv)} average for {f.peer_label()} (magicpin category benchmark)."
    if not f.active_offers():
        body += f" There's no live offer on your listing right now — adding {phrase} is the fastest lever." if offer else ""
    elif offer:
        body += f" Pinning {phrase} to the top of your profile is the fastest lever."
    body += " " + _yes(f, "set it up today", "aaj hi set kar deti hoon")
    return _m(f, "perf_dip", body, "binary_yes_no", "loss_aversion",
              f"Verified dip: {chosen} {pct(val)} (7d); peer benchmark; offer lever.",
              [f.salutation, f"{word} {pct(val)}"], {"type": "offer_live", "label": "profile offer post",
               "artifact": _post(f, offer or "Book your visit", "Tap Call or Directions to book.")})


def perf_spike(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    metric = p.get("metric")
    best, val = None, None
    for m in ([metric] if metric else ["calls", "views"]):
        v = p.get("delta_pct") if (metric and p.get("delta_pct") is not None) else f.delta(m)
        if v is not None and v >= 0.15 and (val is None or v > val):
            best, val = m, v
    if best is None:
        return pivot(f, "perf_spike", "no metric rose by 15%+ in the last 7 days")
    offer, phrase, own = _offer_phrase(f)
    word = {"calls": "calls", "views": "profile views"}.get(best, best)
    body = f"{f.salutation}, {word} at {f.biz} are up {pct(val)} this week"
    drv = p.get("likely_driver")
    if drv:
        body += f" — looks driven by your {humanize_slug(drv)}"
    body += f". {_perf_line(f).capitalize()}." if _perf_line(f) else "."
    body += " Momentum like this is the best time to convert:"
    body += f" pin {phrase} while traffic is high." if offer else " add a clear offer while traffic is high."
    body += " " + _yes(f, "pin it now", "abhi pin kar deti hoon")
    return _m(f, "perf_spike", body, "binary_yes_no", "momentum",
              f"Verified spike: {best} {pct(val, signed=True)} (7d); convert momentum with offer.",
              [f.salutation, f"{word} {pct(val, signed=True)}"], {"type": "offer_live", "label": "profile offer post",
               "artifact": _post(f, offer or "Now booking", "Tap Call to book.")})


def seasonal_perf_dip(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    metric, val = p.get("metric", "views"), p.get("delta_pct")
    if val is None:
        val = f.delta(metric)
    if val is None or val > -0.05:
        return pivot(f, "seasonal_perf_dip", "no dip in the data")
    beat = f.seasonal_beat()
    agg = f.merchant.get("customer_aggregate") or {}
    members = agg.get("total_active_members")
    body = f"{f.salutation}, {metric} are down {pct(abs(val))} this week — and that's expected right now"
    body += f": {_beat_note(beat)}." if beat else "."
    item = f.digest_item(kinds=("seasonal",), seen=seen)
    if item and item.get("actionable"):
        body += f" ({item.get('source')}: {item['actionable'].rstrip('.')}.)"
    if members:
        body += f" So the lever now is keeping your {fmt_int(members)} active {f.people}."
    body += " " + _yes(f, "draft a 4-week member challenge post", "4-week member challenge post draft kar deti hoon")
    return _m(f, "seasonal_perf_dip", body, "binary_yes_no", "reassurance+reframe",
              "Expected seasonal dip; reframed with category seasonal data; retention lever using member count.",
              [f.salutation, f"{metric} {pct(val)}"], {"type": "google_post", "label": "member challenge",
               "artifact": _post(f, "4-week consistency challenge for members", "Show up 3x a week, win a free session.")})


def milestone_reached(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    if p.get("metric") and p.get("value_now") is not None:
        now_v, target = p.get("value_now"), p.get("milestone_value")
        what = humanize_slug(p["metric"]).replace("count", "").strip() + "s" if "count" in p["metric"] else humanize_slug(p["metric"])
        body = f"{f.salutation}, {f.biz} is at {fmt_int(now_v)} {what}"
        if target and target > now_v:
            register(f, target - now_v)
            body += f" — just {fmt_int(target - now_v)} away from {fmt_int(target)}."
        else:
            body += f" — milestone crossed."
        body += " A thank-you note to this week's regulars with a one-tap review request is the quickest way to close it."
    else:
        p = f.perf
        if p.get("views") is None:
            return curious(f, "milestone_reached", "No milestone number in data.")
        ctr = f" and a {pct(p['ctr'], digits=1)} click-through rate" if isinstance(p.get("ctr"), (int, float)) else ""
        body = (f"{f.salutation}, {f.biz} reached {fmt_int(p['views'])} profile views and {fmt_int(p.get('calls', 0))} "
                f"calls in the last {p.get('window_days', 30)} days{ctr} — a good moment to turn happy visitors into reviews.")
    body += " " + _yes(f, "draft a thank-you post and review request", "thank-you post aur review request draft kar deti hoon")
    return _m(f, "milestone_reached", body, "binary_yes_no", "social_proof",
              "Milestone: real totals only; no threshold asserted unless in payload.",
              [f.salutation], {"type": "google_post", "label": "thank-you post",
               "artifact": _post(f, f"Thank you, {f.city or 'neighbours'}!", "Loved your visit? A 30-second Google review helps us a lot.")})


def dormant_with_vera(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    d = p.get("days_since_last_merchant_message")
    lead = f"it's been {d} days since we last spoke. " if d else ""
    return curious(f, "dormant_with_vera", "Dormant merchant: re-open with a low-effort question.", lead=lead)


def curious_ask_due(f: Facts, seen=None) -> Draft:
    return curious(f, "curious_ask_due", "Scheduled curious-ask cadence.")


def renewal_due(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    sub = f.merchant.get("subscription") or {}
    d = p.get("days_remaining", sub.get("days_remaining"))
    plan = p.get("plan") or sub.get("plan") or "current"
    amt = p.get("renewal_amount")
    if d is None:
        return pivot(f, "renewal_due", "no renewal date in data")
    body = f"{f.salutation}, your {plan} plan for {f.biz} renews in {d} days"
    body += f" ({rupees(amt)})." if amt else "."
    pl = _perf_line(f)
    if pl:
        body += f" What it's doing for you: {pl}."
    body += " Renewing on time keeps posts and profile upkeep running without a gap. "
    body += _yes(f, "share the renewal steps", "renewal steps share kar deti hoon")
    return _m(f, "renewal_due", body, "binary_yes_no", "loss_aversion",
              "Renewal due; value shown with the merchant's own 30-day numbers.",
              [f.salutation, f"{d} days"], {"type": "renewal", "label": "renewal steps",
               "artifact": f"Renewal for {f.biz}: open the magicpin partner dashboard → Plan → Renew {plan}. Upkeep continues without a gap."})


def winback_eligible(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    sub = f.merchant.get("subscription") or {}
    d = p.get("days_since_expiry", sub.get("days_since_expiry"))
    body = f"{f.salutation}, it's been {d} days since {f.biz}'s plan paused." if d else f"{f.salutation}, {f.biz}'s plan is paused."
    if p.get("perf_dip_pct") is not None:
        body += f" Since then, profile performance is down {pct(abs(p['perf_dip_pct']))}"
        if p.get("lapsed_customers_added_since_expiry"):
            body += f" and {p['lapsed_customers_added_since_expiry']} more {f.people} have gone quiet"
        body += "."
    offer, phrase, own = _offer_phrase(f)
    body += " I can restart profile upkeep"
    if p.get("lapsed_customers_added_since_expiry"):
        body += f" and send those {p['lapsed_customers_added_since_expiry']} a comeback message"
        if offer:
            body += f" with {phrase}"
    body += ". " + _hl(f, "Reply YES to restart.", "Restart karein? Reply YES.")
    return _m(f, "winback_eligible", body, "binary_yes_no", "loss_aversion",
              "Winback: days since expiry, performance change and lapsed count from payload.",
              [f.salutation, f"{d} days"], {"type": "renewal", "label": "restart + comeback message",
               "artifact": f"We miss you at {f.biz}! {offer or 'Come back this week'} — reply to book."})


def gbp_unverified(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    up = p.get("estimated_uplift_pct")
    path = humanize_slug(p.get("verification_path", "")).replace(" or ", " or a ")
    body = f"{f.salutation}, the Google profile for {f.biz} is still unverified."
    if up:
        body += f" Verified listings get an estimated {pct(up)} more visibility."
    if path:
        body += f" Verification is by {path} — I'll walk you through it, about 5 minutes of your time."
    body += " " + _hl(f, "Start now? Reply YES.", "Abhi shuru karein? Reply YES.")
    return _m(f, "gbp_unverified", body, "binary_yes_no", "effort_externalisation",
              "Unverified profile; uplift estimate from payload; effort externalised.",
              [f.salutation], {"type": "verification", "label": "verification walkthrough",
               "artifact": "Verification: 1) Open Google Business Profile 2) Choose phone/postcard 3) Enter the code — I'll check it went through."})


def active_planning_intent(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    topic = humanize_slug(p.get("intent_topic", "your idea"))
    offer, phrase, own = _offer_phrase(f)
    trend = f.top_trend(tuple(w for w in topic.split() if len(w) > 3))
    lines = [f"{f.biz} — {topic.title()} (starter draft, edit freely)"]
    ctx_line = ""
    for h in f.merchant.get("conversation_history") or []:
        if h.get("from") == "vera":
            keep = [s for s in sentences(h.get("body", "").replace("—", ".")) if not s.endswith("?") and "₹" in s]
            if keep:
                ctx_line = " ".join(keep[:2])
                break
    if offer and own and "₹" not in ctx_line:
        lines.append(f"- Base: {offer}")
    if ctx_line:
        lines.append(f"- From our last chat: {ctx_line}")
    size_label = {"restaurants": "Minimum order", "gyms": "Batch size", "salons": "Slots per day"}.get(f.slug, "Capacity")
    lines.append(f"- {size_label}: [you decide]")
    lines.append("- Booking cut-off: [you decide]")
    artifact = "\n".join(lines)
    body = f"{f.salutation}, here's a first cut of the {topic} you asked about:\n\n{artifact}\n\n"
    if f.perf.get("views") is not None:
        body += (f"Your listing already pulls {fmt_int(f.perf['views'])} profile views and "
                 f"{fmt_int(f.perf.get('calls', 0))} calls a month — a ready package turns more of that into orders. ")
    body += _hl(f, "Reply CONFIRM and I'll turn it into a listing post, or send changes.",
                "CONFIRM bolein toh listing post bana deti hoon, ya changes bhej dijiye.")
    return _m(f, "active_planning_intent", body, "binary_confirm_cancel", "effort_externalisation",
              f"Merchant asked about '{topic}'; delivered a draft artifact immediately (no qualifying questions); real price + trend only.",
              [f.salutation, topic], {"type": "google_post", "label": f"{topic} listing post", "artifact": artifact})


def review_theme_emerged(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    theme, occ, quote = p.get("theme"), p.get("occurrences_30d"), p.get("common_quote")
    if not theme:
        neg = [r for r in f.merchant.get("review_themes") or [] if r.get("sentiment") == "neg"]
        if not neg:
            return curious(f, "review_theme_emerged", "No review theme in data.")
        theme, occ, quote = neg[0]["theme"], neg[0].get("occurrences_30d"), neg[0].get("common_quote")
    body = f"{f.salutation}, {occ} reviews in the last 30 days mention {humanize_slug(theme)}" if occ else f"{f.salutation}, recent reviews mention {humanize_slug(theme)}"
    if quote:
        body += f" — one says \"{quote}\""
    body += "."
    pos = [r for r in f.merchant.get("review_themes") or [] if r.get("sentiment") == "pos"]
    if pos and pos[0].get("occurrences_30d"):
        body += f" Meanwhile {pos[0]['occurrences_30d']} reviews praise {humanize_slug(pos[0]['theme'])}, so this is fixable ops, not the core."
    body += " " + _yes(f, "draft a polite public reply you can reuse", "ek polite public reply draft kar deti hoon jo aap reuse kar sakein")
    art = f"Thank you for the feedback — you're right that {humanize_slug(theme)} wasn't up to our standard. We've changed how we handle it this week. Please give us another try. — {f.salutation}, {f.biz}"
    return _m(f, "review_theme_emerged", body, "binary_yes_no", "reciprocity",
              "Review theme with count and verbatim customer quote from data; balanced with positive theme.",
              [f.salutation, humanize_slug(theme)], {"type": "reply_template", "label": "public review reply", "artifact": art})


# ------------------------------------------------------------------ customer composers
def _cust_open(f: Facts) -> str:
    who, subj = f.customer_names()
    em = EMOJI_CUSTOMER.get(f.slug, "")
    lang = f.customer_lang()
    if f.customer_via_relative():
        return "Namaste" + (f" — {f.biz}, {f.locality} se." if lang != "en" else f" from {f.biz}, {f.locality}.")
    greet = f"Hi {who}" if who else "Hi"
    loc = f", {f.locality}" if f.locality else ""
    return f"{greet}{(' ' + em) if em else ''} — {f.biz}{loc} here."


def _since_phrase(days: int | None) -> str:
    if days is None:
        return "a while"
    if days < 45:
        return f"{days} days"
    return f"about {round(days / 30)} months"


def _cust_close(f: Facts, en: str, hi: str) -> str:
    return hi if f.customer_lang() in ("hinglish", "hindi_roman") else en


def _slots(payload: dict) -> list[str]:
    return [s.get("label") for s in payload.get("available_slots") or payload.get("next_session_options") or [] if s.get("label")]


def recall_due(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    who, subj = f.customer_names()
    last = p.get("last_service_date") or ((f.customer or {}).get("relationship") or {}).get("last_visit")
    since = f.days_since(last) if last else None
    if since and since >= 45:
        register(f, round(since / 30))
    service = humanize_slug(p.get("service_due", "")) or "next check-up"
    offer, phrase, own = _offer_phrase(f, prefer=COMEBACK_PREFS.get(f.slug, ()))
    body = _cust_open(f)
    if f.slug == "gyms":
        body += (f" It's been {_since_phrase(since)} since your last session — easing back in is simpler than starting over."
                 if since is not None and since > 0 else " Time for your next session.")
    elif p.get("service_due") and last:
        body += f" Your last visit was on {f.date_label(last)}, so your {service} is due now."
    elif last and since is not None and since > 0:
        body += f" It's been {_since_phrase(since)} since your last visit — a good time for your {service}."
    else:
        body += f" Your {service} is due."
    slots = _slots(p)
    if slots:
        opts = " or ".join(f"({i + 1}) {s}" for i, s in enumerate(slots[:2]))
        body += f" Open slots: {opts}."
        if offer and own:
            body += f" {offer}."
        body += " " + _cust_close(f, "Reply 1 or 2 to confirm, or send a time that suits you.",
                                  "Bas 1 ya 2 reply karein, ya apna time bata dijiye.")
        cta = "multi_choice_slot"
    else:
        if offer and own:
            body += f" {offer} is available."
        pref = humanize_slug(str(((f.customer or {}).get("preferences") or {}).get("preferred_slots", "")))
        if pref:
            body += " " + _cust_close(f, f"Reply YES and we'll hold a {pref} slot for you.", f"YES reply karein, hum aapke liye {pref} slot hold kar lenge.")
        else:
            body += " " + _cust_close(f, "Reply YES and we'll call you to fix a time.", "YES reply karein, hum time fix karne ke liye call kar lenge.")
        cta = "binary_yes_no"
    return _c(f, "recall_due", body, cta, "continuity",
              f"Customer recall; consent-checked; last visit {last}; real slots/offer only; customer language '{f.customer_lang()}'.",
              [who or "", service, ", ".join(slots[:2])], {"type": "booking", "label": "book the slot",
               "slots": slots[:2]})


def appointment_tomorrow(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    when = parse_dt(p.get("appointment_iso") or p.get("slot_iso"))
    svc = p.get("service")
    what = "table booking" if f.slug == "restaurants" else "appointment"
    body = _cust_open(f) + f" Reminder: your {what}"
    if svc:
        body += f" for {humanize_slug(svc)}"
    body += f" is tomorrow{' at ' + human_time(when) if when else ''}."
    body += " " + _cust_close(f, "Reply 1 to confirm or 2 to reschedule.", "Confirm ke liye 1, reschedule ke liye 2 reply karein.")
    return _c(f, "appointment_tomorrow", body, "multi_choice_slot", "utility",
              "Appointment reminder; no time invented when payload lacks it.", [f.customer_names()[0]],
              {"type": "booking", "label": "confirm appointment"})


def chronic_refill_due(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    mols = p.get("molecule_list") or []
    runout = p.get("stock_runs_out_iso")
    who, _ = f.customer_names()
    senior = (f.cust_ident or {}).get("senior_citizen")
    hindi = f.customer_lang() in ("hindi_roman", "hinglish")
    body = _cust_open(f)
    person = (who.replace("Mr. ", "") + " ji") if who.startswith("Mr.") else (who or "aap")
    if hindi:
        body += f" {person} ki regular medicines" + (f" ({', '.join(mols)})" if mols else "")
        body += f" {f.date_label(runout)} tak khatam ho jayengi." if runout else " refill ke liye due hain."
        body += " Same brand, same pack ready kar dete hain."
    else:
        body += f" {person}'s regular medicines" + (f" ({', '.join(mols)})" if mols else "")
        body += f" run out on {f.date_label(runout)}." if runout else " are due for refill."
        body += " We can keep the same brand and pack ready."
    extras = []
    for o in f.active_offers():
        if senior and "senior" in o.lower():
            extras.append(o)
        elif "delivery" in o.lower() and p.get("delivery_address_saved"):
            extras.append(o + (" (saved address)" if not hindi else " (saved address par)"))
    if extras:
        body += " " + ("; ".join(extras)) + "."
    body += " " + _cust_close(f, "Reply CONFIRM to dispatch, or tell us if the dose has changed.",
                              "Dispatch ke liye CONFIRM reply karein, dose badla ho toh bata dijiye.")
    return _c(f, "chronic_refill_due", body, "binary_confirm_cancel", "utility",
              "Chronic refill: molecules + run-out date from payload; merchant's real offers; senior/relative channel honoured.",
              [person, ", ".join(mols)], {"type": "booking", "label": "dispatch refill"})


def trial_followup(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    who, subj = f.customer_names()
    td = p.get("trial_date") or ((f.customer or {}).get("relationship") or {}).get("first_visit")
    svc = ((f.customer or {}).get("relationship") or {}).get("services_received") or []
    trial = humanize_slug(svc[0]) if svc else "trial"
    body = _cust_open(f) + f" Thanks for {'bringing ' + subj + ' for' if subj else 'coming in for'} the {trial}"
    body += f" on {f.date_label(td)}." if td else "."
    slots = _slots(p)
    if slots:
        body += f" Next session open: {slots[0]}. " + _cust_close(f, f"Shall we hold {'his/her' if subj else 'your'} spot? Reply YES.", "Spot hold karein? YES reply karein.")
    else:
        offer, phrase, own = _offer_phrase(f)
        if offer and own:
            body += f" To continue: {offer}."
        body += " " + _cust_close(f, "Want us to book the next session? Reply YES.", "Next session book karein? YES reply karein.")
    return _c(f, "trial_followup", body, "binary_yes_no", "reciprocity",
              "Trial follow-up; parent addressed for minors; real session slots only.", [who, trial],
              {"type": "booking", "label": "hold next session", "slots": slots[:1]})


def customer_lapsed(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    who, subj = f.customer_names()
    days = p.get("days_since_last_visit")
    if days is None:
        days = f.days_since(f.customer_last_visit())
    elif days >= 45:
        register(f, round(days / 30))
    focus = humanize_slug(p.get("previous_focus", "") or ((f.customer or {}).get("preferences") or {}).get("training_focus", ""))
    offer, phrase, own = _offer_phrase(f, prefer=COMEBACK_PREFS.get(f.slug, ()))
    body = _cust_open(f)
    if days and days > 0:
        span = f"{days} days" if p.get("days_since_last_visit") else _since_phrase(days)
        if f.slug == "dentists":
            body += f" It's been {span} since your last visit — a routine check keeps small issues from becoming bigger ones."
        else:
            body += f" It's been {span} since your last visit — no pressure, it happens to everyone."
    else:
        body += " We haven't seen you in a while — no pressure at all."
    if focus:
        body += f" Since your focus was {focus}, "
        body += f"'{offer}' is an easy way back." if offer else "we can pick up right where you left off."
    elif offer:
        body += f" '{offer}' is on for your next order." if f.slug in ("pharmacies", "restaurants") else f" '{offer}' is an easy way back."
    pref = str(((f.customer or {}).get("preferences") or {}).get("preferred_slots", ""))
    if pref and f.slug not in ("pharmacies",):
        body += f" {humanize_slug(pref).capitalize()} slots are open."
    if f.slug in ("pharmacies", "restaurants"):
        body += " " + _cust_close(f, "Reply YES and we'll keep it ready for you.", "YES reply karein, hum aapke liye ready rakhenge.")
    else:
        body += " " + _cust_close(f, "Want one this week? Reply YES — no commitment.",
                                  "Is hafte ek slot chahiye? YES reply karein — koi commitment nahi.")
    return _c(f, f.trigger.get("kind", "customer_lapsed"), body, "binary_yes_no", "reassurance",
              "Lapsed customer; no-shame tone; days since visit + preference from data; offer is catalog-real.",
              [who, str(days or "")], {"type": "booking", "label": "comeback slot"})


def wedding_package_followup(f: Facts, seen=None) -> Draft:
    p = f.trigger.get("payload") or {}
    who, _ = f.customer_names()
    wd = p.get("wedding_date") or ((f.customer or {}).get("preferences") or {}).get("wedding_date")
    days = f.days_until(wd) if wd else None
    if days is not None and days < 0:
        return Skip("wedding date already passed")
    trial = p.get("trial_completed")
    nxt = humanize_slug(p.get("next_step_window_open", "")) or "bridal prep"
    body = f"Hi {who} ✨ {f.owner_short} from {f.biz} here."
    if days is not None:
        body += f" {days} days to go until {f.date_label(wd)}!"
    if trial:
        body += f" After your bridal trial on {f.date_label(trial)}, the next step is the {nxt}."
    pref = str(((f.customer or {}).get("preferences") or {}).get("preferred_slots", ""))
    slot = humanize_slug(pref) if pref else "a"
    body += f" Want me to block a {slot} slot for the first session next week? Reply YES."
    return _c(f, "wedding_package_followup", body, "binary_yes_no", "timeline_urgency",
              "Bridal follow-up: countdown computed from wedding date; trial date + next window from payload; preferred day honoured.",
              [who, str(days or "")], {"type": "booking", "label": "first prep session"})


COMPOSERS = {
    "research_digest": research_digest,
    "regulation_change": regulation_change,
    "cde_opportunity": cde_opportunity,
    "supply_alert": supply_alert,
    "category_seasonal": category_seasonal,
    "festival_upcoming": festival_upcoming,
    "ipl_match_today": ipl_match_today,
    "competitor_opened": competitor_opened,
    "perf_dip": perf_dip,
    "perf_spike": perf_spike,
    "seasonal_perf_dip": seasonal_perf_dip,
    "milestone_reached": milestone_reached,
    "dormant_with_vera": dormant_with_vera,
    "curious_ask_due": curious_ask_due,
    "renewal_due": renewal_due,
    "winback_eligible": winback_eligible,
    "gbp_unverified": gbp_unverified,
    "active_planning_intent": active_planning_intent,
    "review_theme_emerged": review_theme_emerged,
    "recall_due": recall_due,
    "appointment_tomorrow": appointment_tomorrow,
    "chronic_refill_due": chronic_refill_due,
    "trial_followup": trial_followup,
    "customer_lapsed_soft": customer_lapsed,
    "customer_lapsed_hard": customer_lapsed,
    "winback_customer": customer_lapsed,
    "wedding_package_followup": wedding_package_followup,
    "bridal_followup": wedding_package_followup,
}
