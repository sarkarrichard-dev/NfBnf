import { useMemo } from 'react'
import { cn } from '../lib/cn'
import {
  formatPrice,
  logRowKey,
  logRowsForPeriod,
  money,
  pnlClass,
  rowOpenMtm,
} from '../lib/pnl'
import { fx } from '../lib/theme'
import type { DateRange, LogRow, PeriodKey, TradeRow } from '../types/analytics'

type Props = {
  logRows: LogRow[]
  trades: TradeRow[]
  period: PeriodKey
  range?: DateRange
  mtmUpdatedAt?: string
  hideTitle?: boolean
}

const INDEX_ORDER: Record<string, number> = { NIFTY: 0, BANKNIFTY: 1, SENSEX: 2 }

function groupByIndex(rows: LogRow[]) {
  const groups = new Map<string, LogRow[]>()
  for (const row of rows) {
    const key = String(row.instrument || 'UNKNOWN').toUpperCase()
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(row)
  }
  return [...groups.entries()]
    .sort(
      ([a], [b]) =>
        (INDEX_ORDER[a] ?? 99) - (INDEX_ORDER[b] ?? 99) || a.localeCompare(b),
    )
    .map(([instrument, groupRows]) => ({ instrument, rows: groupRows }))
}

export function TradeLogTable({ logRows, trades, period, range, mtmUpdatedAt, hideTitle }: Props) {
  const rows = useMemo(
    () => logRowsForPeriod(logRows, trades, period, range),
    [logRows, trades, period, range],
  )

  const groups = useMemo(() => groupByIndex(rows), [rows])

  if (!rows.length) {
    const tradeCount = trades.length
    return (
      <section className={cn(fx.panel, 'p-4')}>
        {!hideTitle ? <h2 className="mb-2 text-base font-semibold text-cyan-50/95">Trade log</h2> : null}
        <div className="flex min-h-24 items-center justify-center rounded-lg border border-dashed border-[var(--hair)] bg-white/[0.01] px-4 py-6 text-center">
          <p className="text-sm text-cyan-200/45">
            {tradeCount > 0
              ? 'Loading leg details…'
              : 'No trades closed in this period yet — try a wider range, or check back once the strategy fires.'}
          </p>
        </div>
      </section>
    )
  }

  return (
    <section className={cn(fx.panel, 'p-4')}>
      {!hideTitle ? (
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-base font-semibold text-cyan-50/95">Trade log</h2>
          <p className="text-xs text-cyan-200/45">
            One row per option leg
            {mtmUpdatedAt ? ` · MTM ${mtmUpdatedAt}` : ''}
          </p>
        </div>
      ) : (
        <p className="mb-3 text-xs text-cyan-200/45">
          Closed and open legs in this period
          {mtmUpdatedAt ? ` · MTM ${mtmUpdatedAt}` : ''}
        </p>
      )}
      <div className="max-h-[28rem] overflow-auto rounded-lg border border-cyan-500/15 bg-black/25">
        <table className="min-w-full text-sm">
          <thead className="sticky top-0 z-10 bg-slate-950/95 text-xs text-cyan-200/50">
            <tr className="border-b border-slate-800">
              <th className="px-2 py-2 text-left font-medium">Open</th>
              <th className="px-2 py-2 text-left font-medium">Close</th>
              <th className="px-2 py-2 text-left font-medium">Mode</th>
              <th className="px-2 py-2 text-left font-medium">Index</th>
              <th className="px-2 py-2 text-left font-medium">Side</th>
              <th className="px-2 py-2 text-left font-medium">Strike</th>
              <th className="px-2 py-2 text-left font-medium">Type</th>
              <th className="px-2 py-2 text-right font-medium">Qty</th>
              <th className="px-2 py-2 text-right font-medium">Capital</th>
              <th className="px-2 py-2 text-right font-medium">Entry</th>
              <th className="px-2 py-2 text-right font-medium">Mark</th>
              <th className="px-2 py-2 text-right font-medium">MTM</th>
              <th className="px-2 py-2 text-right font-medium">PnL</th>
              <th className="px-2 py-2 text-left font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => {
              const openMtm = group.rows.reduce((s, r) => s + (rowOpenMtm(r) ?? 0), 0)
              const realized = group.rows.reduce((s, r) => {
                if (r.leg_pnl != null) return s + Number(r.leg_pnl)
                if (r.spread_pnl != null && (r.leg_index ?? 0) === 0) {
                  return s + Number(r.spread_pnl)
                }
                return s
              }, 0)
              const total = openMtm + realized
              return (
                <GroupBlock
                  key={group.instrument}
                  instrument={group.instrument}
                  rows={group.rows}
                  openMtm={openMtm}
                  realized={realized}
                  total={total}
                />
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function GroupBlock({
  instrument,
  rows,
  openMtm,
  realized,
  total,
}: {
  instrument: string
  rows: LogRow[]
  openMtm: number
  realized: number
  total: number
}) {
  const openLegs = rows.filter((r) => r.is_open).length
  return (
    <>
      <tr className="bg-slate-950/70 text-xs text-slate-400">
        <td colSpan={14} className="px-3 py-2">
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <strong className="text-slate-200">{instrument}</strong>
            <span>{rows.length} legs</span>
            <span>{openLegs} open</span>
            <span>Realized {money(realized)}</span>
            <span>MTM {money(openMtm)}</span>
            <span className={pnlClass(total)}>Total {money(total)}</span>
          </div>
        </td>
      </tr>
      {rows.map((row) => (
        <LegRow key={logRowKey(row)} row={row} />
      ))}
    </>
  )
}

function LegRow({ row }: { row: LogRow }) {
  const isRejected = row.status === 'LIVE_REJECTED'
  const sideCls =
    row.side === 'Sell'
      ? 'text-red-300 border-red-500/30 bg-red-500/10'
      : 'text-emerald-300 border-emerald-500/30 bg-emerald-500/10'
  const mtm = rowOpenMtm(row)
  const pnl =
    row.leg_pnl != null
      ? Number(row.leg_pnl)
      : row.spread_pnl != null && (row.leg_index ?? 0) === 0
        ? Number(row.spread_pnl)
        : !row.is_open && row.display_pnl != null && (row.leg_index ?? 0) === 0
          ? Number(row.display_pnl)
          : null

  const px = (v?: number | null) =>
    row.quote_ccy === 'USD'
      ? v == null || Number.isNaN(Number(v))
        ? '—'
        : `$${Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 })}`
      : formatPrice(v)

  const mark =
    row.is_open && row.mark_price != null
      ? `${px(row.mark_price)} live`
      : row.avg_exit != null
        ? px(row.avg_exit)
        : row.mark_price != null
          ? px(row.mark_price)
          : '—'

  return (
    <tr
      className={cn(
        'border-b border-slate-800/60 text-slate-200',
        row.is_open && 'bg-emerald-500/[0.03]',
        isRejected && 'opacity-80',
      )}
    >
      <td className="whitespace-nowrap px-2 py-2 text-xs">{row.open_time_ist || '—'}</td>
      <td className="whitespace-nowrap px-2 py-2 text-xs">{row.close_time_ist || '—'}</td>
      <td className="px-2 py-2">
        <span className="rounded border border-slate-700 px-1.5 py-0.5 text-[11px]">
          {row.mode || '—'}
        </span>
      </td>
      <td className="px-2 py-2">{row.instrument || '—'}</td>
      <td className="px-2 py-2">
        <span className={cn('rounded border px-1.5 py-0.5 text-[11px]', sideCls)}>
          {row.side || '—'}
        </span>
      </td>
      <td className="px-2 py-2 tabular-nums">{row.strike ?? '—'}</td>
      <td className="px-2 py-2">{row.option_type || '—'}</td>
      <td className="px-2 py-2 text-right tabular-nums">{row.quantity ?? '—'}</td>
      <td className="px-2 py-2 text-right tabular-nums text-slate-300">
        {row.capital_deployed != null ? (
          <span title={row.capital_kind === 'margin' ? 'combined margin at risk (sell + hedge)' : 'premium paid'}>
            ₹{Math.round(Number(row.capital_deployed)).toLocaleString('en-IN')}
            <span className="ml-1 text-[9px] uppercase text-slate-500">
              {row.capital_kind === 'margin' ? 'marg' : 'prem'}
            </span>
          </span>
        ) : (
          '—'
        )}
      </td>
      <td className="px-2 py-2 text-right tabular-nums">{px(row.avg_entry)}</td>
      <td className="px-2 py-2 text-right tabular-nums">{mark}</td>
      <td className={cn('px-2 py-2 text-right tabular-nums font-semibold', pnlClass(mtm))}>
        {mtm != null ? money(mtm) : '—'}
      </td>
      <td className={cn('px-2 py-2 text-right tabular-nums', pnlClass(pnl))}>
        {pnl != null ? money(pnl) : '—'}
      </td>
      <td className="px-2 py-2 text-xs">
        <span className={isRejected ? 'font-semibold text-red-400' : ''}>
          {row.display_status || row.status || '—'}
        </span>
      </td>
    </tr>
  )
}
