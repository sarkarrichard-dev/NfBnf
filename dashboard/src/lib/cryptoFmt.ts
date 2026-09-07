/** USD/INR formatters for the crypto tab. The shared lib/pnl.ts money() is
 *  rupee-only; crypto quotes in USD with an INR conversion leg, so it needs
 *  its own. Colours come from the --up / --down CSS vars (see index.css). */

import { istDayBoundsMs, istMonthStartMs, istRangeBoundsMs, istWeekStartMs } from './ist'
import type { DateRange, PeriodKey } from '../types/analytics'

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

/** Unsigned USD, 2dp, e.g. "$20.40" — for cost/margin cells, not P&L. */
export const usd0 = (v: number | null | undefined) =>
  ok(v) ? `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : '—'

/** Unsigned INR, whole rupees, e.g. "₹1,795". */
export const inr0 = (v: number | null | undefined) =>
  ok(v) ? `₹${v.toLocaleString('en-IN', { maximumFractionDigits: 0 })}` : '—'

export const pnlCls = (v: number | null | undefined) =>
  ok(v) && v > 0
    ? 'text-[var(--up)]'
    : ok(v) && v < 0
      ? 'text-[var(--down)]'
      : 'text-slate-400'

export type CryptoTrade = {
  day: string
  closed_at?: string
  exit_time?: string
  pnl_usd: number
  pnl_inr: number
}

/** Closed crypto trades within the selected period / custom range.
 *  Keys on closed_at (ISO UTC), else exit_time, else the `day` string. */
export function cryptoRowsForPeriod<T extends CryptoTrade>(
  rows: T[],
  period: PeriodKey,
  range?: DateRange,
): T[] {
  if (period === 'all') return rows
  if (period === 'custom' && !(range?.from && range?.to)) return rows

  let start = 0
  let end = Number.POSITIVE_INFINITY
  if (period === 'today') {
    const b = istDayBoundsMs()
    start = b.start
    end = b.end
  } else if (period === 'week') {
    start = istWeekStartMs()
  } else if (period === 'month') {
    start = istMonthStartMs()
  } else if (period === 'custom' && range) {
    const b = istRangeBoundsMs(range.from, range.to)
    start = b.start
    end = b.end
  }

  return rows.filter((r) => {
    const raw = r.closed_at || r.exit_time || (r.day ? `${r.day}T12:00:00+05:30` : '')
    const ts = Date.parse(String(raw).replace('Z', '+00:00'))
    if (Number.isNaN(ts)) return false
    return ts >= start && ts < end
  })
}

export function cryptoStats(rows: CryptoTrade[]) {
  const wins = rows.filter((r) => Number(r.pnl_usd) > 0).length
  const losses = rows.filter((r) => Number(r.pnl_usd) < 0).length
  return {
    trades: rows.length,
    wins,
    losses,
    win_rate: rows.length ? wins / rows.length : null,
    net_usd: rows.reduce((s, r) => s + Number(r.pnl_usd || 0), 0),
    net_inr: rows.reduce((s, r) => s + Number(r.pnl_inr || 0), 0),
  }
}
