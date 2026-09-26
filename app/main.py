"""Vera message engine — FastAPI service for the magicpin AI Challenge.

Endpoints: POST /v1/context, POST /v1/tick, POST /v1/reply, GET /v1/healthz, GET /v1/metadata
(+ optional POST /v1/teardown).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .converse import default_next_action, detect_lang, handle_reply, rule_answer
from .decide import Plan, plan_trigger
from .facts import Facts
from .llm import get_llm
from .playbook import Skip, curious
from .store import SCOPES, STORE, Conversation
from .util import iso, parse_dt, short_hash, stable_hash, utcnow
from .validator import validate

VERSION = "1.0.0"
PROMPT_VERSION = "p1"
BOOT_ID = uuid.uuid4().hex[:8]
START = time.time()
TICK_DEADLINE = float(os.getenv("TICK_DEADLINE", "10"))
REPLY_DEADLINE = float(os.getenv("REPLY_DEADLINE", "8"))

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vera")

app = FastAPI(title="Vera message engine", version=VERSION)


@app.on_event("startup")
async def _boot():
    llm = get_llm()
    log.warning("=== BOOT boot_id=%s — in-memory state is EMPTY (llm=%s) ===", BOOT_ID,
                llm.model_label if llm.active else "off")


@app.exception_handler(RequestValidationError)
async def _bad_request(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_request",
                                                  "details": str(exc.errors()[:3])})


# ------------------------------------------------------------------ models (lenient)
class CtxBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str | None = None


class TickBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    now: str | None = None
    available_triggers: list[str] = Field(default_factory=list)


class ReplyBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    conversation_id: str
    merchant_id: str | None = None
    customer_id: str | None = None
    from_role: str = "merchant"
    message: str = ""
    received_at: str | None = None
    turn_number: int | None = None


# ------------------------------------------------------------------ basic endpoints
@app.get("/v1/healthz")
async def healthz():
    return {"status": "ok", "uptime_seconds": int(time.time() - START), "contexts_loaded": STORE.counts()}


@app.get("/v1/metadata")
async def metadata():
    llm = get_llm()
    members = [m.strip() for m in os.getenv("TEAM_MEMBERS", "Shrey Gupta").split(",") if m.strip()]
    return {
        "team_name": os.getenv("TEAM_NAME", "Shrey Gupta"),
        "team_members": members,
        "model": (llm.model_label if llm.active else "none") + " (polish only) + deterministic template engine",
        "approach": ("Rules decide, templates write, LLM polishes: claim-verified trigger routing, per-kind playbook "
                     "with category voice, fact-checked validator, rule-first reply state machine"),
        "contact_email": os.getenv("CONTACT_EMAIL", "shreygupta0924@gmail.com"),
        "version": VERSION,
        "submitted_at": os.getenv("SUBMITTED_AT", "2026-09-27T05:30:00Z"),
    }


@app.post("/v1/teardown")
async def teardown():
    STORE.reset()
    return {"ok": True}


@app.post("/v1/context")
async def push_context(body: CtxBody):
    if body.scope not in SCOPES:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_scope",
                                                      "details": f"scope must be one of {list(SCOPES)}"})
    if not body.context_id:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_context_id"})
    status, resp = STORE.put(body.scope, body.context_id, body.version, body.payload)
    if status == 200 and body.scope == "trigger" and get_llm().active:
        now = parse_dt(body.delivered_at) or utcnow()
        asyncio.create_task(_precompose(body.context_id, now))
    return JSONResponse(status_code=status, content=resp)


# ------------------------------------------------------------------ composition
def _cache_key(plan: Plan) -> str:
    d = plan.draft
    return stable_hash({"v": PROMPT_VERSION, "body": d.body, "aud": d.audience, "lang": d.language})


def _tone(f: Facts) -> str:
    v = f.voice
    return f"{v.get('tone', '')}, {v.get('register', '')}".strip(", ")


async def finalize(plan: Plan, deadline: float, serve: bool = True) -> tuple[str, str] | None:
    """Returns (body, source) — cached first-served output, LLM polish if allowed, else the draft.
    Background precompose (serve=False) only caches successful polishes, so a momentary lack of
    quota never locks in the plain template; whatever is actually served first is cached for good."""
    d, f = plan.draft, plan.facts
    if validate(d.body, f, cta=d.cta, audience=d.audience):
        # draft failed our own checks: fall back to the safe ask-the-merchant draft for merchants
        if d.audience != "merchant":
            return None
        alt = curious(f, d.kind, "Primary draft failed validation; safe fallback.")
        if validate(alt.body, f, cta=alt.cta):
            return None
        plan.draft = d = alt
    key = _cache_key(plan)
    hit = STORE.cache_get(key)
    if hit:
        return hit["body"], hit["source"] + "+cache"
    task = STORE.inflight.get(key)
    if task and not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=max(0.1, deadline - time.time() - 0.5))
        except Exception:
            pass
        hit = STORE.cache_get(key)
        if hit:
            return hit["body"], hit["source"] + "+cache"
    body, source = d.body, "template"
    llm = get_llm()
    if llm.active and deadline - time.time() > 3:
        STORE.inflight[key] = asyncio.current_task()
        try:
            polished = await llm.polish(d.body, _tone(f), d.language, d.audience, deadline)
            if polished and not validate(polished, f, cta=d.cta, audience=d.audience):
                body, source = polished, "llm"
                STORE.stats["llm_ok"] += 1
            else:
                STORE.stats["llm_fail"] += 1
        finally:
            STORE.inflight.pop(key, None)
    if source == "template":
        STORE.stats["template"] += 1
    if not serve and source != "llm":
        return body, source
    STORE.cache_put(key, {"body": body, "source": source})
    return STORE.cache_get(key)["body"], source


async def _precompose(trigger_id: str, now):
    try:
        plan = plan_trigger(STORE, trigger_id, now)
        if isinstance(plan, Plan):
            await finalize(plan, time.time() + 20, serve=False)
    except Exception as e:  # background work must never crash anything
        log.info("precompose %s failed: %s", trigger_id, e)


def _conv_id(plan: Plan) -> str:
    mid = (plan.merchant.get("merchant_id") or "m").split("_")
    short = "_".join(mid[:2]) if len(mid) > 1 else mid[0]
    kind = plan.draft.kind
    base = f"conv_{short}_{kind}_{short_hash(plan.trigger_id, 6)}"
    if plan.customer:
        base = f"conv_{short}_{(plan.customer.get('customer_id') or 'c').split('_')[1] if '_' in (plan.customer.get('customer_id') or '') else 'c'}_{kind}_{short_hash(plan.trigger_id, 6)}"
    cid, i = base, 2
    while cid in STORE.conversations:
        cid, i = f"{base}_{i}", i + 1
    return cid


@app.post("/v1/tick")
async def tick(body: TickBody):
    deadline = time.time() + TICK_DEADLINE
    now = parse_dt(body.now) or utcnow()
    STORE.stats["ticks"] += 1
    plans: list[Plan] = []
    for tid in dict.fromkeys(body.available_triggers or []):
        try:
            r = plan_trigger(STORE, tid, now)
        except Exception as e:
            log.info("plan %s crashed: %s", tid, e)
            continue
        if isinstance(r, Skip):
            log.info("skip %s: %s", tid, r.reason)
            continue
        plans.append(r)
    plans.sort(key=lambda p: (-p.score, p.trigger_id))
    chosen: list[Plan] = []
    used = set()
    for p in plans:
        mid = p.merchant.get("merchant_id")
        slot = ("merchant", mid) if p.draft.audience == "merchant" else ("customer", (p.customer or {}).get("customer_id"))
        if slot in used:
            continue  # one new conversation per merchant (and per customer) per tick; others wait for the next tick
        urg = p.trigger.get("urgency") or 1
        busy = any(c.merchant_id == mid and c.audience == "merchant" and c.state in ("ENGAGED", "ACTION")
                   for c in STORE.conversations.values())
        if p.draft.audience == "merchant" and busy and isinstance(urg, (int, float)) and urg <= 3:
            continue  # merchant is mid-conversation; don't pile on a low-urgency pitch
        used.add(slot)
        chosen.append(p)
        if len(chosen) >= 20:
            break
    results = await asyncio.gather(*(finalize(p, deadline) for p in chosen), return_exceptions=True)
    actions = []
    for p, res in zip(chosen, results):
        if not res or isinstance(res, Exception):
            continue
        text, source = res
        d = p.draft
        cid = _conv_id(p)
        conv = Conversation(conversation_id=cid, merchant_id=p.merchant.get("merchant_id"),
                            customer_id=(p.customer or {}).get("customer_id"), trigger_id=p.trigger_id,
                            kind=d.kind, audience=d.audience, send_as=d.send_as, language=d.language,
                            next_action=d.next_action, created_at=iso(now),
                            facts={"owner": p.facts.salutation, "biz": p.facts.biz, "summary": _facts_summary(p.facts),
                                   "perf_line": _perf_line_text(p.facts)})
        conv.sent_hashes.add(short_hash(text, 16))
        conv.add_turn("bot", text)
        STORE.conversations[cid] = conv
        STORE.sent_keys.add(p.trigger.get("suppression_key") or f"trg:{p.trigger_id}")
        STORE.mstate(conv.merchant_id).last_proactive = iso(now)
        actions.append({
            "conversation_id": cid,
            "merchant_id": conv.merchant_id,
            "customer_id": conv.customer_id,
            "send_as": d.send_as,
            "trigger_id": p.trigger_id,
            "template_name": d.template_name,
            "template_params": [str(x) for x in d.template_params][:5],
            "body": text,
            "cta": d.cta,
            "suppression_key": p.trigger.get("suppression_key") or f"trg:{p.trigger_id}",
            "rationale": d.rationale + (f" Pivot: {d.pivot}." if d.pivot else "") + f" [{source}]",
        })
    return {"actions": actions}


def _perf_line_text(f: Facts) -> str:
    from .playbook import _perf_line
    return _perf_line(f)


def _facts_summary(f: Facts) -> str:
    p = f.perf
    parts = [f"Business: {f.biz} ({f.slug}) in {f.locality}, {f.city}. Owner: {f.salutation}."]
    if p:
        parts.append(f"Last {p.get('window_days', 30)} days: views {p.get('views')}, calls {p.get('calls')}, ctr {p.get('ctr')}.")
    if f.active_offers():
        parts.append("Active offers: " + "; ".join(f.active_offers()) + ".")
    sub = f.merchant.get("subscription") or {}
    if sub:
        parts.append(f"Plan: {sub.get('plan')} ({sub.get('status')}).")
    return " ".join(parts)


# ------------------------------------------------------------------ replies
@app.post("/v1/reply")
async def reply(body: ReplyBody):
    deadline = time.time() + REPLY_DEADLINE
    STORE.stats["replies"] += 1
    conv = STORE.conversations.get(body.conversation_id)
    if conv is None:  # a conversation we didn't open (or state was lost): build one from what we know
        na, meta = default_next_action(STORE, body.merchant_id)
        audience = "customer" if (body.customer_id or body.from_role == "customer") else "merchant"
        conv = Conversation(conversation_id=body.conversation_id, merchant_id=body.merchant_id,
                            customer_id=body.customer_id, trigger_id=None, kind=None, audience=audience,
                            send_as="merchant_on_behalf" if audience == "customer" else "vera",
                            language=detect_lang(body.message), next_action=na,
                            facts={"owner": meta.get("owner", ""), "biz": meta.get("name", "")})
        STORE.conversations[body.conversation_id] = conv
    try:
        res = handle_reply(STORE, conv, body.message, body.from_role)
    except Exception as e:
        log.info("reply handler error: %s", e)
        return {"action": "wait", "wait_seconds": 1800, "rationale": "Could not interpret the reply safely; pausing."}
    if res.get("action") == "_needs_answer":
        lang = res.get("lang", "en")
        llm = get_llm()
        text = None
        if llm.active:
            facts_text = conv.facts.get("summary", "") + (f" Pending action: {(conv.next_action or {}).get('label')}." if conv.next_action else "")
            text = await llm.answer(body.message, facts_text, lang, deadline)
            if text:
                ctx_blob = json.dumps([STORE.get("merchant", conv.merchant_id) or {}], ensure_ascii=False)
                merchant = STORE.get("merchant", conv.merchant_id) or {}
                f = Facts(category=STORE.category_for(merchant) or {}, merchant=merchant, trigger={},
                          customer=STORE.get("customer", conv.customer_id), now=utcnow())
                if validate(text, f, audience=conv.audience, prior_bodies={t["text"] for t in conv.turns if t["role"] == "bot"},
                            context_blob=ctx_blob):
                    text = None
        if text:
            conv.sent_hashes.add(short_hash(text, 16))
            conv.add_turn("bot", text)
            return {"action": "send", "body": text, "cta": "open_ended",
                    "rationale": "Answered the merchant's question from known facts only; one next step."}
        return rule_answer(conv, lang)
    return res
