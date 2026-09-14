/** Map Delta-perp journal rows (crypto/journal) into the index dashboard's
 *  TradeRow / LogRow shapes, so the Reports and Trade-history pages can show
 *  crypto trades next to index trades. The crypto journal holds closed
 *  round-trips only — every mapped row is `is_open: false`. */

import type { LogRow, TradeRow } from '../types/analytics'

export type CryptoJournalRow = {
  exit_id?: string
  strategy?: string
  asset?: string
  mode?: string
  side?: string
  size?: number
  entry_price?: number
  exit_price?: number
  opened_at?: string
  entry_time?: string
  exit_time?: string
  closed_at?: string
  pnl_inr?: number | null
  pnl_usd?: number | null
  exit_reason?: string
}

export type DailyPoint = { period: string; pnl_rupees: number }

const istDisplay = (iso?: string): string | undefined => {
  if (!iso) return undefined
  const t = Date.parse(String(iso).replace(' ', 'T').replace('Z', '+00:00'))
  if (Number.isNaN(t)) return undefined
  return new Date(t).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', hour12: true })
}

const openedIso = (r: CryptoJournalRow) => r.opened_at || r.entry_time
const rowId = (r: CryptoJournalRow) => `crypto:${r.exit_id || `${r.asset}:${openedIso(r)}`}`
const coin = (asset?: string) => String(asset || '—').replace(/USD$/i, '')

export function cryptoToTradeRows(rows: CryptoJournalRow[]): TradeRow[] {
  return rows.map((r) => ({
    id: rowId(r),
    created_at: openedIso(r),
    created_at_ist: istDisplay(openedIso(r)),
    closed_at_ist: istDisplay(r.closed_at || r.exit_time) ?? null,
    instrument: coin(r.asset),
    action: `${(r.side || '').toUpperCase()} · ${r.strategy || 'crypto'}`,
    lot_label: r.size != null ? `${r.size} lot` : undefined,
    pnl: r.pnl_inr ?? null,
    is_open: false,
    entry_session_ok: true,
    status: 'CLOSED',
    display_status: 'Closed',
  }))
}

export function cryptoToLogRows(rows: CryptoJournalRow[]): LogRow[] {
  return rows.map((r) => ({
    trade_id: rowId(r),
    leg_index: 0,
    leg_count: 1,
    open_time_ist: istDisplay(openedIso(r)),
    close_time_ist: istDisplay(r.closed_at || r.exit_time) ?? null,
    instrument: coin(r.asset),
    strategy: r.strategy,
    side: (r.side || '').toLowerCase() === 'short' ? 'Sell' : 'Buy',
    quantity: r.size,
    avg_entry: r.entry_price ?? null,
    avg_exit: r.exit_price ?? null,
    leg_pnl: r.pnl_inr ?? null,
    display_pnl: r.pnl_inr ?? null,
    spread_pnl: r.pnl_inr ?? null,
    is_open: false,
    status: 'Closed',
    display_status: r.exit_reason || 'Closed',
    mode: r.mode,
    quote_ccy: 'USD',
  }))
}

/** Realised P&L per UTC calendar day from a trade list — the client-side
 *  equivalent of the server's `daily_series` (used for the equity curve and
 *  P&L calendar when crypto trades are in view). Newest day first. */
export function dailySeriesFromTrades(trades: TradeRow[]): DailyPoint[] {
  const byDay = new Map<string, number>()
  for (const t of trades) {
    if (t.pnl == null || !t.created_at) continue
    const key = String(t.created_at).slice(0, 10)
    if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) continue
    byDay.set(key, (byDay.get(key) ?? 0) + Number(t.pnl || 0))
  }
  return [...byDay.entries()]
    .sort(([a], [b]) => (a < b ? 1 : -1))
    .map(([period, pnl_rupees]) => ({ period, pnl_rupees }))
}

export function mergeDailySeries(a: DailyPoint[], b: DailyPoint[]): DailyPoint[] {
  const m = new Map<string, number>()
  for (const p of [...a, ...b]) {
    m.set(p.period, (m.get(p.period) ?? 0) + (Number(p.pnl_rupees) || 0))
  }
  return [...m.entries()]
    .sort(([a2], [b2]) => (a2 < b2 ? 1 : -1))
    .map(([period, pnl_rupees]) => ({ period, pnl_rupees }))
}

/** Minimal client-side CSV for the crypto / combined trade log — the server's
 *  /api/reports/export is index-only. */
export function logRowsToCsv(rows: LogRow[]): string {
  const head = ['Open', 'Close', 'Mode', 'Instrument', 'Side', 'Qty', 'Entry', 'Exit', 'PnL', 'Status']
  const cell = (v: unknown) => {
    const s = v == null ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const body = rows.map((r) =>
    [
      r.open_time_ist,
      r.close_time_ist,
      r.mode,
      r.instrument,
      r.side,
      r.quantity,
      r.avg_entry,
      r.avg_exit,
      r.leg_pnl ?? r.display_pnl,
      r.display_status ?? r.status,
    ]
      .map(cell)
      .join(','),
  )
  return [head.join(','), ...body].join('\n')
}
