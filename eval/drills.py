"""Drills: replay scenarios, adaptive injection, latency and determinism.

    python eval/drills.py                 # all drills against http://localhost:8080
    python eval/drills.py --url https://<your-app>.onrender.com replay
"""
import argparse
import os
import time

import httpx

from _common import load_env, load_pack

load_env()
QUAL = ["would you", "do you", "can you tell", "what if", "how about"]
ACT = ["done", "sending", "draft", "here", "confirm", "proceed", "next"]


def base(c, url, pack):
    c.post(f"{url}/v1/teardown")
    for scope in ("category", "merchant", "customer"):
        for cid, p in pack[scope].items():
            c.post(f"{url}/v1/context", json={"scope": scope, "context_id": cid, "version": 1, "payload": p})


def say(c, url, conv, mid, msg, turn, cid=None, role="merchant"):
    r = c.post(f"{url}/v1/reply", json={"conversation_id": conv, "merchant_id": mid, "customer_id": cid,
                                        "from_role": role, "message": msg, "turn_number": turn}).json()
    body = (r.get("body") or "").replace("\n", " ")
    print(f"   {role}: {msg!r}\n   bot → {r['action']}{' ' + str(r.get('wait_seconds')) + 's' if r['action'] == 'wait' else ''}: {body[:160]}")
    return r


def open_conv(c, url, pack, tid, now="2026-04-26T10:30:00Z"):
    c.post(f"{url}/v1/context", json={"scope": "trigger", "context_id": tid, "version": 1, "payload": pack["trigger"][tid]})
    acts = c.post(f"{url}/v1/tick", json={"now": now, "available_triggers": [tid]}).json()["actions"]
    return acts[0] if acts else None


def replay(c, url, pack):
    ok = True
    print("\n== 1. Auto-reply hell (Hinglish canned text, 4 turns)")
    a = open_conv(c, url, pack, "trg_022_cde_webinar_dentists")
    msg = "Aapki jaankari ke liye bahut-bahut shukriya. Main aapki yeh sabhi baatein team tak pahuncha deti hoon."
    acts = [say(c, url, a["conversation_id"], a["merchant_id"], msg, t)["action"] for t in range(2, 6)]
    ok &= acts[:3] == ["send", "wait", "end"]
    print("   PASS" if acts[:3] == ["send", "wait", "end"] else f"   FAIL {acts}")

    print("\n== 2. Intent transition after qualification")
    a = open_conv(c, url, pack, "trg_024_perf_spike_zen")
    say(c, url, a["conversation_id"], a["merchant_id"], "Hmm, how many people saw the kids yoga post?", 2)
    r = say(c, url, a["conversation_id"], a["merchant_id"], "Ok let's do it. What's next?", 3)
    low = (r.get("body") or "").lower()
    good = r["action"] == "send" and any(w in low for w in ACT) and not any(q in low for q in QUAL)
    ok &= good
    print("   PASS" if good else "   FAIL")

    print("\n== 3. Hostile then off-topic")
    a = open_conv(c, url, pack, "trg_009_winback_glamour")
    r1 = say(c, url, a["conversation_id"], a["merchant_id"], "Why are you bothering me, this is useless", 2)
    r2 = say(c, url, a["conversation_id"], a["merchant_id"], "can you also help me file my GST?", 3)
    good = r1["action"] in ("end", "send") and r2["action"] in ("end", "send") and "gst" not in (r2.get("body") or "").lower().replace("gst ", "x") or True
    print("   (check manually: apology/end, then polite decline/end)")

    print("\n== 4. Opt-out")
    a = open_conv(c, url, pack, "trg_025_dormancy_glamour")
    r = say(c, url, a["conversation_id"], a["merchant_id"], "Not interested. Stop messaging me.", 2) if a else {"action": "end"}
    ok &= r["action"] == "end"
    print("   PASS" if r["action"] == "end" else "   FAIL")

    print("\n== 5. Curveball question + later")
    a = open_conv(c, url, pack, "trg_023_competitor_opened_dentist")
    say(c, url, a["conversation_id"], a["merchant_id"], "Btw can you help with my GST filing this month?", 2)
    say(c, url, a["conversation_id"], a["merchant_id"], "ok I'm busy now, next week", 3)

    print("\n== 6. Customer picks a slot")
    c.post(f"{url}/v1/context", json={"scope": "trigger", "context_id": "trg_003_recall_due_priya", "version": 1,
                                      "payload": pack["trigger"]["trg_003_recall_due_priya"]})
    acts = c.post(f"{url}/v1/tick", json={"now": "2026-10-30T10:30:00Z", "available_triggers": ["trg_003_recall_due_priya"]}).json()["actions"]
    if acts:
        print("   opener:", acts[0]["body"][:140])
        say(c, url, acts[0]["conversation_id"], acts[0]["merchant_id"], "2", 2, acts[0]["customer_id"], role="customer")

    print("\n== 7. Unknown conversation id + commitment (simulator style)")
    r = say(c, url, "conv_never_seen", "m_003_studio11_salon_hyderabad", "haan kar do", 2)
    ok &= r["action"] == "send"
    return ok


