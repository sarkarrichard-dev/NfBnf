# Scope — QuantHawk dashboard, Dieter Rams audit

**Date:** 2026-09-14

**Audited surface:** the whole QuantHawk dashboard (`dashboard/`, a React 19 +
Vite + Tailwind v4 SPA served by FastAPI at `http://127.0.0.1:8000`), across
every tab: Dashboard (India), Strategies, Crypto, Futures, Commodities, and
the Insights group (Strategy P&L, Trade history, Reports & PnL), plus
Research and Settings.

**Primary user:** Richard, the platform's operator and sole trader today.
He is not a technical person (established preference: explain everything in
plain language, no jargon). The platform is headed for subscription
distribution to other traders later, but this audit scores it against
today's actual user, not the future one.

**Primary task:** know, at a glance, what the algo is doing and why — is it
running, is it Paper or Live, what's open, what closed and for how much, and
(when something looks wrong) why a trade was or wasn't taken. Secondary
tasks: switch Paper/Live, adjust lot size and strategy toggles, review
historical performance per strategy/instrument.

**Constraints:**
- Existing brand system already committed: hawk-gold accent (`#f2a63c`),
  near-black ground, Plus Jakarta Sans (UI text) + Geist Mono (all numbers),
  tokenised in `dashboard/src/index.css`. Not a blank slate — four "polish
  pass" commits already landed this session (ambient glow, hero P&L numbers,
  designed empty states, a shared `EmptyState` component).
- Must work down to ~400px width (phone).
- No design budget for a new component library — must build on the existing
  Tailwind + CSS-custom-property system.
- This is a live, real-money-adjacent tool (Paper/Live trading switch, kill
  switches, live-arming confirmation) — clarity and honesty carry more
  weight here than on a typical SaaS dashboard.

**Reference designs:** `web.cryptomaty.com` was already torn down as a
competitor reference (a "Cryptomaty Teardown" artifact exists) — flat white/
near-black cards, no shadow, Plus Jakarta Sans + Geist Mono, one accent. That
teardown already shaped the current design system, so this audit measures
QuantHawk against its *own* stated direction, not a fresh competitor lens.

**Known prior work (context, not evidence):** dashboard-lag-diagnosis and
dashboard-ux-expectations describe a history of responsiveness complaints
(now fixed — blocking I/O off the event loop, patch-not-invalidate query
cache) and specific asks (sliding toggle for Paper/Live, confirm modal not a
typed phrase, standardized `TradeLogTable` across every section — the last
of these just shipped today, PR #94). The audit below re-measures the
current state; it does not assume these fixes hold without evidence.
