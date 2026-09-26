"""Harness emulator: a small version of the official 60-minute test, with an LLM playing the merchant.

- teardown, push the base dataset (5 categories, 50 merchants, 200 customers)
- 8 ticks, 5 simulated minutes apart; test-pair triggers arrive over the first ticks
- mid-run injections: new digest item (category v2), a performance update, a surprise customer + recall
- every action becomes a conversation: a persona replies (LLM for natural ones, scripted for auto-reply / hard no)
  for up to 5 turns, then an LLM judge scores the conversation's flow

    python eval/harness.py                       # local bot, LLM merchant + judge via your Groq key
    python eval/harness.py --url https://<your-app>.onrender.com --max-conv 12
"""
import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

from _common import ROOT, judge_call, load_env, load_pack

load_env()

PERSONAS = [
    ("engaged", "You are a busy but interested Indian small-business owner. Reply in 1-2 short English sentences. "
                "Sometimes ask one practical question, sometimes agree. If Vera offers to do the work, say yes by turn 2 or 3."),
    ("hinglish", "You are a friendly Indian shop owner who writes WhatsApp replies in Roman Hinglish (Hindi-English mix), "
                 "1-2 short sentences. Ask one doubt, then agree ('haan kar do', 'theek hai bhej do')."),
    ("auto_reply", None),
    ("curveball", "You are a distracted Indian merchant. First ask something unrelated (GST filing, a loan, or your "
                  "nephew's job), then if Vera redirects politely, ask what exactly she will do. Short replies."),
    ("hard_no", None),
    ("busy", "You are an Indian merchant in the middle of service. Reply that you're busy and will check later or tomorrow. "
             "One short sentence."),
]
AUTO = "Thank you for contacting us! Our team will get back to you shortly. 🙏"
FLOW_JUDGE = """You judge a WhatsApp conversation between Vera (an AI assistant for Indian merchants) and a merchant.
Score FLOW 0-10 (be strict): Did Vera understand each reply (intent: yes / question / busy / no / auto-reply / off-topic)?
Did she switch to action immediately when the merchant agreed (no extra qualifying questions)? Did she answer questions
honestly without inventing numbers? Did she avoid repeating herself? Did she stop or back off when she should?
Return JSON only: {"flow": <0-10>, "issue": "<main problem in one sentence, or 'none'>"}"""


def merchant_reply(persona_prompt: str, transcript: list[dict]) -> str:
    convo = "\n".join(f"{'Vera' if t['role'] == 'bot' else 'You'}: {t['text']}" for t in transcript)
    out = judge_call(persona_prompt + " Reply with the message text only.", f"Conversation so far:\n{convo}\n\nYour next reply:")
    txt = (out or "ok").strip().strip('"')
    return re.sub(r"^(You|Merchant):\s*", "", txt)[:300]


