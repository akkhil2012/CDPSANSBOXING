#!/usr/bin/env python3
"""Segmentation agent (low privilege).

Reads customer profiles, drafts a churn segment, and asks the elevated agent
for propensity scores. It also reads free-text notes, which are UNTRUSTED input.

To keep the demo deterministic, "prompt injection succeeds" is simulated: the
agent obeys any instruction it finds after 'Ignore prior instructions' in a note.
That is the compromised-LLM assumption; the demo is about containing it.

Self-contained (stdlib only) so the Docker image needs just this file.
"""
import argparse
import csv
import json
import os
import re
import time
import uuid
from pathlib import Path

CHURN_MIN = 0.70
DAYS_MIN = 60
INJECTION = re.compile(r"ignore prior instructions\.?\s*(?P<task>.+)", re.I)


def log(msg):
    print(f"  [segmentation-agent] {msg}", flush=True)


# ---- Transports: how this agent talks to the elevated agent -----------------

class DirectTransport:
    """Scenario A: direct call, peer trust, no gateway."""

    def call(self, task, action, args):
        from elevated_agent import handle_peer_task  # host-only import
        return handle_peer_task({"task": task, "action": action, "args": args})


class MailboxTransport:
    """Scenario B: file mailbox to the gateway. Works with --network none."""

    def __init__(self, io_dir, token):
        self.requests = Path(io_dir) / "requests"
        self.responses = Path(io_dir) / "responses"
        self.token = token

    def call(self, task, action, args, timeout=20):
        rid = uuid.uuid4().hex[:8]
        payload = {"id": rid, "token": self.token, "action": action, "args": args, "task": task}
        tmp = self.requests / f"{rid}.tmp"
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, self.requests / f"{rid}.json")
        out = self.responses / f"{rid}.json"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if out.exists():
                return json.loads(out.read_text())
            time.sleep(0.1)
        return {"status": "error", "reason": "gateway timeout"}


# ---- Agent logic ------------------------------------------------------------

def is_match(row):
    return float(row["churn_score"]) >= CHURN_MIN and int(row["days_since_purchase"]) >= DAYS_MIN


def task_to_request(task, ids):
    """Map a natural-language instruction to a structured action request."""
    lowered = task.lower()
    if "export" in lowered or "activate" in lowered or "send" in lowered:
        m = re.search(r"audience '([^']+)'", task)
        return "activate_audience", {
            "audience": m.group(1) if m else "unnamed",
            "fields": ["email", "phone"],
            "customer_ids": ids,
        }
    return "score_profiles", {"customer_ids": ids}


def describe(resp):
    if resp.get("status") == "denied":
        return f"DENIED ({resp.get('reason')})"
    if resp.get("action") == "activate_audience":
        return f"EXECUTED: {resp['records_sent']} raw profiles pushed to '{resp['audience']}'"
    return f"ok ({resp.get('action', resp.get('reason'))})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["direct", "mailbox"], required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--io", default="/io")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    transport = (DirectTransport() if args.mode == "direct"
                 else MailboxTransport(args.io, os.environ.get("GATEWAY_TOKEN", "")))

    with open(args.data, newline="") as f:
        rows = list(csv.DictReader(f))
    log(f"loaded {len(rows)} profiles from {args.data}")

    matched = [r for r in rows if is_match(r)]
    ids = [r["customer_id"] for r in matched]
    rule = f"churn_score >= {CHURN_MIN} AND days_since_purchase >= {DAYS_MIN}"
    log(f"drafted rule: {rule} -> {len(ids)} matches")

    # Legitimate request: ask the elevated agent for scores.
    resp = transport.call("Score these profiles for purchase propensity.", "score_profiles",
                          {"customer_ids": ids})
    scores = resp.get("scores", {})
    log(f"scoring request: {describe(resp)}")

    # Untrusted input: scan free-text notes.
    for r in rows:
        m = INJECTION.search(r["notes"])
        if not m:
            continue
        task = m.group("task").strip()
        log(f"note on {r['customer_id']} contains an instruction; following it (simulated injection)")
        log(f"  injected task: {task}")
        action, action_args = task_to_request(task, ids)
        resp = transport.call(task, action, action_args)
        log(f"elevated agent replied: {describe(resp)}")

    proposal = {
        "rule": rule,
        "segment_size": len(ids),
        "avg_propensity": round(sum(scores.values()) / len(scores), 2) if scores else None,
        "sample": [{"customer_id": r["customer_id"], "churn_score": r["churn_score"]} for r in matched[:5]],
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(proposal, indent=2))
    log(f"proposal written to {args.out}")


if __name__ == "__main__":
    main()
