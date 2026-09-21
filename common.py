"""Shared paths, masking helper, and audit logger for the CDP sandbox demo."""
import hashlib
import json
import time
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
DATA_RAW = ROOT / "data" / "raw" / "customers_raw.csv"          # real PII (host only)
DATA_MASKED = ROOT / "data" / "masked" / "customers_masked.csv"  # hashed PII (sandbox gets this)
OUT = ROOT / "out"
IO = ROOT / "io"                                                 # mailbox shared with the sandbox
AD_PLATFORM = OUT / "ad_platform_inbox.jsonl"                    # mock ad platform
AUDIT_LOG = OUT / "audit.jsonl"


def mask(value: str) -> str:
    """One-way hash so the sandbox can join on a value without seeing it."""
    return hashlib.sha256(("demo-salt:" + value).encode()).hexdigest()[:12]


def audit(event: str, **fields) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.strftime("%H:%M:%S"), "event": event, **fields}
    with open(AUDIT_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")
