---
name: restart-check
description: Verify a just-restarted Algo BNF server actually picked up pending code changes — confirms the restart is fresh, spot-checks whatever shipped since the last one, and scans for new errors
disable-model-invocation: true
---

# Restart check

Run this right after Richard says the server's been restarted. A restart is
the only way `index_ai.server` picks up a code change (CLAUDE.md is explicit
about this), so the point of this skill is catching the case where the
restart didn't actually happen, or happened before the latest merge, rather
than assuming "restarted" means "correct."

## Steps

1. **Confirm the restart is real and recent.** `GET /api/status` →
   `auto.started_at_ist`. This should be within the last few minutes of now
   — if it's older, the restart either didn't happen yet or failed silently;
   say so plainly instead of proceeding as if it worked.

2. **Find what's actually pending verification.** `git log --oneline -15`
   on `main` to see what's merged since the conversation's last confirmed-
   working restart. For each merge, check which files it touched
   (`git show --stat <sha>`) and sort into three buckets:
   - **Restart-requiring backend**: `index_ai/server.py`, `scanner.py`,
     `executor.py`, `dhan_orders.py`, `learning.py`, `hf_learning.py`,
     `ticker.py`, anything under `crypto/`, `commodities/`, `investing/`,
     or `config.py` — these need the checks in step 3.
   - **Dashboard**: `dashboard/src/**` — needs `npm --prefix dashboard run
     build` to have run *and* the server restarted after that build exists,
     not just a backend restart. Check `dashboard/dist`'s newest file
     mtime is after the merge commit's time; if the dashboard changed but
     `dist/` wasn't rebuilt since, say so explicitly rather than silently
     checking stale output.
   - **Nothing to verify**: docs, tests-only, memory files.

3. **Spot-check each pending backend/dashboard item concretely** — not a
   generic health check, but the actual thing that changed:
   - A new or changed endpoint → `curl` it directly, confirm a 200 with
     sane data, not a 404 or an error body.
   - A new config value (a symbol added to an allowlist, a feature flag,
     a new active lane) → confirm it's actually visible in the relevant
     status endpoint (e.g. a new crypto symbol should show up in
     `/api/crypto/status`'s `available_symbols`).
   - A dashboard change → if a preview/browser tool is available, actually
     load the page and check the specific thing that changed; otherwise
     say a manual browser check is still needed and what to look for.

4. **Error scan since the restart.** Tail `memory/server.log` from the
   restart timestamp forward (not just an arbitrary line count), grep
   `error|exception`, and exclude known-benign noise that's never worth
   reporting: `getaddrinfo failed` (transient DNS, self-recovers),
   `ConnectionResetError`, `_call_connection_lost`, `Proactor` (Windows
   asyncio artifacts from any client — including your own test curls —
   disconnecting abruptly). Anything else that survives is a real finding.

## Output

```
RESTART CHECK — started <started_at_ist>
Fresh restart:     yes / no — <reason if no>
Pending changes:   <N commits since last check, one line each>
Backend checks:    <n/a / each spot-check result, endpoint by endpoint>
Dashboard:         <n/a / rebuilt+verified / rebuilt but not restarted-into / stale>
New errors:        <none / quoted findings>
ALL CLEAR: yes/no
```

If anything is unclear — no way to tell what "last confirmed restart" means
because the conversation just started, or git history doesn't cleanly map
to a single restart boundary — say so and fall back to a plain full health
check (the `status-check` skill) rather than guessing.
