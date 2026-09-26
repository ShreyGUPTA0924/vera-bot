"""Check your LLM keys: lists your Gemini models, then tries a real call on several models and
request shapes, and on Groq. Tells you what to put in .env.

    python eval/llm_check.py
    python eval/llm_check.py gemini-3.5-flash gemini-3.1-flash-lite     # test specific models
"""
import os
import sys
import time

import httpx

from _common import load_env

load_env()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.llm import Gemini  # noqa: E402

DEFAULT_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-flash-lite-latest"]
PROMPT = 'Rewrite for WhatsApp, keep all numbers, return JSON {"body": "..."}: "Dr. Meera, calls down 30% this week. Reply YES."'


def try_gemini(key, model):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    g = Gemini(key, model)
    for i, v in enumerate(Gemini.VARIANTS):
        t = time.time()
        try:
            r = httpx.post(url, json=g._body("You polish messages.", PROMPT, v),
                           headers={"x-goog-api-key": key}, timeout=30)
        except Exception as e:
            print(f"   {model:<26} variant {i}: {type(e).__name__}")
            return None
        dt = time.time() - t
        if r.status_code == 200:
            try:
                parts = r.json()["candidates"][0]["content"]["parts"]
                txt = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            except Exception:
                txt = r.text[:80]
            print(f"   {model:<26} variant {i}: OK in {dt:.1f}s -> {txt.strip()[:90]}")
            return i, dt
        if r.status_code != 400:
            msg = r.json().get("error", {}).get("message", "")[:70] if r.headers.get("content-type", "").startswith("application/json") else ""
            print(f"   {model:<26} variant {i}: HTTP {r.status_code} {msg}")
            return None
    print(f"   {model:<26} no request shape accepted (400 on all)")
    return None


def main():
    key = os.getenv("GEMINI_API_KEY")
    results = {}
    if key:
        models = sys.argv[1:] or DEFAULT_MODELS
        print("Gemini:")
        for m in models:
            res = try_gemini(key, m)
            if res:
                results[m] = res
    else:
        print("GEMINI_API_KEY not set — skipping Gemini")
    gk = os.getenv("GROQ_API_KEY")
    groq_ok = False
    if gk:
        model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
        body = {"model": model, "temperature": 0, "max_tokens": 300, "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": PROMPT}]}
        if "gpt-oss" in model:
            body["reasoning_effort"] = "low"
        t = time.time()
        r = httpx.post("https://api.groq.com/openai/v1/chat/completions", json=body,
                       headers={"Authorization": f"Bearer {gk}"}, timeout=30)
        dt = time.time() - t
        groq_ok = r.status_code == 200
        out = r.json()["choices"][0]["message"]["content"][:90] if groq_ok else r.text[:120]
        print(f"\nGroq:\n   {model:<26} {'OK' if groq_ok else 'HTTP ' + str(r.status_code)} in {dt:.1f}s -> {out}")
    print("\nSuggested .env lines:")
    if results:
        best = min(results.items(), key=lambda kv: kv[1][1])
        print(f"   GEMINI_MODEL={best[0]}\n   GEMINI_VARIANT={best[1][0]}")
    if groq_ok and not results:
        print("   LLM_ORDER=groq,gemini      (Gemini isn't answering right now; Groq goes first)")
    elif groq_ok:
        print("   LLM_ORDER=gemini,groq")


if __name__ == "__main__":
    main()
