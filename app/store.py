"""In-memory state. One process, one worker: this module is the single source of truth.

Render's free tier has an ephemeral disk, so nothing is persisted; a restart = empty state
(logged loudly at boot in main.py).
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from .util import iso, utcnow

SCOPES = ("category", "merchant", "customer", "trigger")

MAX_TURNS_KEPT = 10
MAX_FINGERPRINTS = 20
CACHE_MAX = 5000


@dataclass
class Conversation:
    conversation_id: str
    merchant_id: str | None
    customer_id: str | None
    trigger_id: str | None
    kind: str | None
    audience: str  # "merchant" | "customer"
    send_as: str
    language: str = "en"
    state: str = "OPEN"  # OPEN, ENGAGED, ACTION, WAITING, CLOSING, ENDED
    turns: list[dict] = field(default_factory=list)  # [{role, text}]
    sent_hashes: set[str] = field(default_factory=set)
    next_action: dict | None = None
    facts: dict = field(default_factory=dict)  # small snapshot for replies
    unanswered: int = 0
    offtopic: int = 0
    created_at: str = ""

    def add_turn(self, role: str, text: str):
        self.turns.append({"role": role, "text": text})
        if len(self.turns) > MAX_TURNS_KEPT:
            self.turns = self.turns[-MAX_TURNS_KEPT:]


@dataclass
class MerchantState:
    opted_out: bool = False
    hostile: bool = False
    autoreply_fps: "OrderedDict[str, int]" = field(default_factory=OrderedDict)
    autoreply_hits: int = 0
    last_proactive: str | None = None  # simulated ISO time


class Store:
    def __init__(self):
        self.reset()

    def reset(self):
        self.ctx: dict[tuple[str, str], dict[str, Any]] = {}
        self.conversations: dict[str, Conversation] = {}
        self.sent_keys: set[str] = set()
        self.merchants_state: dict[str, MerchantState] = {}
        self.digest_seen: dict[str, int] = {}  # "slug:item_id" -> category version first seen
        self.cache: "OrderedDict[str, dict]" = OrderedDict()
        self.inflight: dict[str, asyncio.Task] = {}
        self.stats = {"llm_ok": 0, "llm_fail": 0, "template": 0, "ticks": 0, "replies": 0}

    # ---------- contexts ----------
    def put(self, scope: str, context_id: str, version: int, payload: dict) -> tuple[int, dict]:
        key = (scope, context_id)
        cur = self.ctx.get(key)
        if cur is not None and cur["version"] >= version:
            return 409, {"accepted": False, "reason": "stale_version", "current_version": cur["version"]}
        stored_at = iso(utcnow())
        self.ctx[key] = {"version": version, "payload": payload, "stored_at": stored_at}
        if scope == "category":
            slug = payload.get("slug") or context_id
            for item in payload.get("digest") or []:
                if isinstance(item, dict) and item.get("id"):
                    self.digest_seen.setdefault(f"{slug}:{item['id']}", version)
        return 200, {"accepted": True, "ack_id": f"ack_{context_id}_v{version}", "stored_at": stored_at}

    def get(self, scope: str, context_id: str | None) -> dict | None:
        if not context_id:
            return None
        rec = self.ctx.get((scope, context_id))
        return rec["payload"] if rec else None

    def version(self, scope: str, context_id: str) -> int | None:
        rec = self.ctx.get((scope, context_id))
        return rec["version"] if rec else None

    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in SCOPES}
        for (scope, _cid) in self.ctx:
            out[scope] = out.get(scope, 0) + 1
        return out

    def category_for(self, merchant: dict | None) -> dict | None:
        if not merchant:
            return None
        slug = merchant.get("category_slug")
        cat = self.get("category", slug)
        if cat is None and slug:  # tolerate a category pushed under a different id
            for (scope, _cid), rec in self.ctx.items():
                if scope == "category" and rec["payload"].get("slug") == slug:
                    return rec["payload"]
        return cat

    # ---------- merchant state ----------
    def mstate(self, merchant_id: str | None) -> MerchantState:
        mid = merchant_id or "_unknown"
        if mid not in self.merchants_state:
            self.merchants_state[mid] = MerchantState()
        return self.merchants_state[mid]

    def remember_fingerprint(self, merchant_id: str | None, fp: str) -> int:
        st = self.mstate(merchant_id)
        st.autoreply_fps[fp] = st.autoreply_fps.get(fp, 0) + 1
        st.autoreply_fps.move_to_end(fp)
        while len(st.autoreply_fps) > MAX_FINGERPRINTS:
            st.autoreply_fps.popitem(last=False)
        return st.autoreply_fps[fp]

    # ---------- cache ----------
    def cache_get(self, key: str) -> dict | None:
        val = self.cache.get(key)
        if val is not None:
            self.cache.move_to_end(key)
        return val

    def cache_put(self, key: str, value: dict):
        if key in self.cache:  # first served output wins (determinism)
            return
        self.cache[key] = value
        while len(self.cache) > CACHE_MAX:
            self.cache.popitem(last=False)


STORE = Store()