def run(args):
    url = args.url.rstrip("/")
    pack = load_pack()
    c = httpx.Client(timeout=40)
    c.post(f"{url}/v1/teardown")
    for scope in ("category", "merchant", "customer"):
        for cid, p in pack[scope].items():
            c.post(f"{url}/v1/context", json={"scope": scope, "context_id": cid, "version": 1, "payload": p})
    print("healthz after warmup:", c.get(f"{url}/v1/healthz").json()["contexts_loaded"])
    t0 = datetime(2026, 4, 26, 10, 0, tzinfo=timezone.utc)
    pairs = [p["trigger_id"] for p in pack["pairs"]]
    schedule = {i: pairs[i * 5:(i + 1) * 5] for i in range(6)}
    active, convs, lat, errors = [], [], [], 0
    for tick in range(8):
        now = t0 + timedelta(minutes=5 * tick)
        for tid in schedule.get(tick, []):
            c.post(f"{url}/v1/context", json={"scope": "trigger", "context_id": tid, "version": 1, "payload": pack["trigger"][tid]})
            active.append(tid)
        if tick == 2:  # injected research item
            cat = dict(pack["category"]["dentists"])
            cat["digest"] = [{"id": "d_HX1", "kind": "research", "title": "Silver diamine fluoride arrests 81% of early caries in a 1,200-child trial",
                              "source": "IJDR Nov 2026", "trial_n": 1200, "summary": "Twice-yearly SDF arrested 81% of early lesions.",
                              "actionable": "Offer SDF to pediatric patients who can't sit for fillings"}] + cat["digest"]
            c.post(f"{url}/v1/context", json={"scope": "category", "context_id": "dentists", "version": 2, "payload": cat})
            trig = {"id": "trg_HX_digest", "scope": "merchant", "kind": "research_digest", "merchant_id": "m_015_dr_priya_dentist_chandigarh",
                    "payload": {"top_item_id": "d_HX1"}, "urgency": 2, "suppression_key": "hx:1"}
            c.post(f"{url}/v1/context", json={"scope": "trigger", "context_id": trig["id"], "version": 1, "payload": trig})
            active.append(trig["id"])
        if tick == 4:  # performance update + surprise customer with a recall two minutes later
            m = dict(pack["merchant"]["m_007_powerhouse_gym_bangalore"])
            m["performance"] = dict(m["performance"], views=1320, delta_7d={"views_pct": -0.41, "calls_pct": -0.2})
            c.post(f"{url}/v1/context", json={"scope": "merchant", "context_id": m["merchant_id"], "version": 2, "payload": m})
            c.post(f"{url}/v1/context", json={"scope": "trigger", "context_id": "trg_HX_dip", "version": 1,
                                              "payload": {"id": "trg_HX_dip", "scope": "merchant", "kind": "perf_dip", "merchant_id": m["merchant_id"],
                                                          "payload": {"metric": "views", "delta_pct": -0.41}, "urgency": 4, "suppression_key": "hx:2"}})
            cust = {"customer_id": "c_HX1", "merchant_id": "m_001_drmeera_dentist_delhi",
                    "identity": {"name": "Neel", "phone_redacted": "<phone>", "language_pref": "hi-en mix"},
                    "relationship": {"first_visit": "2025-10-01", "last_visit": "2025-10-20", "visits_total": 2, "services_received": ["cleaning"]},
                    "state": "lapsed_soft", "preferences": {"preferred_slots": "weekday_evening", "channel": "whatsapp", "reminder_opt_in": True},
                    "consent": {"opted_in_at": "2025-10-01", "scope": ["recall_reminders"]}}
            c.post(f"{url}/v1/context", json={"scope": "customer", "context_id": "c_HX1", "version": 1, "payload": cust})
            c.post(f"{url}/v1/context", json={"scope": "trigger", "context_id": "trg_HX_recall", "version": 1,
                                              "payload": {"id": "trg_HX_recall", "scope": "customer", "kind": "recall_due",
                                                          "merchant_id": "m_001_drmeera_dentist_delhi", "customer_id": "c_HX1",
                                                          "payload": {"service_due": "6_month_cleaning", "last_service_date": "2025-10-20"},
                                                          "urgency": 3, "suppression_key": "hx:3"}})
            active += ["trg_HX_dip", "trg_HX_recall"]
        t = time.time()
        r = c.post(f"{url}/v1/tick", json={"now": now.isoformat().replace("+00:00", "Z"), "available_triggers": active})
        lat.append(time.time() - t)
        if r.status_code != 200:
            errors += 1
            continue
        acts = r.json().get("actions", [])
        print(f"tick {tick}: {len(active)} active triggers -> {len(acts)} actions ({lat[-1]:.1f}s)")
        for a in acts:
            if len(convs) >= args.max_conv:
                break
            convs.append({"action": a, "persona": PERSONAS[len(convs) % len(PERSONAS)][0], "turns": [{"role": "bot", "text": a["body"]}]})
    # conversations
    for conv in convs:
        a, pname = conv["action"], conv["persona"]
        pprompt = dict(PERSONAS)[pname]
        role = "customer" if a.get("customer_id") else "merchant"
        for turn in range(2, 7):
            if pname == "auto_reply":
                msg = AUTO
            elif pname == "hard_no":
                msg = "Not interested. Please don't message again." if turn == 2 else "?"
            else:
                msg = merchant_reply(pprompt, conv["turns"])
                time.sleep(args.pace)
            conv["turns"].append({"role": role, "text": msg})
            t = time.time()
            rr = c.post(f"{url}/v1/reply", json={"conversation_id": a["conversation_id"], "merchant_id": a["merchant_id"],
                                                 "customer_id": a.get("customer_id"), "from_role": role, "message": msg,
                                                 "turn_number": turn})
            lat.append(time.time() - t)
            if rr.status_code != 200:
                errors += 1
                break
            res = rr.json()
            if res["action"] == "send":
                conv["turns"].append({"role": "bot", "text": res["body"]})
            else:
                conv["turns"].append({"role": "bot", "text": f"[{res['action']}{' ' + str(res.get('wait_seconds')) + 's' if res['action'] == 'wait' else ''}]"})
                break
    # judge
    scores = []
    for conv in convs:
        txt = "\n".join(f"{'Vera' if t['role'] == 'bot' else 'Merchant'}: {t['text']}" for t in conv["turns"])
        out = judge_call(FLOW_JUDGE, txt) or "{}"
        m = re.search(r"\{.*\}", out, re.S)
        try:
            j = json.loads(m.group(0)) if m else {}
        except Exception:
            j = {}
        conv["flow"], conv["issue"] = j.get("flow"), j.get("issue")
        if isinstance(conv["flow"], (int, float)):
            scores.append(conv["flow"])
        print(f"\n--- {conv['persona']} | {conv['action']['trigger_id']} | flow {conv['flow']} | {conv['issue']}")
        for t in conv["turns"]:
            print(f"   {'VERA' if t['role'] == 'bot' else t['role'].upper():<8} {t['text'][:220].replace(chr(10), ' ')}")
        time.sleep(args.pace)
    bodies = [t["text"] for cv in convs for t in cv["turns"] if t["role"] == "bot"]
    repeats = sum(1 for cv in convs for i, t in enumerate(cv["turns"]) if t["role"] == "bot" and
                  any(u["role"] == "bot" and u["text"] == t["text"] and not t["text"].startswith("[") for u in cv["turns"][:i]))
    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    with open(out_dir / "harness.jsonl", "w", encoding="utf-8") as fh:
        for cv in convs:
            fh.write(json.dumps(cv, ensure_ascii=False) + "\n")
    print("\n================ SUMMARY")
    print(f"conversations: {len(convs)} | bot messages: {len(bodies)} | errors: {errors} | verbatim repeats: {repeats}")
    print(f"max latency: {max(lat):.1f}s | mean: {sum(lat) / len(lat):.2f}s")
    if scores:
        print(f"avg flow score: {sum(scores) / len(scores):.1f}/10 over {len(scores)} judged conversations")
    print("transcripts: results/harness.jsonl")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("BOT_URL", "http://localhost:8080"))
    ap.add_argument("--max-conv", type=int, default=14)
    ap.add_argument("--pace", type=float, default=float(os.getenv("PACE", "3")))
    run(ap.parse_args())
