"""Validator: the gate every outbound body passes (template, LLM polish, or reply)."""
from __future__ import annotations

import json
import re

from .facts import GLOBAL_TABOO, JARGON, Facts
from .util import numbers_in

URL_RE = re.compile(r"(https?://\S+|www\.\S+|\b[\w-]+\.(?:com|in|io|org|net|co)\b(?:/\S*)?)", re.I)
SNAKE_RE = re.compile(r"\b[a-z]+_[a-z_]+\b")
QUALIFYING = ["would you", "do you", "can you tell", "what if", "how about"]
ACTIONING = ["done", "sending", "draft", "confirm", "proceed", "next", "here"]


def _word_hit(text: str, phrase: str) -> bool:
    return re.search(r"(?<![\w])" + re.escape(phrase) + r"(?![\w])", text, re.I) is not None


def validate(body: str, facts: Facts | None, *, cta: str = "open_ended", audience: str = "merchant",
             prior_bodies: set[str] | None = None, commit_mode: bool = False,
             extra_numbers: set[str] | None = None, context_blob: str = "") -> list[str]:
    v: list[str] = []
    text = (body or "").strip()
    if not text:
        return ["empty body"]
    if len(text) > 1100:
        v.append("too long")
    # numbers must come from context / derived facts
    if facts is not None:
        allowed = facts.allowed_numbers() | (extra_numbers or set())
        bad = sorted(n for n in numbers_in(text) if n not in allowed)
        if bad:
            v.append(f"unverified numbers: {bad[:5]}")
        blob = context_blob or json.dumps([facts.category, facts.merchant, facts.trigger, facts.customer or {}],
                                          ensure_ascii=False)
        taboos = set(facts.taboos) | set(GLOBAL_TABOO)
    else:
        blob = context_blob
        taboos = set(GLOBAL_TABOO)
    for u in URL_RE.findall(text):
        if u not in blob:
            v.append(f"url not in context: {u}")
    for t in taboos:
        if t and _word_hit(text, t):
            v.append(f"taboo: {t}")
    for j in JARGON:
        if _word_hit(text, j):
            v.append(f"jargon: {j}")
    snake = [s for s in SNAKE_RE.findall(text) if s not in blob or "_" in s]
    if snake:
        v.append(f"snake_case: {snake[:3]}")
    # CTA shape: at most one explicit reply instruction; a question or keyword at the end for action CTAs
    replies = len(re.findall(r"\breply\b", text, re.I))
    if replies > 2:
        v.append("multiple CTAs")
    if cta == "none" and text.rstrip().endswith("?"):
        v.append("cta none but ends with a question")
    if audience == "customer" and re.search(r"\bvera\b", text, re.I):
        v.append("customer message mentions Vera")
    if prior_bodies and text in prior_bodies:
        v.append("verbatim repeat")
    if commit_mode:
        low = text.lower()
        if any(q in low for q in QUALIFYING):
            v.append("commit reply contains a qualifying question")
        if not any(a in low for a in ACTIONING):
            v.append("commit reply lacks an action word")
    return v
