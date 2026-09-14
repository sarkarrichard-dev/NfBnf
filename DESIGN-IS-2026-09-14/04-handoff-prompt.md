/make-plan Redesign QuantHawk dashboard's shared component layer. Current design failed a Dieter Rams audit at 17/30 with critical gaps in principles #2 (useful), #3 (aesthetic), #4 (understandable), #8 (thorough), and #10 (as little design as possible).

Verdict paragraph (quoted from 03-verdict.md):
> QuantHawk's dashboard has a real, deliberate, well-chosen design foundation — an honest voice, a genuine brand, a lean bundle — but it is executed inconsistently enough (a fragmented type scale, a third of its colors bypassing its own token system, five reimplementations of the same card, a missing standing-required control on three of four lanes, a rejected word still on screen, a reproducible keyboard-navigation bug) that the numeric threshold calls for a redesign pass, not a handful of isolated patches. Read this REDESIGN narrowly: nothing says the information architecture, the tab structure, the brand, or the product's honest voice are wrong — those scored well or are preserved wholesale. What scored low is execution discipline: the same four or five patterns were rebuilt by hand each time instead of shared once.

Why redesign and not refine: total score is 17/30, below the ≥20 threshold for REFINE, even though no single principle scored 0 — the audit's own rule reads a below-20 total as REDESIGN regardless.

