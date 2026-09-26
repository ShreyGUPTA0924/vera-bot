"""Check your LLM keys: lists the Gemini models your key can use and tests one real call per provider.

    python eval/llm_check.py
"""
import os
import time

import httpx

from _common import load_env

load_env()


def check_gemini():
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY not set — skipping Gemini")
        return
    r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                  headers={"x-goog-api-key": key}, timeout=30)
    if r.status_code != 200:
        print("Gemini list failed:", r.status_code, r.text[:200])
        return
    names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    flash = [n for n in names if "flash" in n]
    print("Gemini models with generateContent (flash only):")
    for n in flash:
        print("   ", n)
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    print(f"\nTesting GEMINI_MODEL={model} ...")
    t = time.time()
    body = {"contents": [{"role": "user", "parts": [{"text": 'Return JSON {"ok": true}'}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                   headers={"x-goog-api-key": key}, json=body, timeout=30)
    if r.status_code == 400 and "thinking" in r.text.lower():
        print("   (model rejects thinkingBudget=0 — the bot handles this automatically; retrying without it)")
        body["generationConfig"].pop("thinkingConfig")
        r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                       headers={"x-goog-api-key": key}, json=body, timeout=30)
    print(f"   status {r.status_code} in {time.time() - t:.1f}s:", r.text[:160].replace("\n", " "))


def check_groq():
    key = os.getenv("GROQ_API_KEY")
    if not key:
        print("GROQ_API_KEY not set — skipping Groq")
        return
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
    print(f"\nTesting GROQ_MODEL={model} ...")
    t = time.time()
    body = {"model": model, "temperature": 0, "max_tokens": 200, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": 'Return JSON {"ok": true}'}]}
    if "gpt-oss" in model:
        body["reasoning_effort"] = "low"
    r = httpx.post("https://api.groq.com/openai/v1/chat/completions", json=body,
                   headers={"Authorization": f"Bearer {key}"}, timeout=30)
    print(f"   status {r.status_code} in {time.time() - t:.1f}s:", r.text[:160].replace("\n", " "))


if __name__ == "__main__":
    check_gemini()
    check_groq()
