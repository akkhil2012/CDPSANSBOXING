# CDP Segmentation: Agent Sandboxing Demo

Shows a confused-deputy attack between two agents in a CDP workflow, first with no
controls (PII leaks), then with a sandbox + policy gateway (leak blocked).

## Run

```bash
python run_demo.py                 # Docker sandbox, interactive approval
python run_demo.py --auto-approve  # no prompt
python run_demo.py --no-docker     # if Docker isn't installed (weaker isolation)
```

Requires Python 3.9+ (stdlib only). Docker is optional but recommended for the full effect.

## Files

| File | Role |
|---|---|
| `make_data.py` | Fake CDP: 50 customers, raw + masked copies, one poisoned note |
| `elevated_agent.py` | Tools: `score_profiles`, `read_raw_pii`, `activate_audience`. Trusts peers blindly |
| `segmentation_agent.py` | Low-privilege agent; reads untrusted notes; runs on host (A) or in Docker (B) |
| `gateway.py` | Allow-list, scoped short-lived HMAC token, audit log |
| `run_demo.py` | Runs both scenarios, approval gate, summary |
| `Dockerfile` | Image containing only the segmentation agent |

## What each scenario shows

**A: no controls.** Segmentation agent reads the poisoned note, forwards the instruction,
elevated agent exports raw emails and phones to `external-partner`.

**B: controls.** Same data, same fooled agent, but:
- Container has `--network none`, read-only filesystem, all capabilities dropped
- Only the masked dataset is mounted; raw PII is never visible
- No secrets in the image; the sandbox holds only a token scoped to `score_profiles`
- The gateway denies `activate_audience` (not on the allow-list) and logs it
- A human approves before anything is promoted to production

## Talking points

- The injection still works on the agent. Sandboxing doesn't fix the model, it limits the blast radius.
- Defense in depth: even if the allow-list had a bug, the token scope and the missing network/credentials would still block exfiltration.
- Audit trail gives you forensics: every cross-agent call, allowed or denied.

## Making it more realistic

- **Real LLMs:** replace the scripted "follows the injection" logic in `segmentation_agent.py`
  and `handle_peer_task` in `elevated_agent.py` with tool-calling LLM loops. Keep the gateway and
  sandbox unchanged. (LLM behavior is non-deterministic, so keep the scripted version as a fallback.)
- **Real transport:** swap the file mailbox for a Unix socket mounted into the container.
- **Real CDP:** point `elevated_agent.py` at your CDP's API and the gateway at your policy engine.

## Notes

- All data is fake and all "integrations" are local files. Nothing leaves your machine.
- The gateway secret is generated in memory per run; token handling here is demo-grade, not production-grade.
