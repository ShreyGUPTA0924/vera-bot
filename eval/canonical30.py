"""Canonical-30 runner: loads the expanded dataset into a running bot, fires each of the 30 test-pair
triggers, saves outputs, validates them, and (optionally) scores them with the official scorer prompt.

    python eval/canonical30.py                      # bot at http://localhost:8080, no judging
    python eval/canonical30.py --judge              # + LLM judge (uses your dev keys; ~4 min)
    python eval/canonical30.py --url https://<your-app>.onrender.com --now 2026-04-26T10:30:00Z
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

from _common import ROOT, judge_call, load_env, load_pack

load_env()


def push_all(c: httpx.Client, url: str, pack: dict, triggers: list[str]):
    c.post(f"{url}/v1/teardown")
    n = 0
    for scope in ("category", "merchant", "customer"):
        for cid, payload in pack[scope].items():
            r = c.post(f"{url}/v1/context", json={"scope": scope, "context_id": cid, "version": 1,
                                                  "payload": payload, "delivered_at": "2026-04-26T10:00:00Z"})
            n += r.status_code == 200
    for tid in triggers:
        c.post(f"{url}/v1/context", json={"scope": "trigger", "context_id": tid, "version": 1,
                                          "payload": pack["trigger"][tid], "delivered_at": "2026-04-26T10:00:00Z"})
    h = c.get(f"{url}/v1/healthz").json()
    print(f"pushed {n} base contexts; healthz: {h['contexts_loaded']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("BOT_URL", "http://localhost:8080"))
    ap.add_argument("--now", default="2026-04-26T10:30:00Z")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--only", default="", help="comma list of test ids, e.g. T01,T05")
    ap.add_argument("--pace", type=float, default=float(os.getenv("PACE", "4")),
                    help="seconds between ticks so free-tier LLM quota can polish each message")
    args = ap.parse_args()
    url = args.url.rstrip("/")
    pack = load_pack()
    pairs = [p for p in pack["pairs"] if not args.only or p["test_id"] in args.only.split(",")]
    c = httpx.Client(timeout=40)
    push_all(c, url, pack, [p["trigger_id"] for p in pairs])
    time.sleep(2)  # let background polish run

    from app.facts import Facts
    from app.util import parse_dt
    from app.validator import validate
    scorer = None
    if args.judge:
        sys.path.insert(0, str(ROOT / "starter"))
        import judge_simulator as js

        class Adapter:
            def complete(self, prompt, system=""):
                return judge_call(system, prompt) or ""
        scorer = js.LLMScorer(Adapter(), None)

    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    rows, dims = [], {"specificity": [], "category_fit": [], "merchant_fit": [], "decision_quality": [], "engagement_compulsion": []}
    for p in pairs:
        time.sleep(args.pace)
        t0 = time.time()
        r = c.post(f"{url}/v1/tick", json={"now": args.now, "available_triggers": [p["trigger_id"]]})
        lat = time.time() - t0
        acts = r.json().get("actions", [])
        trig = pack["trigger"][p["trigger_id"]]
        merch = pack["merchant"][p["merchant_id"]]
        cust = pack["customer"].get(p["customer_id"]) if p["customer_id"] else None
        cat = pack["category"][merch["category_slug"]]
        if not acts:
            print(f"\n[{p['test_id']}] {trig['kind']} — NO ACTION (bot chose restraint)  {lat:.1f}s")
            rows.append({"test_id": p["test_id"], "body": "", "cta": "none", "send_as": "", "suppression_key": trig.get("suppression_key"), "rationale": "no action"})
            continue
        a = acts[0]
        # rebuild the bot's own fact sheet (incl. derived day counts) to validate against
        from app.decide import plan_trigger
        from app.store import Store
        st = Store()
        for sc in ("category", "merchant", "customer"):
            for cid_, pl in pack[sc].items():
                st.put(sc, cid_, 1, pl)
        st.put("trigger", p["trigger_id"], 1, trig)
        pl_ = plan_trigger(st, p["trigger_id"], parse_dt(args.now))
        f = getattr(pl_, "facts", None) or Facts(category=cat, merchant=merch, trigger=trig, customer=cust, now=parse_dt(args.now))
        v = validate(a["body"], f, cta=a["cta"], audience="customer" if a["send_as"] != "vera" else "merchant")
        print(f"\n[{p['test_id']}] {trig['kind']} | {a['send_as']} | {a['cta']} | {len(a['body'])}ch | {lat:.1f}s")
        print("   " + a["body"].replace("\n", "\n   "))
        if v:
            print("   !! validator:", v)
        rows.append({"test_id": p["test_id"], "body": a["body"], "cta": a["cta"], "send_as": a["send_as"],
                     "suppression_key": a["suppression_key"], "rationale": a["rationale"]})
        if scorer:
            s = scorer.score(a, cat, merch, trig, cust)
            for k in dims:
                dims[k].append(getattr(s, k))
            print(f"   judge: spec {s.specificity} | cat {s.category_fit} | merch {s.merchant_fit} | "
                  f"decision {s.decision_quality} | engage {s.engagement_compulsion}  => {s.total}/50")
            if s.hint:
                print("   hint:", s.hint)
            time.sleep(float(os.getenv("JUDGE_SLEEP", "10")))
    with open(out_dir / "submission.jsonl", "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"\nwrote {out_dir / 'submission.jsonl'}")
    if scorer and dims["specificity"]:
        n = len(dims["specificity"])
        avg = {k: sum(v) / n for k, v in dims.items()}
        print("\nAVERAGES over", n, "scored messages:")
        for k, v in avg.items():
            print(f"   {k:<22} {v:.1f}")
        print(f"   {'TOTAL':<22} {sum(avg.values()):.1f} / 50")


if __name__ == "__main__":
    main()