Preserve from current design (all of this stays untouched):
- The tab/screen structure and information architecture (Dashboard, Strategies, Crypto, Futures, Commodities, Strategy P&L, Trade history, Reports & PnL, Research, Settings) — `dashboard/src/App.tsx`.
- The brand tokens themselves: hawk-gold accent (`--acc: #f2a63c`), near-black ground, the `--up`/`--down`/`--warn`/`--armed` semantic colors, Plus Jakarta Sans + Geist Mono — `dashboard/src/index.css:59-83`. These scored well (#7 long-lasting = 3/3) and are the *target* every hardcoded color should be migrated onto, not replaced.
- The honest copy voice and every "advisory only" / net-negative self-disclosure already in place (`BrainPanel.tsx`, `DayReviewPanel.tsx`, `CryptoDayReviewPanel.tsx`, `strategies/MarketplaceView.tsx:86-88`) — scored 2/3, among the strongest findings in the whole audit. Do not soften or remove these disclosures while touching nearby copy.
- The asymmetric safety friction (Live→Paper needs no confirm, Paper→Live does) on both `ExecutionPanel.tsx` and `CryptoExecutionPanel.tsx` — verified correct, not a defect.
- The India lane's Confirm-modal (not typed-phrase) live-arming flow — this is a previously-approved, deliberate product decision, not a bug; do not add a typed-phrase requirement to it while touching that file for other reasons.
- Every shared component already doing its job with a single implementation: `PeriodBar`, `TradeLogTable`, `SourceToggle`, `EmptyState`.

Discard (the specific patterns causing the failures — nothing else):
- Five independent stat-tile implementations. Evidence: `CryptoPanel.tsx:117-140` (`StatTile`), `FuturesPanel.tsx:44-51` (`Stat`), `CommoditiesPanel.tsx:62-69` (`Stat`), `pages/ReportsPage.tsx:24-39` (`Tile`), `StatsRail.tsx:154-164` (inlined). Caused failure on #10 and contributed to #3 (each reimplementation drifted slightly on spacing/type).
- Five independent rupee-formatting functions. Evidence: `lib/pnl.ts` (`money()`, canonical — keep this one), `LanesPanel.tsx:11-12` (`rupees()`), `CommoditiesPanel.tsx:57-58`, `strategies/DeploymentsView.tsx:5-6`, `strategies/MyStrategiesView.tsx:7-8`. Caused failure on #10.
- Two hand-copied Paper/Live toggle + lots-stepper implementations. Evidence: `ExecutionPanel.tsx:131-218` vs. `CryptoExecutionPanel.tsx:140-239`, verbatim-duplicated class strings. Caused failure on #10 and #3 (drift risk between two "identical" controls that are actually two separate copies).
- Two entirely dead files and two dead exports. Evidence: `components/ui/Tabs.tsx` (65 lines, zero imports), `components/PositionsPanel.tsx` (169 lines, zero imports — superseded by `JournalPanel.tsx`'s inline table), `components/ui/Button.tsx:91-97` (`ButtonGroup`), `:99-116` (`ToggleButton`). Caused failure on #10.
- 34 hardcoded Tailwind color utility classes bypassing the token system. Evidence: `bg-emerald-500`, `text-emerald-300`, `border-amber-500`, `text-rose-300`, `text-red-400` and 29 more variants, repo-wide, including visibly in `TradeLogTable.tsx`'s P&L cells. Caused failure on #3.
- ~10 arbitrary sub-pixel font sizes outside the named Tailwind scale. Evidence: `text-[10px]`, `text-[10.5px]`, `text-[11px]`, `text-[11.5px]`, `text-[12px]`, `text-[12.5px]`, `text-[13px]`, `text-[13.5px]`, `text-[14px]`, repo-wide. Caused failure on #3.
- The word "friction" and ~10 other unexplained jargon terms. Evidence: `FuturesPanel.tsx:67,186,214`, `lib/strategies.ts:159` ("friction" — a word Richard explicitly told a prior session he doesn't understand and asked removed, per standing project memory); also MTM, LTP, "OOS Δ", PF, "Ratchet step", "Sell gate"/"Credit stop"/"Credit profit", "Egress IP"/"whitelist", unexpanded "CPR + EMA + Supertrend". Caused failure on #4.
- Missing manual "Close position" control on 3 of 4 open-position surfaces. Evidence: present only on `CryptoPanel.tsx:372-382`; absent from `JournalPanel.tsx` (via `PositionRow.tsx`), `LanesPanel.tsx:55-65`, `CommoditiesPanel.tsx:152-177` — a standing, previously-recorded product requirement ("a manual override/close control on every lane that can hold a position") that was never finished. Caused failure on #2.
- The `md:hidden` mobile "Close menu" element being keyboard-focusable at desktop width, and a non-deterministic Tab order (differs across two identical 15-press walks from the same starting state). Evidence: `shell/Sidebar.tsx:31`; reproduced twice. Caused failure on #8. Also fix the one failing contrast pair while here: the inactive Paper/Live toggle label at 3.86:1 (needs 4.5:1) — `ExecutionPanel.tsx:161-162` / `CryptoExecutionPanel.tsx` equivalent.

Top 5 moves from the audit (verbatim):
1. #10 — Unify the five duplicated patterns (stat tile, money formatter, Paper/Live toggle) into one shared component each; delete the 2 dead files and 2 dead exports.
2. #2 — Add the manual "Close position" control to Journal, Lanes (futures), and Commodities open-position lists — the same close machinery Crypto's already uses, per the project's own "never a separately-computed close path" convention.
3. #4 — Remove "friction" from every screen (`FuturesPanel.tsx`, `lib/strategies.ts`), replacing it with concrete rupee-charge phrasing (model: `CommoditiesPanel.tsx:196-199`); do a pass on the remaining ~10 jargon terms.
4. #3 — Collapse the ~14-value type scale onto the named Tailwind scale (xs/sm/base/lg/2xl); replace all 34 hardcoded color utility classes with the existing `--up`/`--down`/`--warn`/`--armed` tokens.
5. #8 — Fix the `md:hidden` focus leak and the non-deterministic Tab order; lift the one failing contrast pair to a passing token.

Redesign principles in priority order:
1. #10 (as little design as possible) — every card, formatter, and toggle exists once, imported everywhere it's needed, not rebuilt per screen.
2. #4 (understandable) — no word a first-time reader can't parse without help, and never a word this specific user has already said he doesn't understand.
3. #2 (useful) — every lane that can hold a position gives the operator the same manual override, with no exceptions.

Deliverables for the plan:
- A component inventory: for each of the 5 duplicated patterns, one canonical implementation + a list of every call site to migrate, in the same style `TradeLogTable`/`PeriodBar` already establish as the working precedent in this codebase.
- A token-migration checklist: the 34 hardcoded color classes mapped 1:1 to their intended semantic token, file:line each.
- A jargon-and-copy pass: "friction" and the ~10 other flagged terms, each with its plain-English replacement already drafted in `01-evidence.md`'s Copy & Honesty section.
- The manual-close feature ported to 3 new call sites, reusing `crypto.lanes`-style "same exit machinery, no separately-computed P&L" discipline on the backend equivalents for futures/commodities if a backend close endpoint doesn't already exist for those lanes — confirm this before assuming it's frontend-only.
- A states checklist re-verification: empty/loading/error/success/focus/disabled, specifically confirmed present on the Crypto tab (not just Dashboard) before calling this done.
- The two accessibility fixes (focus leak, contrast) with a regression check: repeat the 15-press Tab walk twice and confirm identical sequences both times.
- Migration path: none needed — every change here is additive or a like-for-like internal swap; no user-visible flow changes, no data migration.
- Cutover criteria: n/a — this ships as normal PRs against `main`, no flag, no parallel old/new version to retire.

Anti-patterns to guard against (specific to this REDESIGN):
- Porting the old per-screen duplication under a new shared name — actually consolidate to one component, don't rename five copies.
- Treating the token/jargon/accessibility passes as optional cleanup — they're separate top-5 items each tied to a scored failure, not drive-by polish.
- Touching the brand palette, the tab structure, or the India lane's Confirm-modal arming flow while in this code — all three are explicitly on the Preserve list above and scored well; changing them is out of scope for this pass.
- Skipping the regression check on the Tab-order fix — the bug was non-deterministic across repeated identical walks, so a single manual click-through is not sufficient evidence it's fixed.
