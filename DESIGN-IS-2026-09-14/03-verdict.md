# Verdict — QuantHawk dashboard

**Total: 17/30. No principle scored 0. Verdict: REDESIGN**, by the
mechanical rule (total < 20 triggers REDESIGN regardless of whether any
single principle hit 0 — see "Verdict commitment" in the audit method).

**One sentence:** QuantHawk's dashboard has a real, deliberate, well-chosen
design foundation — an honest voice, a genuine brand, a lean bundle — but it
is executed inconsistently enough (a fragmented type scale, a third of its
colors bypassing its own token system, five reimplementations of the same
card, a missing standing-required control on three of four lanes, a rejected
word still on screen, a reproducible keyboard-navigation bug) that the
numeric threshold calls for a redesign pass, not a handful of isolated
patches.

**Read this REDESIGN narrowly — that's what the evidence actually supports.**
Nothing here says the information architecture, the tab structure, the
brand, or the product's honest voice are wrong — those all scored well (#6,
#7) or are preserved wholesale below. What scored low is *execution
discipline*: the same four or five patterns (a stat tile, a money formatter,
a mode toggle, a manual close action) were rebuilt by hand each time instead
of shared once, and that same instinct — solve it locally instead of finding
the existing solution — is what let 34 hardcoded colors, 14 font sizes, and
one specifically-rejected word slip past review. The fix is systemic
(a shared-component pass, a token-usage lint, a jargon sweep) rather than
a screen-by-screen restyle, which is exactly what a REDESIGN of the
*component layer* — not the product — should target.

## Top 5 highest-leverage moves

1. **#10 (as little design as possible) — Unify the five duplicated
   patterns into one shared component each.** Evidence: `01-evidence.md`
   Structural section — the stat tile (5 reimplementations), the money
   formatter (5 reimplementations), the Paper/Live toggle (2 hand-copied
   implementations), plus deleting 2 confirmed-dead files
   (`ui/Tabs.tsx`, `PositionsPanel.tsx`) and 2 dead exports (`ButtonGroup`,
   `ToggleButton`).

2. **#2 (useful) — Add the manual "Close position" control to the three
   lanes that don't have it.** Evidence: Structural section — Journal,
   Lanes (futures), and Commodities open-position lists have no close
   action; only Crypto does. This isn't a new idea, it's finishing a
   standing, already-recorded product requirement.

3. **#4 (understandable) — Remove "friction" from every screen it appears
   on, and do a pass on the remaining acronym list.** Evidence: Copy &
   Honesty section — `FuturesPanel.tsx:67,186,214` and
   `lib/strategies.ts:159`. Replace with the concrete rupee-charge phrasing
   already used correctly elsewhere in the app (`CommoditiesPanel.tsx:196-199`
   is the model to copy). Also expand or replace MTM, LTP, OOS Δ, PF,
   "Ratchet step," "Egress IP" with plain equivalents (specific
   replacements proposed in the Copy & Honesty evidence).

4. **#3 (aesthetic) — Collapse the type scale and enforce the color
   tokens.** Evidence: Visual section — 14 sizes down to the existing
   named Tailwind scale (xs/sm/base/lg/2xl, dropping the ~10 arbitrary
   sub-pixel sizes), and replace the 34 hardcoded `emerald-*`/`red-*`/
   `amber-*`/`rose-*` classes with the existing `--up`/`--down`/`--warn`/
   `--armed` tokens they already have a 1:1 equivalent for.

5. **#8 (thorough) — Fix the two confirmed accessibility defects.**
   Evidence: Accessibility section — the `md:hidden` "Close menu" element
   focusable at desktop width (a CSS/markup bug, not a design decision),
   and the non-deterministic Tab order (needs investigation into what
   re-render is stealing focus mid-walk). Also lift the failing 3.86:1
   inactive-toggle-label contrast to the token system's existing
   higher-contrast muted color.

## Anti-patterns checked against and rejected

- Not recommending REDESIGN because one screen looked bad — the findings
  span the shared component layer used by every screen, and the specific
  worst offenders (Structural, Visual) are itemized with file:line evidence
  above, not a vague "it feels inconsistent."
- Not softening to REFINE because the codebase is large or because much of
  it (honesty, bundle weight, brand voice) scored well — the rule is
  mechanical on the total, and re-scoring to dodge the number would be the
  exact inflation the audit method warns against.
- Not treating this as license to touch the tab structure, the brand
  palette, or the product's voice — none of those are in the Discard list
  in the handoff below.