def inject(c, url, pack):
    print("\n== Adaptive injection")
    cat = dict(pack["category"]["dentists"])
    new_item = {"id": "d_INJECT_1", "kind": "research", "title": "Silver diamine fluoride halts 81% of early caries in a 1,200-child trial",
                "source": "IJDR Nov 2026", "trial_n": 1200, "summary": "Twice-yearly SDF application arrested 81% of early lesions.",
                "actionable": "Consider SDF for pediatric patients who can't sit for fillings"}
    cat["digest"] = [new_item] + list(cat.get("digest") or [])
    print("   push category v2:", c.post(f"{url}/v1/context", json={"scope": "category", "context_id": "dentists", "version": 2, "payload": cat}).json())
    print("   re-push v2 (expect 409):", c.post(f"{url}/v1/context", json={"scope": "category", "context_id": "dentists", "version": 2, "payload": cat}).status_code)
    trig = {"id": "trg_INJ_digest", "scope": "merchant", "kind": "research_digest", "source": "external",
            "merchant_id": "m_015_dr_priya_dentist_chandigarh", "customer_id": None, "payload": {"placeholder": True},
            "urgency": 2, "suppression_key": "inj:1", "expires_at": "2026-12-01T00:00:00Z"}
    c.post(f"{url}/v1/context", json={"scope": "trigger", "context_id": trig["id"], "version": 1, "payload": trig})
    acts = c.post(f"{url}/v1/tick", json={"now": "2026-04-26T11:00:00Z", "available_triggers": [trig["id"]]}).json()["actions"]
    body = acts[0]["body"] if acts else ""
    print("   uses injected item:", "PASS" if "81%" in body or "SDF" in body or "diamine" in body else "FAIL", "|", body[:150])
    # surprise customer + recall 2 minutes later
    cust = {"customer_id": "c_INJ_1", "merchant_id": "m_001_drmeera_dentist_delhi",
            "identity": {"name": "Neel", "phone_redacted": "<phone>", "language_pref": "hi-en mix"},
            "relationship": {"first_visit": "2025-10-01", "last_visit": "2026-04-20", "visits_total": 2, "services_received": ["cleaning"]},
            "state": "active", "preferences": {"preferred_slots": "weekday_evening", "channel": "whatsapp", "reminder_opt_in": True},
            "consent": {"opted_in_at": "2025-10-01", "scope": ["recall_reminders"]}}
    c.post(f"{url}/v1/context", json={"scope": "customer", "context_id": "c_INJ_1", "version": 1, "payload": cust})
    trig2 = {"id": "trg_INJ_recall", "scope": "customer", "kind": "recall_due", "source": "internal",
             "merchant_id": "m_001_drmeera_dentist_delhi", "customer_id": "c_INJ_1",
             "payload": {"service_due": "6_month_cleaning", "last_service_date": "2026-04-20"}, "urgency": 3,
             "suppression_key": "inj:2", "expires_at": "2026-12-01T00:00:00Z"}
    c.post(f"{url}/v1/context", json={"scope": "trigger", "context_id": trig2["id"], "version": 1, "payload": trig2})
    acts = c.post(f"{url}/v1/tick", json={"now": "2026-10-22T11:02:00Z", "available_triggers": [trig2["id"]]}).json()["actions"]
    print("   surprise customer recall:", acts[0]["body"][:170] if acts else "NO ACTION")
    # perf update
    m = dict(pack["merchant"]["m_007_powerhouse_gym_bangalore"])
    m["performance"] = dict(m["performance"], delta_7d={"views_pct": -0.41, "calls_pct": -0.2})
    c.post(f"{url}/v1/context", json={"scope": "merchant", "context_id": m["merchant_id"], "version": 2, "payload": m})
    trig3 = {"id": "trg_INJ_dip", "scope": "merchant", "kind": "perf_dip", "source": "internal", "merchant_id": m["merchant_id"],
             "payload": {"placeholder": True}, "urgency": 3, "suppression_key": "inj:3"}
    c.post(f"{url}/v1/context", json={"scope": "trigger", "context_id": trig3["id"], "version": 1, "payload": trig3})
    acts = c.post(f"{url}/v1/tick", json={"now": "2026-04-26T11:10:00Z", "available_triggers": [trig3["id"]]}).json()["actions"]
    body = acts[0]["body"] if acts else ""
    print("   uses updated perf (-41%):", "PASS" if "41%" in body else "FAIL", "|", body[:150])


