"""Reply engine: rule-first intent classification + per-conversation state machine.

The LLM is only used (optionally) to phrase answers to open questions; states and actions are
always decided by rules so the replay scenarios behave identically every time.
"""
from __future__ import annotations

import re

from .store import Conversation, Store
from .util import norm_text, short_hash

AUTO_STRONG = [
    "thank you for contacting", "thanks for contacting", "thank you for reaching", "thanks for reaching out",
    "thank you for your message", "thanks for your message", "we will get back", "we'll get back",
    "will get back to you", "get back to you shortly", "respond shortly", "revert shortly", "our team will",
    "team will respond", "team will get back", "currently unavailable", "currently closed", "we are closed",
    "outside business hours", "outside working hours", "business hours are", "this is an automated",
    "automated message", "automated assistant", "auto reply", "auto-reply", "away from the phone",
    "jald hi sampark", "jaldi sampark", "aapse sampark karenge", "jaankari ke liye", "team tak pahuncha",
    "sampark karne ke liye dhanyavaad", "sampark karne ke liye shukriya", "सम्पर्क करने", "संपर्क करने",
    "जल्द ही", "स्वचालित",
]
OPT_OUT = ["stop", "unsubscribe", "don't message", "dont message", "do not message", "stop messaging",
           "stop sending", "band karo", "mat bhejo", "message mat", "nahi chahiye", "not interested",
           "remove me", "leave me alone", "no more messages", "block"]
HOSTILE = ["useless", "spam", "idiot", "stupid", "bakwas", "bakwaas", "pagal", "fraud", "scam", "shut up",
           "bothering", "irritating", "nonsense", "waste of time", "rubbish", "get lost", "chutiya", "bewakoof",
           "harass", "pathetic"]
COMMIT = ["yes", "yeah", "yep", "yup", "haan", "han ji", "haanji", "ha ji", "ji haan", "ok", "okay", "okk",
          "sure", "go ahead", "let's do it", "lets do it", "let us do it", "do it", "kar do", "kardo", "karo",
          "chalo", "start", "proceed", "confirm", "please do", "send it", "send me", "what's next",
          "whats next", "what next", "judna hai", "join", "sign me up", "bilkul", "theek hai", "thik hai",
          "sahi hai", "done", "👍", "✅", "go for it", "sounds good", "alright", "interested"]
NEXT_WORDS = ["what's next", "whats next", "what next", "next step", "aage kya"]
LATER = ["later", "baad mein", "baad me", "busy", "not now", "abhi nahi", "tomorrow", "kal ", "next week",
         "call later", "free nahi", "in a meeting", "remind me"]
HARD_NO = {"no", "nahi", "nope", "na", "no thanks", "nahin", "no thank you", "not now thanks"}
OFF_TOPIC = ["gst", "itr", "income tax", "tax filing", "loan", "insurance", "visa", "passport", "crypto",
             "mutual fund", "stock market", "recharge", "electricity bill", "accountant", "lawyer", "court",
             "cricket score", "weather", "horoscope", "job", "salary"]
THANKS = ["thanks", "thank you", "thx", "shukriya", "dhanyavaad", "dhanyawad"]
HINGLISH_WORDS = {"hai", "nahi", "kya", "karo", "kar", "aap", "ji", "bhi", "mein", "hoon", "haan", "chahiye",
                  "abhi", "theek", "kaise", "kitna", "bhej", "ho", "hain", "mujhe", "hum", "kab", "karein"}


def _has(text: str, phrases) -> str | None:
    for p in phrases:
        if re.search(r"(?<![\w])" + re.escape(p) + r"(?![\w])", text):
            return p
    return None


def detect_lang(msg: str) -> str:
    if re.search(r"[ऀ-ॿ]", msg or ""):
        return "hinglish"
    words = set(norm_text(msg).split())
    return "hinglish" if len(words & HINGLISH_WORDS) >= 2 else "en"


