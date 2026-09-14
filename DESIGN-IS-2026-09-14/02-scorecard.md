# Scorecard — QuantHawk dashboard

Scored by the orchestrator only, against the evidence in `01-evidence.md`.
Tie-breaker rule applied throughout: when uncertain between two adjacent
scores, the lower one is used. Each principle is scored against its worst
representative instance on the audited surface, not an average.

1. Good design is innovative — **Score: 2/3**
   Evidence: the "advisory only" AI day-review framing and the
   confidence-ladder Strategy-P&L status badges (watching/observing/ready)
   are a genuine, deliberate refinement over the typical trading-dashboard
   pattern of presenting AI output as authoritative.
   Justification: this refreshes an existing category (AI trade summaries,
   backtest dashboards) with a real, considered improvement — honest framing
   most peers don't attempt — but it doesn't introduce a pattern unseen
   elsewhere; not a 3.

2. Good design is useful — **Score: 1/3**
   Evidence: the manual "Close position" control exists on exactly one of
   four structurally-identical open-position surfaces (Crypto only — see
   Structural evidence), directly short of a standing, explicitly-recorded
   product requirement that a manual override exist on every lane that can
   hold a position. The focus-order bug (a hidden mobile-only element
   focusable at desktop width; non-deterministic Tab sequence) adds real
   detours for a keyboard user on the primary task.
   Justification: the primary task (know what's happening, control it) is
   supported on the main screens, but a specifically-required control is
   missing on 3 of 4 lanes and keyboard navigation is measurably unreliable
   — more than "an adjacent surface adds steps," short of "not directly
   supported."

3. Good design is aesthetic — **Score: 1/3**
   Evidence: 14 distinct font sizes (11 of them within an 8-14px band in
   0.5-1px increments) and 34 hardcoded Tailwind color classes bypassing the
   11-token design system the branding work established.
   Justification: a real system exists and is mostly followed, but the
   violation count (34 colors, 14 type sizes) is far past "≤2 minor
   inconsistencies" — this is pervasive enough to cost a full point below
   "one jarring violation."

4. Good design is understandable — **Score: 1/3**
   Evidence: the word "friction" is still on screen in three places
   (`FuturesPanel.tsx`, `lib/strategies.ts` → `MyStrategiesView.tsx`) despite
   Richard directly telling a prior session he doesn't understand it and
   asking for concrete rupee costs instead — a standing instruction, not a
   generic style preference. A further list of ~10 unexplained
   acronyms/jargon terms (MTM, LTP, OOS Δ, PF, "Ratchet step", "Egress IP",
   etc.) sits alongside it.
   Justification: most controls are named plainly, but a specifically
   rejected word persisting on screen, plus a real jargon list beyond the
   "2-3 unclear" band, holds this at 1 rather than 2.

5. Good design is unobtrusive — **Score: 2/3**
   Evidence: zero idle/continuous animation anywhere in the app (Weight &
   Friction evidence); flat panels, single accent used sparingly. Against
   that: the same shared `TradeLogTable` sits one DOM level deeper on
   Futures/Commodities than on Crypto purely from an unnecessary extra
   wrapper `<section>`/`<div>`, and five independently-built stat-tile
   components add structural noise without visual noise.
   Justification: chrome stays visually quiet and content-first, but
   redundant wrapping and duplicated "chrome components" are still a
   present-but-quiet form of the interface asserting itself where it need
   not — short of a 3.

6. Good design is honest — **Score: 2/3**
   Evidence: zero dark patterns found across a full read of every
   user-facing string; the risk-direction asymmetry correctly favors safety;
   the app explicitly discloses its own strategies are "net-negative after
   real costs" in its own Marketplace tab. Against that: two "Deploy"
   buttons (Marketplace, Builder) don't deploy anything — the Builder one at
   least self-discloses this via its own click handler's toast.
   Justification: exceptionally honest overall — better than almost any
   comparable product on self-disclosure — but two labeled buttons that
   don't do what they say is more than the "≤1 minor inflation" band for a 3.

7. Good design is long-lasting — **Score: 3/3**
   Evidence: near-black ground, single gold accent, flat un-shadowed panels,
   Plus Jakarta Sans + Geist Mono — a considered brand choice (a real
   competitor teardown informed it) rather than a generated-default look
   (no warm-cream/terracotta, no generic SaaS card-shadow kit).
   Justification: no dated trend markers found in the visual language
   itself; the internal inconsistencies counted under aesthetic/#10 are
   about execution discipline, not about the design reading as tied to a
   passing trend.

8. Good design is thorough down to the last detail — **Score: 1/3**
   Evidence: the focus-order bug is a confirmed, reproduced thoroughness
   defect (an off-screen element reachable, a non-deterministic tab
   sequence). Error and success states on the Crypto tab were not
   confirmed present in this pass (data-availability limited what could be
   observed, not proof they're missing, but they could not be shown to
   exist either).
   Justification: a real, confirmed keyboard-state defect plus genuine
   uncertainty on 2 more states on the second-most-used screen holds this
   below "1 state missing or rough."

9. Good design is environmentally friendly — **Score: 3/3**
   Evidence: 80,855 bytes gzip of eager JS — under the 100KB bar — plus zero
   idle animation, zero autoplay, 37 total network requests on first paint.
   Justification: clears the top anchor's numeric bar on every measured
   dimension.

10. Good design is as little design as possible — **Score: 1/3**
    Evidence: two entire dead files (`ui/Tabs.tsx`, `PositionsPanel.tsx`),
    two dead exported components (`ButtonGroup`, `ToggleButton`), five
    independent reimplementations of the same stat-tile pattern, five
    independent implementations of rupee-formatting logic, two
    near-identical hand-copied Paper/Live toggle implementations where one
    shared component would do.
    Justification: well past the "3-5 removable elements" band once dead
    files, dead exports, and every duplicated pattern are counted — the app
    isn't dominated by decoration, so not a 0, but the redundancy is
    substantial and systemic, not marginal.

**Total: 2+1+1+1+2+2+3+1+3+1 = 17/30**
