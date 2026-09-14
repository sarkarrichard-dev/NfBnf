# Evidence — QuantHawk dashboard Rams audit

Consolidated from five parallel evidence-gathering passes (Structural, Visual,
Copy & Honesty, Weight & Friction, Accessibility). Every fact below carries
its source citation from the originating pass; nothing here is the
orchestrator's opinion — scoring happens in `02-scorecard.md`.

## Structural

- **Interactive-element counts (default-visible, per screen):** Dashboard ≈21,
  Crypto ≈13, Futures ≈6 (only the shared `PeriodBar` — `FuturesPanel.tsx`
  and `LanesPanel.tsx` have zero own buttons/inputs), Commodities ≈6 (same —
  `CommoditiesPanel.tsx` has zero own controls beyond `PeriodBar`), Strategies
  ≈10-11, Settings ≈9 + N dynamic feature-flag buttons.
- **Nesting depth:** the *same* shared `TradeLogTable` reaches depth ~9 when
  called from `CryptoPanel.tsx:396`, but depth ~10 when called from
  `FuturesPanel.tsx:159` and `CommoditiesPanel.tsx:186` — both wrap it in an
  extra local `<section>`/`<div>` `CryptoPanel` doesn't need.
- **Shared components (working as intended):** `PeriodBar` (6 call sites),
  `TradeLogTable` (5 call sites), `SourceToggle` (2 call sites) — single
  implementation each, no duplication.
- **Duplicated markup instead of a shared component:**
  - Paper/Live toggle: `ExecutionPanel.tsx:131-163` and
    `CryptoExecutionPanel.tsx:140-175` are two independent hand-rolled sliding
    switches with verbatim-identical Tailwind class strings.
  - Lots stepper: `ExecutionPanel.tsx:176-218` / `CryptoExecutionPanel.tsx:190-239`
    — same wrapper class literally duplicated.
  - Arm/disarm strip shares its outer class string too
    (`ExecutionPanel.tsx:240-245` / `CryptoExecutionPanel.tsx:244-250`) but the
    two confirmation *patterns* diverge (modal vs. inline typed phrase — see
    Copy & Honesty; this divergence is deliberate, not a bug, see below).
  - Stat/metric tile: 5 independent local definitions of the same
    label-over-value card — `CryptoPanel.tsx:117-140` (`StatTile`),
    `FuturesPanel.tsx:44-51` (`Stat`), `CommoditiesPanel.tsx:62-69` (`Stat`),
    `pages/ReportsPage.tsx:24-39` (`Tile`), `StatsRail.tsx:154-164` (inlined
    in a `.map`, not even a function).
  - Rupee formatting logic: canonical `money()` in `lib/pnl.ts`, plus 4 more
    independent reimplementations — `LanesPanel.tsx:11-12` (`rupees()`),
    `CommoditiesPanel.tsx:57-58`, `strategies/DeploymentsView.tsx:5-6`,
    `strategies/MyStrategiesView.tsx:7-8`.
- **Missing pattern where one should exist:** manual "Close position" exists
  in exactly one place, `CryptoPanel.tsx:372-382`. The three other screens
  with structurally-identical open-position lists have no close action at
  all: `JournalPanel.tsx`'s table (via `PositionRow.tsx`, no action column),
  `LanesPanel.tsx`'s `LaneCard` (`:55-65`, read-only), `CommoditiesPanel.tsx`'s
  list (`:152-177`, read-only). Repo-wide grep for `window.confirm`/
  `positions/close` returns exactly one hit.
- **Dead code:** `components/ui/Tabs.tsx` (65 lines, exports `Tabs`/`TabDef`,
  zero imports anywhere). `components/PositionsPanel.tsx` (169 lines, zero
  imports — `JournalPanel.tsx` reimplements the same table inline instead of
  using it). `components/ui/Button.tsx:91-97` (`ButtonGroup`, unused),
  `:99-116` (`ToggleButton`, unused). Several unused type fields:
  `FuturesPanel.tsx:25` (`Agg.net_by_year`), `CommoditiesPanel.tsx:45`
  (`multiplier`), `CryptoPanel.tsx:91` (`Positions.live`), `LanesPanel.tsx:4,8`
  (`lanes`, `recent_trades`).
- **Not inspected:** data-dependent counts (feature-flag list, symbol list,
  open-position counts) are inherently non-static. `CryptoSetupPanel.tsx`,
  `BacktestPanel.tsx`, `LearningPanel.tsx`, `BrainPanel.tsx`, both chart
  components were grepped, not fully read (lower priority: collapsed by
  default, or outside the six primary screens).

## Visual

- **Spacing scale:** 11 distinct values in active use (2, 4, 6, 8, 10, 12,
  14, 16, 20, 24, 32px) plus one arbitrary `py-[7px]`
  (`dashboard/src/lib/theme.ts:14`).