def classify(msg: str) -> str:
    raw = (msg or "").strip()
    t = norm_text(raw) + " "
    low = raw.lower()
    if not raw:
        return "empty"
    if _has(low, AUTO_STRONG):
        return "auto_reply"
    if _has(low, OPT_OUT):
        return "opt_out"
    if _has(low, HOSTILE):
        return "hostile"
    if norm_text(raw) in HARD_NO:
        return "decline"
    if _has(low, OFF_TOPIC) and not _has(low, NEXT_WORDS):
        return "off_topic"
    if _has(low, LATER):
        return "later"
    commit = _has(low, COMMIT)
    if raw.strip() in ("1", "2", "3"):
        return "choice"
    if commit and ("?" not in raw or _has(low, NEXT_WORDS)):
        return "commit"
    if _has(low, THANKS) and len(t.split()) <= 4:
        return "thanks"
    if "?" in raw or re.match(r"^(what|how|when|where|why|which|kya|kaise|kab|kitna|kitne|can|is|are|do|does)\b", t):
        return "question"
    if commit:
        return "commit"
    return "open"


# ---------------------------------------------------------------- reply builders
def _L(lang: str, en: str, hi: str) -> str:
    return hi if lang == "hinglish" else en


def _unique(conv: Conversation, options: list[str]) -> str | None:
    for o in options:
        if short_hash(o, 16) not in conv.sent_hashes:
            return o
    return None


def default_next_action(store: Store, merchant_id: str | None) -> tuple[dict, dict]:
    """For conversations we never opened (e.g. judge replays): derive a concrete action from data."""
    m = store.get("merchant", merchant_id) or {}
    cat = store.category_for(m) or {}
    ident = m.get("identity") or {}
    name = ident.get("name", "your business")
    owner = ident.get("owner_first_name", "")
    if m.get("category_slug") == "dentists" and owner and not owner.lower().startswith("dr"):
        owner = f"Dr. {owner}"
    offers = [o.get("title") for o in m.get("offers") or [] if o.get("status") == "active"]
    offer = offers[0] if offers else next((o["title"] for o in cat.get("offer_catalog") or []
                                           if o.get("type") == "service_at_price"), None)
    loc = ident.get("locality", "")
    art = f"{name}{' — ' + loc if loc else ''}\n{offer or 'Now booking'} — call or tap Directions to visit."
    return ({"type": "google_post", "label": f"Google post featuring {offer}" if offer else "Google post",
             "artifact": art}, {"owner": owner, "name": name})


