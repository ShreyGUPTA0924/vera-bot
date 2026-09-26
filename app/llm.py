"""LLM polish layer (free tier): Gemini primary, Groq failover, token-bucket rate limits.

The LLM never decides anything. It only rewrites a validated template draft for fluency, and its
output must pass the same validator AND keep the draft's facts; otherwise the draft is used.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import deque

import httpx

from .util import numbers_in

SYSTEM = """You polish short WhatsApp messages for Vera, magicpin's assistant for Indian local businesses.
Rewrite the DRAFT so it reads like a sharp, warm human operator wrote it.
Hard rules:
- Keep EVERY fact exactly: every number, price, date, name, source and offer must stay, unchanged.
- Never add facts, numbers, names, dates, prices, claims, urgency or promises that are not in the DRAFT.
- Keep the greeting/name at the start and keep exactly ONE call to action as the LAST sentence, with the same reply keyword (YES / CONFIRM / 1 or 2).
- No links, no hashtags, no hype words (guaranteed, miracle, best in city, amazing), no internal terms.
- Match the language instruction. Hinglish means natural Roman-script Hindi-English mix.
- Similar length or shorter than the DRAFT. Keep line breaks of any draft list.
Return JSON only: {"body": "<message>"}"""

LANG_NOTE = {
    "hinglish": "Light Hinglish (mostly English, natural Hindi phrases; Roman script).",
    "hindi_roman": "Mostly Hindi in Roman script, simple and respectful.",
    "en": "Plain, friendly Indian English.",
}


class Limiter:
    def __init__(self, rpm: int, tpm: int, rpd: int):
        self.rpm, self.tpm, self.rpd = max(1, rpm), max(500, tpm), max(1, rpd)
        self.calls: deque = deque()
        self.tokens: deque = deque()
        self.day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 3600))  # PT day
        self.day_count = 0
        self.cool_until = 0.0

    def _trim(self, now: float):
        while self.calls and now - self.calls[0] > 60:
            self.calls.popleft()
        while self.tokens and now - self.tokens[0][0] > 60:
            self.tokens.popleft()
        d = time.strftime("%Y-%m-%d", time.gmtime(now - 7 * 3600))
        if d != self.day:
            self.day, self.day_count = d, 0

    def try_acquire(self, est_tokens: int) -> bool:
        now = time.time()
        if now < self.cool_until:
            return False
        self._trim(now)
        if len(self.calls) >= self.rpm or self.day_count >= self.rpd:
            return False
        if sum(t for _, t in self.tokens) + est_tokens > self.tpm:
            return False
        self.calls.append(now)
        self.tokens.append((now, est_tokens))
        self.day_count += 1
        return True

    def cooldown(self, sec: float = 60):
        self.cool_until = time.time() + sec


class Provider:
    name = "base"

    def __init__(self):
        self.sem = asyncio.Semaphore(2)

    async def complete(self, client: httpx.AsyncClient, system: str, user: str, timeout: float) -> str:
        raise NotImplementedError


class Gemini(Provider):
    name = "gemini"

    def __init__(self, key: str, model: str):
        super().__init__()
        self.key, self.model = key, model
        self.sem = asyncio.Semaphore(3)
        self.thinking_mode = os.getenv("GEMINI_THINKING", "auto")  # auto | off | none

    async def complete(self, client, system, user, timeout):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        cfg = {"temperature": 0, "maxOutputTokens": 500, "responseMimeType": "application/json"}
        if self.thinking_mode in ("auto", "off"):
            cfg["thinkingConfig"] = {"thinkingBudget": 0}
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": cfg}
        r = await client.post(url, json=body, headers={"x-goog-api-key": self.key}, timeout=timeout)
        if r.status_code == 400 and "thinking" in r.text.lower() and self.thinking_mode == "auto":
            self.thinking_mode = "none"  # model doesn't accept a thinking budget; remember and retry once
            cfg.pop("thinkingConfig", None)
            r = await client.post(url, json=body, headers={"x-goog-api-key": self.key}, timeout=timeout)
        if r.status_code != 200:
            raise httpx.HTTPStatusError(f"gemini {r.status_code}", request=r.request, response=r)
        data = r.json()
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        return "".join(p.get("text", "") for p in parts if not p.get("thought"))


class Groq(Provider):
    name = "groq"

    def __init__(self, key: str, model: str):
        super().__init__()
        self.key, self.model = key, model

    async def complete(self, client, system, user, timeout):
        body = {"model": self.model, "temperature": 0, "seed": 7, "max_tokens": 700,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if "gpt-oss" in self.model:
            body["reasoning_effort"] = "low"
        r = await client.post("https://api.groq.com/openai/v1/chat/completions", json=body,
                              headers={"Authorization": f"Bearer {self.key}"}, timeout=timeout)
        if r.status_code != 200:
            raise httpx.HTTPStatusError(f"groq {r.status_code}", request=r.request, response=r)
        return r.json()["choices"][0]["message"]["content"] or ""


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


class LLM:
    def __init__(self):
        self.enabled = os.getenv("LLM_ENABLED", "1") == "1"
        self.timeout = float(os.getenv("LLM_CALL_TIMEOUT", "6"))
        self.providers: list[tuple[Provider, Limiter]] = []
        if os.getenv("GEMINI_API_KEY"):
            self.providers.append((Gemini(os.environ["GEMINI_API_KEY"], os.getenv("GEMINI_MODEL", "gemini-2.5-flash")),
                                   Limiter(_env_int("GEMINI_RPM", 8), _env_int("GEMINI_TPM", 200000), _env_int("GEMINI_RPD", 400))))
        if os.getenv("GROQ_API_KEY"):
            self.providers.append((Groq(os.environ["GROQ_API_KEY"], os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")),
                                   Limiter(_env_int("GROQ_RPM", 24), _env_int("GROQ_TPM", 6400), _env_int("GROQ_RPD", 800))))
        self._client: httpx.AsyncClient | None = None
        self.last_error: str | None = None
        self.model_label = "+".join(f"{p.name}:{getattr(p, 'model', '')}" for p, _ in self.providers) or "none"

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.providers)

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

    async def json_call(self, system: str, user: str, deadline: float) -> dict | None:
        """Try providers in order while budget + time allow. Returns parsed JSON or None."""
        if not self.active:
            return None
        est = (len(system) + len(user)) // 3 + 400
        for prov, lim in self.providers:
            remaining = deadline - time.time()
            if remaining < 2.5:
                return None
            if not lim.try_acquire(est):
                continue
            try:
                async with prov.sem:
                    txt = await asyncio.wait_for(prov.complete(self.client(), system, user, min(self.timeout, remaining - 0.5)),
                                                 timeout=min(self.timeout, remaining - 0.3))
                m = re.search(r"\{.*\}", txt or "", re.S)
                if m:
                    return json.loads(m.group(0))
                self.last_error = f"{prov.name}: no json"
            except httpx.HTTPStatusError as e:
                code = e.response.status_code if e.response is not None else 0
                self.last_error = f"{prov.name}: http {code}"
                if code in (429, 503):
                    lim.cooldown(60)
            except Exception as e:  # timeout, network, parse
                self.last_error = f"{prov.name}: {type(e).__name__}"
                lim.cooldown(20)
        return None

    async def polish(self, draft_body: str, tone: str, lang: str, audience: str, deadline: float) -> str | None:
        user = json.dumps({"DRAFT": draft_body, "tone": tone, "audience": audience,
                           "language": LANG_NOTE.get(lang, LANG_NOTE["en"])}, ensure_ascii=False)
        out = await self.json_call(SYSTEM, user, deadline)
        body = (out or {}).get("body") if isinstance(out, dict) else None
        if not isinstance(body, str) or not body.strip():
            return None
        body = body.strip()
        # must keep the draft's facts: every number in the draft must survive
        if not numbers_in(draft_body) <= numbers_in(body):
            return None
        if len(body) > len(draft_body) * 1.25 + 60:
            return None
        return body

    async def answer(self, question: str, facts_text: str, lang: str, deadline: float) -> str | None:
        system = ("You are Vera, magicpin's WhatsApp assistant for Indian local businesses. Answer the merchant's "
                  "question in 1-3 short sentences using ONLY the FACTS. If the facts don't contain the answer, say "
                  "honestly you don't have that detail — never guess numbers. End with one low-effort next step. "
                  "No links. Return JSON only: {\"body\": \"...\"}")
        user = json.dumps({"QUESTION": question, "FACTS": facts_text, "language": LANG_NOTE.get(lang, LANG_NOTE["en"])},
                          ensure_ascii=False)
        out = await self.json_call(system, user, deadline)
        body = (out or {}).get("body") if isinstance(out, dict) else None
        return body.strip() if isinstance(body, str) and body.strip() else None


LLM_CLIENT: LLM | None = None


def get_llm() -> LLM:
    global LLM_CLIENT
    if LLM_CLIENT is None:
        LLM_CLIENT = LLM()
    return LLM_CLIENT