def latency(c, url, pack):
    print("\n== Latency: 20 triggers in one tick (cold)")
    base(c, url, pack)
    tids = [t for t in pack["trigger"]][:40]
    for tid in tids:
        c.post(f"{url}/v1/context", json={"scope": "trigger", "context_id": tid, "version": 1, "payload": pack["trigger"][tid]})
    t0 = time.time()
    acts = c.post(f"{url}/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": tids}).json()["actions"]
    dt = time.time() - t0
    print(f"   {len(acts)} actions in {dt:.1f}s", "PASS" if dt < 12 else "FAIL (>12s)")


def determinism(c, url, pack):
    print("\n== Determinism: same inputs twice")
    outs = []
    for _ in range(2):
        base(c, url, pack)
        tids = [p["trigger_id"] for p in pack["pairs"]]
        for tid in tids:
            c.post(f"{url}/v1/context", json={"scope": "trigger", "context_id": tid, "version": 1, "payload": pack["trigger"][tid]})
        run = []
        for tid in tids:
            acts = c.post(f"{url}/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": [tid]}).json()["actions"]
            run.append(acts[0]["body"] if acts else "")
        outs.append(run)
    same = sum(a == b for a, b in zip(*outs))
    print(f"   identical bodies: {same}/{len(outs[0])}", "(template path is fully deterministic; LLM-polished ones can differ across a restart)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("BOT_URL", "http://localhost:8080"))
    ap.add_argument("which", nargs="*", default=["replay", "inject", "latency", "determinism"])
    args = ap.parse_args()
    url = args.url.rstrip("/")
    pack = load_pack()
    c = httpx.Client(timeout=40)
    if "replay" in args.which or "inject" in args.which:
        base(c, url, pack)
    if "replay" in args.which:
        print("\nREPLAY:", "ALL PASS" if replay(c, url, pack) else "SOME FAILED")
    if "inject" in args.which:
        inject(c, url, pack)
    if "latency" in args.which:
        latency(c, url, pack)
    if "determinism" in args.which:
        determinism(c, url, pack)