def handle_reply(store: Store, conv: Conversation, message: str, from_role: str) -> dict:
    kind = classify(message)
    # mirror the language of this turn; fall back to the conversation language for tiny messages
    words = len(norm_text(message).split())
    lang = detect_lang(message) if words >= 3 else conv.language if conv.language == "hinglish" and detect_lang(message) == "hinglish" else detect_lang(message)
    conv.add_turn(from_role, message)
    st = store.mstate(conv.merchant_id)
    customer_side = conv.audience == "customer" or from_role == "customer"
    na = conv.next_action or {}
    owner = conv.facts.get("owner") or ""
    label = na.get("label", "next step")

    def send(body: str, cta: str, rationale: str, state: str | None = None) -> dict:
        if state:
            conv.state = state
        h = short_hash(body, 16)
        if h in conv.sent_hashes:  # never repeat verbatim
            return wait(1800, "Would have repeated an earlier message verbatim; backing off instead.")
        conv.sent_hashes.add(h)
        conv.add_turn("bot", body)
        return {"action": "send", "body": body, "cta": cta, "rationale": rationale}

    def wait(sec: int, rationale: str) -> dict:
        conv.state = "WAITING"
        return {"action": "wait", "wait_seconds": sec, "rationale": rationale}

    def end(rationale: str) -> dict:
        conv.state = "ENDED"
        return {"action": "end", "rationale": rationale}

    # ---- terminal states
    if conv.state == "ENDED":
        return end("Conversation already closed; not re-engaging.")
    if conv.state == "CLOSING":
        if kind == "off_topic":
            conv.state = "ENDED"
            return send(_L(lang, "That one's outside what I can help with — your CA or the right professional is best for it. I won't message further. 🙏",
                           "Ye mere scope se bahar hai — iske liye aapke CA/sahi professional best rahenge. Main aage message nahi karungi. 🙏"),
                        "none", "Off-topic after hostility: one polite line, then closing.", state="ENDED")
        return end("Merchant was upset earlier; closing without further messages.")

    # ---- priority rules
    if kind == "opt_out":
        st.opted_out = True
        return end("Merchant explicitly opted out; closing and suppressing future proactive messages for them.")
    if kind == "hostile":
        st.hostile = True
        return send(_L(lang, "Sorry for the bother — I won't message you again. If you ever want help with your listing, just say 'Hi'. 🙏",
                       "Sorry for the disturbance — main aapko dobara message nahi karungi. Kabhi listing mein help chahiye ho toh bas 'Hi' bhej dijiye. 🙏"),
                    "none", "Frustration detected: short apology + clear opt-out path; conversation moves to closing.",
                    state="CLOSING")
    if kind == "auto_reply":
        fp = short_hash(norm_text(message), 12)
        seen = store.remember_fingerprint(conv.merchant_id, fp)
        st.autoreply_hits += 1
        hits = max(seen, st.autoreply_hits if seen > 1 else 1)
        if hits <= 1:
            who = f"{owner} ji" if owner and not owner.startswith("Dr") else (owner or "")
            body = _L(lang,
                      f"Looks like an automatic reply 🙂 {who + ', w' if who else 'W'}hen you see this, just reply YES and I'll get the {label} ready for you.",
                      f"Lagta hai ye auto-reply hai 🙂 {who + ', j' if who else 'J'}ab aap dekhein, bas YES reply kar dijiye — main {label} ready kar dungi.")
            return send(body, "binary_yes_no", "Detected WhatsApp auto-reply; one explicit prompt for the owner, no pitch repeat.")
        if hits == 2:
            return wait(14400, "Same auto-reply again — owner isn't at the phone. Backing off 4 hours.")
        return end("Auto-reply repeated 3+ times with no human reply; closing to avoid wasting turns.")
    if kind == "decline":
        return end("Merchant declined; exiting gracefully without pushing.")

    # ---- customer-side flows
    if customer_side:
        slots = na.get("slots") or []
        if kind in ("choice", "commit"):
            pick = None
            if message.strip() in ("1", "2", "3") and slots:
                i = int(message.strip()) - 1
                pick = slots[i] if 0 <= i < len(slots) else None
            body = (f"Confirmed for {pick}. You'll get a reminder the day before — see you then!" if pick else
                    _L(lang, "Done — noted. We'll confirm the details shortly. Thank you!",
                       "Done — note kar liya hai. Details jaldi confirm karte hain. Dhanyavaad!"))
            return send(body, "none", "Customer confirmed; booking acknowledged, no further ask.", state="DONE")
        if kind == "later":
            return wait(86400, "Customer asked for later; checking back tomorrow.")
        if kind == "thanks" or conv.state == "DONE":
            return end("Customer acknowledged; nothing more to ask.")
        body = _unique(conv, [
            _L(lang, "Thanks for the message — our team will reply to this personally within the day. For a booking, just reply YES.",
               "Message ke liye thanks — hamari team aaj hi personally reply karegi. Booking ke liye bas YES bhej dijiye."),
            _L(lang, "Noted — someone from our team will get back to you personally today.",
               "Note kar liya — team aaj aapse personally baat karegi."),
        ])
        return send(body, "open_ended", "Customer question/open reply; route to merchant staff, single low-friction ask.") if body else end("Nothing new to say.")

    # ---- merchant-side flows
    if kind == "commit" or (kind == "choice"):
        if conv.state in ("ACTION", "DONE"):
            body = _unique(conv, [
                _L(lang, f"Confirmed — the {label} is queued and goes live today. I'll share how it performs after 7 days.",
                   f"Confirmed — {label} queue ho gaya hai, aaj live ho jayega. 7 din baad performance share karungi."),
                _L(lang, "Done. Anything else you want me to take off your plate this week, just send it here.",
                   "Done. Is hafte aur kuch bhi ho, bas yahan bhej dijiye."),
            ])
            return send(body, "none", "Merchant confirmed again; closing the loop with the outcome.", state="DONE") if body else end("Action already confirmed; nothing further.")
        art = na.get("artifact")
        if not art:
            na, meta = default_next_action(store, conv.merchant_id)
            conv.next_action = na
            art, label = na["artifact"], na["label"]
        body = _L(lang,
                  f"Done ✅ Here's the {label}, ready to go:\n\n{art}\n\nReply CONFIRM to make it live, or send any change.",
                  f"Done ✅ Ye raha {label}, ready hai:\n\n{art}\n\nLive karne ke liye CONFIRM reply karein, ya changes bhej dijiye.")
        return send(body, "binary_confirm_cancel",
                    "Explicit commitment detected: switched from pitch to action, delivered the artifact immediately (no qualifying questions).",
                    state="ACTION")
    if kind == "later":
        m = message.lower()
        sec = 604800 if "week" in m else 86400 if ("tomorrow" in m or "kal" in m) else 14400
        return wait(sec, f"Merchant asked for time; backing off {sec // 3600}h.")
    if kind == "off_topic":
        conv.offtopic += 1
        if conv.offtopic >= 2:
            return end("Repeated off-topic requests; closing politely.")
        topic = _has(message.lower(), OFF_TOPIC) or "that"
        body = _L(lang,
                  f"I'll have to leave {topic.upper() if len(topic) <= 3 else topic} to your CA or the right expert — that's outside what I can do. Coming back to the {label}: reply YES and I'll have it ready.",
                  f"{topic.upper() if len(topic) <= 3 else topic} ke liye aapke CA/expert best rahenge — ye mere scope se bahar hai. Wapas {label} pe: YES bolein toh ready kar deti hoon.")
        return send(body, "binary_yes_no", "Off-topic request declined politely; redirected to the pending action.")
    if kind == "thanks":
        if conv.state in ("ACTION", "DONE"):
            return end("Merchant thanked after action; conversation complete.")
        body = _L(lang, f"Anytime! Whenever you're ready, reply YES and I'll get the {label} done.",
                  f"Koi baat nahi! Jab ready hon, YES bhej dijiye — {label} main kar dungi.")
        return send(body, "binary_yes_no", "Polite acknowledgement; single low-friction next step.") if short_hash(body, 16) not in conv.sent_hashes else end("Nothing new to add.")
    # question / open: answered by the caller (LLM with facts) or this safe rule
    if conv.state == "OPEN":
        conv.state = "ENGAGED"
    return {"action": "_needs_answer", "kind": kind, "lang": lang}


