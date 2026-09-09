/** Map futures paper-lane journal rows (trade-shaped, one instrument per trade —
 *  index or stock futures) into the index dashboard's TradeRow / LogRow shapes,
 *  so the Reports and Trade-history pages can show them under the Futures source. */

import type { LogRow, TradeRow } from '../types/analytics'

export type FuturesJournalRow = {
  instrument?: string
  mode?: string
  direction?: string // LONG | SHORT
  lot_size?: number
  entry_time?: string
  entry?: number
  exit_time?: string
  exit?: number
  exit_reason?: string
  points?: number
  gross_rupees?: number
  friction_rupees?: number
  net_rupees?: number | null
}

const istDisplay = (iso?: string): string | undefined => {
  if (!iso) return undefined
  const t = Date.parse(String(iso).replace(' ', 'T').replace('Z', '+00:00'))
  if (Number.isNaN(t)) return undefined
  return new Date(t).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', hour12: true })
}

const rowId = (r: FuturesJournalRow) =>
  `futures:${r.instrument || '?'}:${r.entry_time || r.exit_time || ''}`

export function futuresToTradeRows(rows: FuturesJournalRow[]): TradeRow[] {
  return rows.map((r) => ({
    id: rowId(r),
    created_at: r.entry_time,
    created_at_ist: istDisplay(r.entry_time),
    closed_at_ist: istDisplay(r.exit_time) ?? null,
    instrument: r.instrument,
    action: `${(r.direction || '').toUpperCase()} FUT`,
    lot_label: r.lot_size != null ? `${r.lot_size} qty` : undefined,
    pnl: r.net_rupees ?? null,
    is_open: false,
    entry_session_ok: true,
    status: 'CLOSED',
    display_status: 'Closed',
    capital_deployed:
      r.entry != null && r.lot_size != null ? Math.round(r.entry * r.lot_size) : null,
    capital_kind: 'margin',
  }))
}

export function futuresToLogRows(rows: FuturesJournalRow[]): LogRow[] {
  return rows.map((r) => ({
    trade_id: rowId(r),
    leg_index: 0,
    leg_count: 1,
    open_time_ist: istDisplay(r.entry_time),
    close_time_ist: istDisplay(r.exit_time) ?? null,
    instrument: r.instrument,
    side: (r.direction || '').toUpperCase() === 'SHORT' ? 'Sell' : 'Buy',
    quantity: r.lot_size,
    avg_entry: r.entry ?? null,
    avg_exit: r.exit ?? null,
    leg_pnl: r.net_rupees ?? null,
    display_pnl: r.net_rupees ?? null,
    spread_pnl: r.net_rupees ?? null,
    capital_deployed:
      r.entry != null && r.lot_size != null ? Math.round(r.entry * r.lot_size) : null,
    capital_kind: 'margin',
    is_open: false,
    status: 'Closed',
    display_status: r.exit_reason || 'Closed',
    mode: r.mode === 'PAPER' ? 'Paper' : r.mode,
  }))
}