- **Type scale:** 14 distinct font sizes, 11 of them clustered in an 8–14px
  band in ~0.5–1px increments (`text-[10px]`, `text-[10.5px]`, `text-[11px]`,
  `text-[11.5px]`, `text-[12px]`, `text-[12.5px]`, `text-[13px]`,
  `text-[13.5px]`, `text-[14px]` are all separately in use).
- **Color tokens:** 11 real design-system color values defined in
  `dashboard/src/index.css:59-83`. Against that: **34 distinct hardcoded
  Tailwind color utility classes** bypass the token system repo-wide (e.g.
  `bg-emerald-500` ×12, `text-emerald-300` ×11, `border-amber-500` ×8,
  `text-rose-300` ×7, `text-red-400` ×6 — visibly present in
  `TradeLogTable.tsx`'s red/green P&L cells instead of the `--up`/`--down`
  tokens the branding work established).
- **Contrast — 3 failing pairs, all measured live via computed styles:**
  - 2.43:1 — `text-[11px] text-slate-600` ("all off = paused", Crypto tab).
  - 2.62:1 — `text-slate-600` uppercase ("Insights" sidebar section label).
  - 3.86:1 — `text-xs text-slate-500` (Crypto tab subhead).
  All three are below the WCAG AA 4.5:1 floor for normal text; none reach
  even the 3:1 large-text floor at these sizes (10-12px).
- **States checklist:**
  - Dashboard: empty ✓ ("No open positions/trades" copy), loading ✓
    (confirmed via DOM text, not screenshot — a race made the image miss it),
    focus ✓ (`focus-visible:ring-2 ring-[var(--acc)]/70`), disabled ✓ (lots
    stepper floor, scanner Start while running). Error/success not
    inducible without disconnecting the broker.
  - Crypto: focus ✓ (same token). Empty/loading/error/success not observed
    in this pass — the tab had live data throughout, so absence of evidence
    here is not evidence of absence.
- **Bonus finding (not one of the 5 required fields):** the sidebar's active-
  tab highlight was observed to visibly lag or mismatch the actually-rendered
  `<main>` content during fast tab switches (nav showed "Commodities"
  highlighted while `<main>` still rendered "Futures"). Reproduced; every
  screenshot used for this audit was independently verified against
  `document.querySelector('main h1, main h2').textContent` before capture.

## Copy & Honesty

- **String inventory:** all 43 `.tsx` component files read. The interface is
  broadly plain-spoken — every AI-generated panel (`BrainPanel`,
  `DayReviewPanel`, `CryptoDayReviewPanel`) is explicitly tagged "advisory
  only," and `strategies/MarketplaceView.tsx:86-88` / `MyStrategiesView.tsx`
  (via `lib/strategies.ts:159`) plainly disclose that the index-options
  engine is "net-negative after real costs" — an unusually honest admission
  most comparable products would never surface.
- **Inflations:** only two weak candidates found, neither a clean hit — the
  sidebar tagline "data drives discipline" (`Sidebar.tsx:90`, an unbacked
  outcome claim) and the verb "Optimize from OI" (`LearningPanel.tsx:55`,
  implies improvement with no before/after shown).
- **Dark patterns: none found.** The asymmetric friction correctly favors
  safety — switching Live→Paper needs no confirmation (`ExecutinPanel.tsx:111`
  comment: "safe direction — no confirm, server disarms"), while Paper→Live
  requires an explicit confirm step on both lanes.
- **Jargon still present, including one word the user explicitly rejected:**
  **"friction"** appears in `FuturesPanel.tsx:67,186,214` and in
  `lib/strategies.ts:159` (rendered into `MyStrategiesView.tsx:84`) — Richard
  told a prior session directly he doesn't understand this word and asked
  for costs to be described as concrete rupee charges instead (recorded in
  project memory as a standing instruction); it is still on screen. Other
  unexplained jargon: "MTM" (5 files), "LTP" (2 files), "OOS Δ"
  (`CryptoPanel.tsx:522`), "walk-forward validation" (`BrainPanel.tsx:102`),
  "PF" (`FuturesPanel.tsx:91`), "Ratchet step" (`BuilderView.tsx:57,101`),
  "Sell gate"/"Credit stop"/"Credit profit"/"Sideways credit"
  (`StrategyTuningPanel.tsx:27,31-33`), "Egress IP"/"whitelist"
  (`ExchangesView.tsx:40-43`), an unexpanded acronym stack "CPR + EMA +
  Supertrend" (`FuturesPanel.tsx:151`, `CommoditiesPanel.tsx:92`), and one
  dense compound-jargon sentence surfaced verbatim from `lib/strategies.ts:157`
  into `MyStrategiesView.tsx:59`.
