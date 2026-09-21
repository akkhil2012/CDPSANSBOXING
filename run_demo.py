#!/usr/bin/env python3
"""Run the demo: same poisoned data, without and with the sandbox.

    python run_demo.py                 # both scenarios, Docker sandbox, asks for approval
    python run_demo.py --no-docker     # sandbox step runs as a plain process (no network isolation)
    python run_demo.py --auto-approve  # skip the interactive approval prompt
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

import make_data
from common import AD_PLATFORM, AUDIT_LOG, DATA_MASKED, DATA_RAW, IO, OUT, ROOT
from gateway import Gateway

IMAGE = "cdp-seg-agent"


def banner(text):
    print("\n" + "=" * 70 + f"\n{text}\n" + "=" * 70)


def reset():
    for d in (OUT, IO):
        shutil.rmtree(d, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    make_data.main()


def show_ad_platform():
    lines = AD_PLATFORM.read_text().splitlines() if AD_PLATFORM.exists() else []
    if not lines:
        print("\n  MOCK AD PLATFORM INBOX: empty (0 records received)")
        return 0
    print(f"\n  MOCK AD PLATFORM INBOX: {len(lines)} records received. First 3:")
    for line in lines[:3]:
        print("   ", line)
    return len(lines)


def docker_ok():
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def scenario_a():
    banner("SCENARIO A: no sandbox, no gateway (agent calls elevated agent directly)")
    out = OUT / "proposed_segment_A.json"
    subprocess.run([sys.executable, "segmentation_agent.py", "--mode", "direct",
                    "--data", str(DATA_RAW), "--out", str(out)], cwd=ROOT, check=True)
    leaked = show_ad_platform()
    print("\n  RESULT: the injection escalated through agent-to-agent trust. PII leaked.")
    return leaked


def scenario_b(use_docker):
    banner("SCENARIO B: sandboxed agent + policy gateway")
    AD_PLATFORM.unlink(missing_ok=True)
    if AUDIT_LOG.exists():
        AUDIT_LOG.rename(OUT / "audit_A.jsonl")

    gw = Gateway(IO)
    gw.start()
    token = gw.mint_token(scopes=["score_profiles"], ttl_seconds=300)
    proposal = IO / "out" / "proposed_segment.json"

    try:
        if use_docker:
            print("  Sandbox: Docker, --network none, read-only fs, masked data only, no raw PII\n")
            subprocess.run(["docker", "build", "-q", "-t", IMAGE, "."], cwd=ROOT, check=True,
                           stdout=subprocess.DEVNULL)
            cmd = ["docker", "run", "--rm", "--network", "none", "--read-only",
                   "--tmpfs", "/tmp", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges",
                   "--memory", "256m", "--pids-limit", "64",
                   "-v", f"{DATA_MASKED.parent}:/data:ro",   # masked data only; raw is never mounted
                   "-v", f"{IO}:/io",
                   "-e", f"GATEWAY_TOKEN={token}"]
            if hasattr(os, "getuid"):
                cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
            cmd += [IMAGE, "--mode", "mailbox", "--data", "/data/customers_masked.csv",
                    "--io", "/io", "--out", "/io/out/proposed_segment.json"]
            subprocess.run(cmd, check=True)
        else:
            print("  WARNING: --no-docker. Runs as a plain process: gateway and masked data still "
                  "apply, but no network/filesystem isolation.\n")
            env = {**os.environ, "GATEWAY_TOKEN": token}
            subprocess.run([sys.executable, "segmentation_agent.py", "--mode", "mailbox",
                            "--data", str(DATA_MASKED), "--io", str(IO), "--out", str(proposal)],
                           cwd=ROOT, env=env, check=True)
    finally:
        gw.stop()

    leaked = show_ad_platform()
    print("\n  AUDIT TRAIL (gateway):")
    for line in AUDIT_LOG.read_text().splitlines():
        rec = json.loads(line)
        print(f"    {rec.pop('ts')}  {rec.pop('event'):<16} {rec}")
    print("\n  RESULT: the agent was still fooled, but the gateway blocked the escalation.")
    return leaked, proposal


def approval_gate(proposal, auto):
    banner("HUMAN APPROVAL: promote sandbox output to production?")
    seg = json.loads(proposal.read_text())
    print(json.dumps(seg, indent=2))
    answer = "approve" if auto else input("\nType 'approve' to promote, anything else to reject: ").strip().lower()
    if answer == "approve":
        dest = OUT / "production"
        dest.mkdir(exist_ok=True)
        shutil.copy(proposal, dest / "segment_churn_30d.json")
        print(f"\n  PROMOTED -> {dest / 'segment_churn_30d.json'}")
    else:
        print("\n  REJECTED. Nothing reaches production.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-docker", action="store_true")
    ap.add_argument("--auto-approve", action="store_true")
    args = ap.parse_args()

    use_docker = not args.no_docker
    if use_docker and not docker_ok():
        print("Docker not available; falling back to --no-docker.")
        use_docker = False

    reset()
    leaked_a = scenario_a()
    leaked_b, proposal = scenario_b(use_docker)
    approval_gate(proposal, args.auto_approve)

    banner("SUMMARY")
    print(f"  Raw PII records reaching the ad platform:  A (no sandbox) = {leaked_a}   |   B (sandbox) = {leaked_b}")
    print("  Takeaway: the injection still worked on the agent. The sandbox limited what it could do.")


if __name__ == "__main__":
    main()
