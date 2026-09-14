---
name: pr-check
description: Run Algo BNF's full pre-PR checklist from CLAUDE.md — tests, lint diff, dashboard build/typecheck, and a judgment call on whether trading-safety-reviewer or test-isolation-reviewer is warranted
disable-model-invocation: true
---

# Pre-PR check

Run this before opening a PR on Algo BNF. It's the exact checklist CLAUDE.md
already documents — this just runs it in order and reports a single pass/fail
summary instead of doing each step by hand.

## Steps

1. **Scope the change.** `git status --porcelain` and `git diff --stat main...HEAD`
   (or against the branch's merge-base if not on `main`) to see every file
   touched this branch.

2. **Tests.** `python -m pytest -q`. Must be green — 0 failures. If a test
   file under `tests/` was itself touched this branch, note that in the
   summary (it's a signal to consider `test-isolation-reviewer` below).

3. **Lint — diff only, not the whole tree.** `CLAUDE.md` is explicit that
   `ruff check index_ai/` has ~8 pre-existing cosmetic errors that are not
   worth chasing. Run `ruff check` only on the files this branch actually
   touched (from step 1's file list), and compare against `git stash` if
   there's any doubt whether a finding predates this branch.

4. **Dashboard, only if `dashboard/src/` changed:**
   - `npx tsc -b --noEmit` from `dashboard/` — must be clean.
   - `npm --prefix dashboard run build` — must succeed.
   - Note in the summary that a browser check of the golden path is still the
     user's own call to make (or offer to drive it yourself if a preview
     tool is available), since this skill only verifies the build compiles.

5. **Money-path review — decide, don't skip silently.** Check the touched
   files against `trading-safety-reviewer`'s trigger list (`executor.py`,
   `dhan_orders.py`, `exit.py`, `config.py` trading flags, `charges.py`,
   `spread_calib.py`, any FastAPI handler in `server.py`, and by the same
   logic `crypto/executor.py`, `crypto/lanes.py`, `commodities/lanes.py`).
   If any match, either invoke `trading-safety-reviewer` now or state
   explicitly in the summary why this change is paper-only/display-only and
   the review is being skipped — CLAUDE.md's own convention is to document
   that reasoning in the PR body, not to skip it without a note.

6. **Test isolation — decide, don't skip silently.** If any test file under
   `tests/` was added or changed this branch, check whether it calls real
   production code that can reach `index_ai.notify`, a broker client
   (Dhan/Delta), or the real `.env`/state files without mocking — the exact
   pattern `test-isolation-reviewer` exists to catch (see that subagent's
   description for the full checklist; this was a real, months-long bug in
   this exact repo, not a hypothetical). Invoke it if the change is
   nontrivial, or note why it's unnecessary (e.g. the test only exercises a
   pure function with no side effects).

## Output

One summary block:

```
PR CHECK — <branch name>
Tests:        <pass/fail, N passed>
Lint (diff):  <clean / N new findings, listed>
Dashboard:    <n/a / tsc clean + build ok / FAILED — details>
Safety review: <n/a / done, clean / done, N findings / SKIPPED — <reason>>
Test isolation: <n/a / done, clean / done, N findings / SKIPPED — <reason>>
READY TO OPEN PR: yes/no
```

If anything is red, stop and fix it before proceeding — don't open the PR
with a known-failing step.