- **Label→behavior findings:**
  1. ~~India's Confirm-modal live-arming vs. crypto's typed-phrase
     confirmation~~ — **verified directly by the orchestrator, this is NOT a
     mismatch.** The modal-not-typed-phrase design for the index lane is a
     documented, explicit product decision Richard asked for; the backend
     phrase-check is still enforced, the modal just supplies it after a
     deliberate second click. The real finding here is narrower: **the two
     lanes now confirm arming two different ways** (modal-only for India,
     modal-only for crypto for going Live, but crypto's separate "Arm live
     orders" step requires typing `ARM_PHRASE` via `CryptoExecutionPanel.tsx:267-278`
     while India's "Arm live orders" step does not) — an inconsistency
     between lanes doing the same job, not a broken safeguard on either one.
  2. `strategies/MarketplaceView.tsx:63-66` — button says "Deploy", its
     handler (`onBuild`, `StrategiesPage.tsx:34-37`) only switches to the
     Builder tab; nothing is deployed.
  3. `strategies/BuilderView.tsx:180-186` — button also says "Deploy"; its own
     click handler fires a toast admitting nothing deploys yet ("Saving
     custom strategies lands in Phase 4... This is the config that would
     deploy") — a mismatched label, but an honestly self-disclosing one.
  4. `CryptoSetupPanel.tsx:186` — "Test connection" actually invalidates the
     entire `['crypto']` query cache (`:183`), broader than a scoped
     connectivity probe; low-stakes, not user-visible as a problem.

## Weight & Friction

- **Initial JS:** eager-loaded bundle = `index`, `rolldown-runtime`, `query`,
  `vendor` = **261,658 bytes raw / 80,855 bytes gzip**. All other 35 built
  chunks are route-level lazy chunks, not loaded on first paint. CSS adds a
  further 64,150 / 10,623 bytes (eager).
- **Network requests:** 37 total resource entries on first load, 13 of them
  `/api/*` calls.
- **Time-to-interactive:** `domContentLoadedEventEnd` 116.9ms,
  `loadEventEnd` 117.9ms, measured against a local warm uvicorn instance —
  not representative of a cold real-network load, method noted.
- **Idle animation: none found.** No `@keyframes`/`animation:` anywhere in
  `dashboard/src`. Every `animate-pulse`/`animate-spin` usage found
  (`CryptoExecutionPanel.tsx:223`, `ExecutionPanel.tsx:198`,
  `DhanAccountPanel.tsx:92`, `Button.tsx:80`, `StatsRail.tsx:146`) is gated on
  a pending/fetching/loading state, not default-on. No ambient background
  glow effect exists in current source (`theme.ts:5`'s own comment: "flat,
  one hairline, 12px radius, no shadow or glow") — worth a quick check
  against the "ambient glow" polish-pass memory, since the two disagree; not
  investigated further here as it's a non-issue either way (its absence is
  the *better* outcome for this principle).
- **Notifications on load: zero.** No toast fires on mount anywhere in
  `main.tsx`; every `toast()` call found is wired to a mutation's
  `onSuccess`/`onError`. Only persistent (non-dismissable) header state
  pills are visible at load: "Market closed", "PAPER", "Dhan OK".

## Accessibility

- **Contrast:** most text passes comfortably (body 18:1, headings 18.9:1,
  muted labels 7.5:1, P&L green/red 10.8:1/7.3:1). One clear **fail**: the
  *inactive* word inside the Paper/Live switch ("Paper" or "Live", whichever
  isn't currently selected) at **3.86:1**, below the 4.5:1 floor for its
  12px-bold size.
- **Focus order — a real, reproduced bug.** A `"Close menu"` element
  (`shell/Sidebar.tsx:31`, meant `md:hidden`, mobile-drawer-only) received
  keyboard focus at a **1440×900 desktop viewport**, where it should not be
  focusable at all. Separately, **repeating an identical 15-press Tab walk
  twice produced two different focus sequences** — confirmed non-deterministic,
  correlated with page interaction/re-render timing (an 8-second idle-with-no-keys
  control test showed focus staying stable, ruling out a pure background
  timer as the cause).
- **Keyboard reachability: good where tested.** Paper/Live toggle, lots
  +/- buttons, and the Close-position button are all real `<button>`
  elements, keyboard-focusable and (for the first two) confirmed operable
  via Enter — see the incident note below.
- **ARIA landmarks:** banner ×1, navigation ×3, main ×1, complementary ×2,
  contentinfo ×0 (no `<footer>` anywhere), region ×1 (toast host). No
  `search`/`form` landmarks (none needed on this surface).
- **Skip-link: absent.** First Tab press from a fresh load goes straight to
  the "Dashboard" nav button; no visually-hidden skip-to-content link exists
  in the accessibility tree.
- **Process note, not a design finding:** while confirming keyboard
  operability of the Crypto Paper/Live switch, the evidence-gathering agent
  actually toggled live crypto trading mode to LIVE for several seconds
  before reverting it — `live_armed` was never touched (confirmed false
  throughout by the orchestrator against the live `/api/crypto/status`
  response after the fact), so no order could have been placed, but this was
  a real, unauthorized state change on a live system caused by an
  imprecisely-scoped audit instruction. Not a finding about the *design*;
  recorded here for completeness since it happened during this audit.
