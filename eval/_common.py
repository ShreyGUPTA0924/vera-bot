"""Shared helpers for eval scripts: .env loading, starter-pack loading, tiny sync LLM client."""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines), without overriding real env vars."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def pack_dir() -> Path:
    """Expanded dataset dir. Run the generator first if it doesn't exist."""
    cand = [ROOT / "starter" / "expanded", ROOT / "expanded"]
    for c in cand:
        if (c / "test_pairs.json").exists():
            return c
    sys.exit("Expanded dataset not found. Unzip the starter pack into ./starter and run:\n"
             "  python starter/dataset/generate_dataset.py --seed-dir starter/dataset --out starter/expanded")


def load_pack():
    d = pack_dir()
    def load(sub, key):
        out = {}
        for f in sorted(glob.glob(str(d / sub / "*.json"))):
            obj = json.load(open(f, encoding="utf-8"))
            out[obj[key]] = obj
        return out
    return {
        "category": load("categories", "slug"),
        "merchant": load("merchants", "merchant_id"),
        "customer": load("customers", "customer_id"),
        "trigger": load("triggers", "id"),
        "pairs": json.load(open(d / "test_pairs.json"))["pairs"],
    }


def judge_call(system: str, prompt: str) -> str | None:
    """Sync call for local judging. Prefers JUDGE_* settings, else GROQ, else GEMINI (dev keys)."""
    import httpx
    prov = os.getenv("JUDGE_PROVIDER") or ("groq" if os.getenv("GROQ_API_KEY") else "gemini")
    try:
        if prov == "groq":
            jm = os.getenv("JUDGE_MODEL", "openai/gpt-oss-120b")
            body = {"model": jm, "temperature": 0, "max_tokens": 1200,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
            if "gpt-oss" in jm:
                body["reasoning_effort"] = "low"
            r = httpx.post("https://api.groq.com/openai/v1/chat/completions", timeout=60,
                           headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"}, json=body)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        model = os.getenv("JUDGE_MODEL", os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"))
        r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", timeout=60,
                       headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                       json={"systemInstruction": {"parts": [{"text": system}]},
                             "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                             "generationConfig": {"temperature": 0}})
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts if not p.get("thought"))
    except Exception as e:
        print(f"   (judge call failed: {e})")
        return None