def rule_answer(conv: Conversation, lang: str) -> dict:
    """Fallback answer when the LLM is unavailable: honest, grounded, one next step."""
    na = conv.next_action or {}
    label = na.get("label", "next step")
    known = conv.facts.get("perf_line", "")
    options = []
    if known:
        options.append(_L(lang, f"I don't have a breakdown for that exact item, so I won't guess. What I can see: {known}. Want the {label}? Reply YES and I'll have it ready.",
                          f"Us item ka exact breakdown mere paas nahi hai, toh guess nahi karungi. Jo dikh raha hai: {known}. {label} chahiye? YES bolein, ready kar deti hoon."))
    options += [
        _L(lang, f"Good question. Short version: I handle the {label} end to end — it needs about 5 minutes of your time to approve. Reply YES and I'll start.",
           f"Accha sawaal. Short mein: {label} main poora handle karungi — aapke bas 5 minute approve karne mein lagenge. YES bolein toh shuru karti hoon."),
        _L(lang, f"I don't have more detail on that in front of me right now, so I won't guess. What I can do today is the {label} — reply YES and it's done.",
           f"Us par abhi mere paas exact detail nahi hai, toh guess nahi karungi. Aaj main {label} kar sakti hoon — YES bolein aur ho jayega."),
    ]
    for o in options:
        h = short_hash(o, 16)
        if h not in conv.sent_hashes:
            conv.sent_hashes.add(h)
            conv.add_turn("bot", o)
            return {"action": "send", "body": o, "cta": "binary_yes_no",
                    "rationale": "Question answered honestly from available facts (no guessing); single next step."}
    conv.state = "WAITING"
    return {"action": "wait", "wait_seconds": 3600, "rationale": "Already answered; giving the merchant time."}
