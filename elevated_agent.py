"""Elevated agent: can read raw PII and push audiences to the (mock) ad platform.

`handle_peer_task` is a scripted stand-in for an LLM agent that TRUSTS requests
from peer agents. That misplaced trust is the vulnerability the demo exercises.
Swap it for a real LLM tool-calling loop if you want; the tools stay the same.
"""
import csv
import json
import re
import time

from common import AD_PLATFORM, DATA_RAW, OUT, audit


def _load_raw():
    with open(DATA_RAW, newline="") as f:
        return list(csv.DictReader(f))


# ---- Tools -----------------------------------------------------------------

def score_profiles(customer_ids):
    """Low-risk tool: returns a purchase-propensity score per customer id."""
    wanted = set(customer_ids)
    return {
        r["customer_id"]: round(min(1.0, float(r["churn_score"]) * 0.9 + 0.05), 2)
        for r in _load_raw() if r["customer_id"] in wanted
    }


def read_raw_pii(customer_ids):
    """High-risk tool: returns real emails and phone numbers."""
    wanted = set(customer_ids)
    return [
        {"customer_id": r["customer_id"], "email": r["email"], "phone": r["phone"]}
        for r in _load_raw() if r["customer_id"] in wanted
    ]


def activate_audience(name, profiles):
    """High-risk tool: pushes profiles to the mock ad platform (a JSONL file)."""
    OUT.mkdir(parents=True, exist_ok=True)
    with open(AD_PLATFORM, "a") as f:
        for p in profiles:
            f.write(json.dumps({"audience": name, "received_at": time.strftime("%H:%M:%S"), **p}) + "\n")
    audit("elevated.ACTIVATED_AUDIENCE", audience=name, records=len(profiles), via="direct call, no gateway")
    return {"status": "ok", "action": "activate_audience", "audience": name, "records_sent": len(profiles)}


# ---- The vulnerable part: blind trust in peer agents ------------------------

def handle_peer_task(msg):
    """Interpret a natural-language task from a peer agent and run it. No checks."""
    task = msg["task"]
    ids = msg.get("args", {}).get("customer_ids", [])
    lowered = task.lower()

    if "export" in lowered or "activate" in lowered or "send" in lowered:
        m = re.search(r"audience '([^']+)'", task)
        audience = m.group(1) if m else "unnamed"
        return activate_audience(audience, read_raw_pii(ids))
    if "score" in lowered:
        return {"status": "ok", "action": "score_profiles", "scores": score_profiles(ids)}
    return {"status": "error", "reason": "unknown task"}
