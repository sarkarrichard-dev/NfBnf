import type { LogRow, PeriodKey, PeriodStats, TradeRow } from '../types/analytics'

export function money(v?: number | null): string {
  if (v == null || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  const sign = n >= 0 ? '+' : ''
  return `${sign}₹${n.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
}

export function pctRate(v?: number | null): string {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return `${(Number(v) * 100).toFixed(1)}%`
}

export function formatPrice(v?: number | null): string {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
}

export function logRowKey(row: LogRow): string {
  return `${row.trade_id}:${row.leg_index ?? 0}`
}

export function parseIstTime(value?: string | null): number {
  if (!value) return 0
  const parsed = Date.parse(String(value).replace(/,/g, ''))
  return Number.isNaN(parsed) ? 0 : parsed
}

export function sortLogRowsStable(rows: LogRow[]): LogRow[] {
  return [...rows].sort((a, b) => {
    if (a.is_open !== b.is_open) return a.is_open ? -1 : 1
    const tb = parseIstTime(b.open_time_ist)
    const ta = parseIstTime(a.open_time_ist)
    if (tb !== ta) return tb - ta
    const idCmp = String(a.trade_id || '').localeCompare(String(b.trade_id || ''))
    if (idCmp !== 0) return idCmp
    return (a.leg_index || 0) - (b.leg_index || 0)
  })
}

function mergeLogRow(prev: LogRow | undefined, next: LogRow): LogRow {
  if (!prev || !next.is_open) return next
  return {
    ...next,
    leg_mtm: next.leg_mtm != null ? next.leg_mtm : prev.leg_mtm,
    mark_price: next.mark_price != null ? next.mark_price : prev.mark_price,
    display_pnl: next.display_pnl != null ? next.display_pnl : prev.display_pnl,
    mtm_updated_at_ist: next.mtm_updated_at_ist || prev.mtm_updated_at_ist,
    avg_entry: next.avg_entry != null ? next.avg_entry : prev.avg_entry,
  }
}

export function mergeLogRows(prevRows: LogRow[], newRows: LogRow[]): LogRow[] {
  if (!newRows.length) return prevRows
  const prevList = prevRows || []
  const prevMap = Object.fromEntries(prevList.map((r) => [logRowKey(r), r]))
  const newMap = Object.fromEntries(newRows.map((r) => [logRowKey(r), r]))
  const order: LogRow[] = []
  const seen = new Set<string>()

  for (const row of prevList) {
    const key = logRowKey(row)
    if (newMap[key]) {
      order.push(mergeLogRow(prevMap[key], newMap[key]))
      seen.add(key)
    }
  }
  for (const row of newRows) {
    const key = logRowKey(row)
    if (!seen.has(key)) {
      order.push(row)
      seen.add(key)
    }
  }
  return sortLogRowsStable(order)
}

export function mergeTradesLiveMtm(prev: TradeRow[], live: TradeRow[]): TradeRow[] {
  if (!live.length) return prev
  const byId = Object.fromEntries(live.map((r) => [r.id, r]))
  return prev.map((t) => {
    const u = byId[t.id]
    if (!u) return t
    return {
      ...t,
      ...u,
      mtm_pnl: u.mtm_pnl != null ? u.mtm_pnl : t.mtm_pnl,
      mtm_updated_at_ist: u.mtm_updated_at_ist || t.mtm_updated_at_ist,
      current_option_ltp: u.current_option_ltp ?? t.current_option_ltp,
      legs_detail: u.legs_detail?.length ? u.legs_detail : t.legs_detail,
      mtm_error: u.mtm_error ?? t.mtm_error,
    }
  })
}

export function rowOpenMtm(row: LogRow): number | null {
  if (!row.is_open) return null
  if (row.leg_mtm != null) return Number(row.leg_mtm)
  if (row.display_pnl != null && Number(row.leg_index || 0) === 0) {
    return Number(row.display_pnl)
  }
  return null
}

export function legPnlValue(row: LogRow): number | null {
  const mtm = rowOpenMtm(row)
  if (mtm != null) return mtm
  if (row.leg_pnl != null) return Number(row.leg_pnl)
  if (row.display_pnl != null) return Number(row.display_pnl)
  return null
}

export function legDisplayName(row: LogRow): string {
  const parts: string[] = []
  if (row.instrument) parts.push(String(row.instrument).toUpperCase())
  if (row.expiry) parts.push(String(row.expiry).toUpperCase())
  if (row.strike != null && row.strike !== '—') parts.push(String(row.strike))
  if (row.option_type) parts.push(String(row.option_type).toUpperCase())
  if (parts.length >= 2) return parts.join(' ')
  return `${row.instrument || '—'} ${row.side || ''} ${row.strike || ''} ${row.option_type || ''}`.trim()
}

export function signedQty(row: LogRow): number {
  const q = Number(row.quantity) || 0
  return row.side === 'Sell' ? -Math.abs(q) : Math.abs(q)
}

export function legPctChange(row: LogRow): number | null {
  const entry = Number(row.avg_entry)
  const ltp = Number(row.mark_price)
  if (!entry || !ltp || Number.isNaN(entry) || Number.isNaN(ltp)) return null
  if (row.side === 'Sell') return ((entry - ltp) / entry) * 100
  return ((ltp - entry) / entry) * 100
}

export function tradesForPeriod(
  trades: TradeRow[],
  period: PeriodKey,
): TradeRow[] {
  if (period === 'all') return trades
  const now = new Date()
  let startMs = 0
  if (period === 'today') {
    const ist = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }))
    ist.setHours(0, 0, 0, 0)
    startMs = ist.getTime()
  } else if (period === 'week') {
    const ist = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }))
    const day = ist.getDay()
    const diff = day === 0 ? 6 : day - 1
    ist.setDate(ist.getDate() - diff)
    ist.setHours(0, 0, 0, 0)
    startMs = ist.getTime()
  } else if (period === 'month') {
    const ist = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }))
    ist.setDate(1)
    ist.setHours(0, 0, 0, 0)
    startMs = ist.getTime()
  }
  return trades.filter((t) => {
    const ts = new Date(String(t.created_at || '').replace('Z', '+00:00')).getTime()
    return ts >= startMs
  })
}

export function logRowsForPeriod(
  logRows: LogRow[],
  trades: TradeRow[],
  period: PeriodKey,
): LogRow[] {
  const tradeIds = new Set(tradesForPeriod(trades, period).map((t) => t.id))
  return sortLogRowsStable(logRows.filter((r) => tradeIds.has(String(r.trade_id || ''))))
}

export function openLegRows(
  logRows: LogRow[],
  trades: TradeRow[],
  period: PeriodKey,
): LogRow[] {
  return logRowsForPeriod(logRows, trades, period).filter((r) => r.is_open)
}

export function periodBlock(
  analytics: {
    today?: PeriodStats
    week?: PeriodStats
    month?: PeriodStats
    overview?: PeriodStats
  } | null,
  period: PeriodKey,
): PeriodStats {
  if (!analytics) {
    return {
      trades: 0,
      closed: 0,
      open: 0,
      wins: 0,
      losses: 0,
      win_rate: null,
      pnl_rupees: 0,
    }
  }
  if (period === 'today') return analytics.today || analytics.overview || {}
  if (period === 'week') return analytics.week || analytics.overview || {}
  if (period === 'month') return analytics.month || analytics.overview || {}
  return analytics.overview || {}
}

export function pnlClass(v?: number | null): string {
  if (v == null) return 'text-slate-500'
  if (v > 0) return 'text-emerald-400'
  if (v < 0) return 'text-red-400'
  return 'text-slate-400'
}
