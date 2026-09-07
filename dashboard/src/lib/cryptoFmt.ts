/** USD/INR formatters for the crypto tab. The shared lib/pnl.ts money() is
 *  rupee-only; crypto quotes in USD with an INR conversion leg, so it needs
 *  its own. Colours come from the --up / --down CSS vars (see index.css). */

export const ok = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

export const num = (v: number | null | undefined, d = 2) =>
  ok(v) ? v.toLocaleString(undefined, { maximumFractionDigits: d }) : '—'

/** Signed USD, e.g. "+$12.34" / "−$5.00". */
export const usd = (v: number | null | undefined) =>
  ok(v)
    ? `${v >= 0 ? '+' : '−'}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
    : '—'

/** Signed INR, whole rupees. */
export const inr = (v: number | null | undefined) =>
  ok(v)
    ? `${v >= 0 ? '+' : '−'}₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
    : '—'

export const pnlCls = (v: number | null | undefined) =>
  ok(v) && v > 0
    ? 'text-[var(--up)]'
    : ok(v) && v < 0
      ? 'text-[var(--down)]'
      : 'text-slate-400'
