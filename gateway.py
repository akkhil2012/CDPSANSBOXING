"""Policy gateway between the sandboxed segmentation agent and the elevated agent.

Controls enforced here (on the host, outside the sandbox):
  1. Allow-list of actions: only `score_profiles` is reachable.
  2. Scoped, short-lived, HMAC-signed token: the sandbox holds a token, never a secret.
  3. Structured requests only: natural-language "tasks" are never executed.
  4. Audit log of every request, allowed or denied.

Transport is a file mailbox (io/requests -> io/responses) so the sandbox needs
no network at all.
"""
import base64
import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path

from common import audit
from elevated_agent import score_profiles


class Gateway:
    ALLOWLIST = {"score_profiles"}

    def __init__(self, io_dir):
        self.io = Path(io_dir)
        self.requests = self.io / "requests"
        self.responses = self.io / "responses"
        for d in (self.requests, self.responses, self.io / "out"):
            d.mkdir(parents=True, exist_ok=True)
            os.chmod(d, 0o777)  # let the container's user write here
        self._secret = os.urandom(32)  # lives only in the host process
        self._stop = threading.Event()
        self._thread = None

    # ---- tokens ----
    def mint_token(self, scopes, ttl_seconds=300):
        body = base64.urlsafe_b64encode(
            json.dumps({"scopes": list(scopes), "exp": time.time() + ttl_seconds}).encode()
        ).decode()
        sig = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"

    def _verify(self, token, action):
        try:
            body, sig = token.rsplit(".", 1)
            expected = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected):
                return False, "bad token signature"
            claims = json.loads(base64.urlsafe_b64decode(body))
            if time.time() > claims["exp"]:
                return False, "token expired"
            if action not in claims["scopes"]:
                return False, f"action '{action}' outside token scope"
            return True, "ok"
        except Exception as exc:  # malformed token
            return False, f"invalid token ({exc})"

    # ---- policy ----
    def handle(self, req):
        rid, action = req.get("id"), req.get("action")
        if action not in self.ALLOWLIST:
            audit("gateway.DENIED", request_id=rid, action=action, reason="not on allow-list")
            return {"status": "denied", "reason": f"action '{action}' is not on the allow-list"}
        ok, why = self._verify(req.get("token", ""), action)
        if not ok:
            audit("gateway.DENIED", request_id=rid, action=action, reason=why)
            return {"status": "denied", "reason": why}
        ids = req.get("args", {}).get("customer_ids", [])
        if not isinstance(ids, list):
            audit("gateway.DENIED", request_id=rid, action=action, reason="bad arguments")
            return {"status": "denied", "reason": "bad arguments"}
        audit("gateway.ALLOWED", request_id=rid, action=action, n_ids=len(ids))
        return {"status": "ok", "action": action, "scores": score_profiles(ids)}

    # ---- mailbox loop ----
    def _loop(self):
        while not self._stop.is_set():
            for req_file in sorted(self.requests.glob("*.json")):
                try:
                    req = json.loads(req_file.read_text())
                    resp = self.handle(req)
                    tmp = self.responses / f"{req['id']}.tmp"
                    tmp.write_text(json.dumps(resp))
                    os.replace(tmp, self.responses / f"{req['id']}.json")
                finally:
                    req_file.unlink(missing_ok=True)
            time.sleep(0.1)

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
