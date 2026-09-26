"""Contract tests: the API behaves exactly as the challenge's testing brief specifies.

    python -m pytest -q
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.main import app  # noqa: E402
from app.store import STORE  # noqa: E402

REQUIRED_ACTION_FIELDS = {"conversation_id", "merchant_id", "customer_id", "send_as", "trigger_id", "template_name",
                          "template_params", "body", "cta", "suppression_key", "rationale"}

CAT = {"slug": "dentists", "voice": {"tone": "peer_clinical", "vocab_taboo": ["guaranteed"], "code_mix": "hindi_english_natural"},
       "offer_catalog": [{"title": "Dental Cleaning @ ₹299", "type": "service_at_price"}],
       "peer_stats": {"avg_ctr": 0.03, "avg_calls_30d": 12, "avg_views_30d": 1820},
       "digest": [{"id": "d1", "kind": "research", "title": "3-month recall beats 6-month", "source": "JIDA Oct 2026, p.14",
                   "summary": "Recall at 3 months cut recurrence 38%.", "actionable": "Review recall intervals"}]}
MER = {"merchant_id": "m1", "category_slug": "dentists",
       "identity": {"name": "Test Dental", "city": "Delhi", "locality": "Saket", "languages": ["en", "hi"], "owner_first_name": "Asha"},
       "subscription": {"status": "active", "plan": "Pro", "days_remaining": 80},
       "performance": {"window_days": 30, "views": 1000, "calls": 6, "ctr": 0.02, "delta_7d": {"views_pct": -0.2, "calls_pct": -0.3}},
       "offers": [], "conversation_history": [], "customer_aggregate": {"total_unique_ytd": 300}, "signals": []}
TRG = {"id": "t1", "scope": "merchant", "kind": "perf_dip", "source": "internal", "merchant_id": "m1",
       "payload": {"metric": "calls", "delta_pct": -0.3}, "urgency": 4, "suppression_key": "dip:m1:w1"}


@pytest.fixture()
def c():
    STORE.reset()
    with TestClient(app) as client:
        yield client


def push(c, scope, cid, version, payload):
    return c.post("/v1/context", json={"scope": scope, "context_id": cid, "version": version, "payload": payload,
                                       "delivered_at": "2026-04-26T10:00:00Z"})


def test_healthz_and_metadata(c):
    h = c.get("/v1/healthz").json()
    assert h["status"] == "ok" and set(h["contexts_loaded"]) == {"category", "merchant", "customer", "trigger"}
    m = c.get("/v1/metadata").json()
    assert {"team_name", "team_members", "model", "approach", "version"} <= set(m)


def test_context_versioning(c):
    assert push(c, "category", "dentists", 1, CAT).status_code == 200
    r = push(c, "category", "dentists", 1, CAT)
    assert r.status_code == 409 and r.json() == {"accepted": False, "reason": "stale_version", "current_version": 1}
    assert push(c, "category", "dentists", 2, CAT).status_code == 200
    assert push(c, "category", "dentists", 1, CAT).status_code == 409
    r = push(c, "bogus", "x", 1, {})
    assert r.status_code == 400 and r.json()["accepted"] is False
    assert c.post("/v1/context", json={"scope": "merchant"}).status_code == 400
    assert c.get("/v1/healthz").json()["contexts_loaded"]["category"] == 1


def test_tick_contract_and_suppression(c):
    push(c, "category", "dentists", 1, CAT)
    push(c, "merchant", "m1", 1, MER)
    push(c, "trigger", "t1", 1, TRG)
    assert c.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": []}).json() == {"actions": []}
    acts = c.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": ["t1", "unknown"]}).json()["actions"]
    assert len(acts) == 1
    a = acts[0]
    assert REQUIRED_ACTION_FIELDS <= set(a)
    assert a["body"] and a["send_as"] == "vera" and a["suppression_key"] == "dip:m1:w1"
    assert "Dr. Asha" in a["body"] and "30%" in a["body"]
    # same trigger listed again next tick -> not re-sent
    again = c.post("/v1/tick", json={"now": "2026-04-26T10:35:00Z", "available_triggers": ["t1"]}).json()["actions"]
    assert again == []


def test_dip_claim_not_supported_pivots(c):
    push(c, "category", "dentists", 1, CAT)
    m = dict(MER, performance=dict(MER["performance"], delta_7d={"views_pct": 0.08, "calls_pct": 0.02}))
    push(c, "merchant", "m1", 1, m)
    push(c, "trigger", "t2", 1, dict(TRG, id="t2", payload={"placeholder": True}, suppression_key="dip2"))
    a = c.post("/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": ["t2"]}).json()["actions"][0]
    assert "down" not in a["body"].lower()  # never claims a dip that isn't in the data
    assert "Pivot" in a["rationale"]


def test_reply_flows(c):
    push(c, "category", "dentists", 1, CAT)
    push(c, "merchant", "m1", 1, MER)
    auto = "Thank you for contacting us! Our team will respond shortly."
    acts = [c.post("/v1/reply", json={"conversation_id": f"cx{i}", "merchant_id": "m1", "from_role": "merchant",
                                      "message": auto, "turn_number": i + 1}).json()["action"] for i in range(1, 5)]
    assert acts[:3] == ["send", "wait", "end"]
    r = c.post("/v1/reply", json={"conversation_id": "ci", "merchant_id": "m1", "from_role": "merchant",
                                  "message": "Ok lets do it. Whats next?", "turn_number": 2}).json()
    low = r["body"].lower()
    assert r["action"] == "send" and any(w in low for w in ["done", "draft", "confirm", "next", "here"])
    assert not any(q in low for q in ["would you", "do you", "can you tell", "what if", "how about"])
    r = c.post("/v1/reply", json={"conversation_id": "ch", "merchant_id": "m1", "from_role": "merchant",
                                  "message": "Stop messaging me. This is useless spam.", "turn_number": 2}).json()
    assert r["action"] == "end"
    r = c.post("/v1/reply", json={"conversation_id": "ch", "merchant_id": "m1", "from_role": "merchant",
                                  "message": "hello?", "turn_number": 3}).json()
    assert r["action"] == "end"


def test_teardown(c):
    push(c, "category", "dentists", 1, CAT)
    c.post("/v1/teardown")
    assert c.get("/v1/healthz").json()["contexts_loaded"]["category"] == 0
