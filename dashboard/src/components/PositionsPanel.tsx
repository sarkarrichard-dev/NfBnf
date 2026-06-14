import { useMemo } from 'react'
import { cn } from '../lib/cn'
import {
  formatPrice,
  legDisplayName,
  legPctChange,
  legPnlValue,
  money,
  openLegRows,
  pnlClass,
  signedQty,
} from '../lib/pnl'
import type { LogRow, PeriodKey, PositionsFilter, TradeRow } from '../types/analytics'

type Props = {
  logRows: LogRow[]
  trades: TradeRow[]
  period: PeriodKey
  filter: PositionsFilter
  onFilterChange: (filter: PositionsFilter) => void
  mtmUpdatedAt?: string
}

function passesFilter(row: LogRow, filter: PositionsFilter): boolean {
  const pnl = legPnlValue(row)
  if (filter === 'profit') return pnl != null && pnl > 0
  if (filter === 'loss') return pnl != null && pnl < 0
  return true
}

export function PositionsPanel({
  logRows,
  trades,
  period,
  filter,
  onFilterChange,
  mtmUpdatedAt,
}: Props) {
  const allOpen = useMemo(
    () => openLegRows(logRows, trades, period),
    [logRows, trades, period],
  )

  const visible = useMemo(
    () => allOpen.filter((row) => passesFilter(row, filter)),
    [allOpen, filter],
  )

  const totalPnl = useMemo(
    () => visible.reduce((s, r) => s + (legPnlValue(r) ?? 0), 0),
    [visible],
  )

  const filters: { id: PositionsFilter; label: string }[] = [
    {
      id: 'all',
      label: 'Open',
    },
    {
      id: 'profit',
      label: 'In profit',
    },
    {
      id: 'loss',
      label: 'In loss',
    },
  ]

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-slate-100">Today&apos;s positions</h2>
          <p className="mt-1 text-xs text-slate-400">
            Live MTM from Dhan option LTP
            {mtmUpdatedAt ? ` · ${mtmUpdatedAt}` : ''}
          </p>
        </div>
        <div className="flex flex-wrap gap-4 text-sm">
          <div className="text-right">
            <span className="block text-xs text-slate-400">P&amp;L</span>
            <strong className={cn('text-lg tabular-nums', pnlClass(totalPnl))}>
              {money(totalPnl)}
            </strong>
          </div>
          <div className="text-right">
            <span className="block text-xs text-slate-400">Open legs</span>
            <strong className="text-lg text-slate-100">{allOpen.length}</strong>
          </div>
        </div>
      </div>

      <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label="Position filters">
        {filters.map((f) => {
          const count =
            f.id === 'all'
              ? allOpen.length
              : allOpen.filter((r) => passesFilter(r, f.id)).length
          const label = count ? `${f.label} [${count}]` : f.label
          return (
            <button
              key={f.id}
              type="button"
              onClick={() => onFilterChange(f.id)}
              className={cn(
                'rounded-full border px-3 py-1 text-xs transition',
                filter === f.id
                  ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                  : 'border-slate-700 bg-slate-950 text-slate-400 hover:text-slate-200',
              )}
            >
              {label}
            </button>
          )
        })}
      </div>

      <div className="max-h-80 overflow-auto rounded-lg border border-slate-800">
        <table className="min-w-full text-sm">
          <thead className="sticky top-0 z-10 bg-slate-950/95 text-xs text-slate-400">
            <tr className="border-b border-slate-800">
              <th className="px-3 py-2 text-left font-medium">B/S</th>
              <th className="px-3 py-2 text-left font-medium">Name</th>
              <th className="px-3 py-2 text-left font-medium">Mode</th>
              <th className="px-3 py-2 text-right font-medium">Qty</th>
              <th className="px-3 py-2 text-right font-medium">Avg</th>
              <th className="px-3 py-2 text-right font-medium">LTP</th>
              <th className="px-3 py-2 text-right font-medium">P&amp;L</th>
              <th className="px-3 py-2 text-right font-medium">%</th>
            </tr>
          </thead>
          <tbody>
            {!allOpen.length ? (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-slate-500">
                  No open positions in this period.
                </td>
              </tr>
            ) : !visible.length ? (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-slate-500">
                  No legs match this filter.
                </td>
              </tr>
            ) : (
              visible.map((row) => {
                const key = `${row.trade_id}:${row.leg_index ?? 0}`
                const isBuy = row.side !== 'Sell'
                const qty = signedQty(row)
                const pnl = legPnlValue(row)
                const pct = legPctChange(row)
                return (
                  <tr
                    key={key}
                    className="border-b border-slate-800/70 bg-emerald-500/[0.03] text-slate-200"
                  >
                    <td className="px-3 py-2">
                      <span
                        className={cn(
                          'inline-flex h-6 w-6 items-center justify-center rounded text-[11px] font-bold',
                          isBuy
                            ? 'border border-emerald-500/35 bg-emerald-500/15 text-emerald-300'
                            : 'border border-red-500/35 bg-red-500/15 text-red-300',
                        )}
                        title={isBuy ? 'Buy' : 'Sell'}
                      >
                        {isBuy ? 'B' : 'S'}
                      </span>
                    </td>
                    <td className="max-w-xs px-3 py-2 text-slate-100">{legDisplayName(row)}</td>
                    <td className="px-3 py-2">
                      <span className="rounded border border-slate-700 px-1.5 py-0.5 text-[11px] text-slate-300">
                        {row.mode || '—'}
                      </span>
                    </td>
                    <td
                      className={cn(
                        'px-3 py-2 text-right tabular-nums font-semibold',
                        qty >= 0 ? 'text-emerald-400' : 'text-red-400',
                      )}
                    >
                      {qty >= 0 ? '+' : ''}
                      {qty}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatPrice(row.avg_entry)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatPrice(row.mark_price)}</td>
                    <td className={cn('px-3 py-2 text-right tabular-nums font-semibold', pnlClass(pnl))}>
                      {pnl != null ? money(pnl) : '—'}
                    </td>
                    <td className={cn('px-3 py-2 text-right tabular-nums', pnlClass(pnl))}>
                      {pct != null ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : '—'}
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
          {visible.length > 0 ? (
            <tfoot>
              <tr className="border-t border-slate-700 bg-slate-950/80 text-slate-300">
                <td colSpan={6} className="px-3 py-2 text-xs">
                  Filtered total
                </td>
                <td className={cn('px-3 py-2 text-right font-semibold tabular-nums', pnlClass(totalPnl))}>
                  {money(totalPnl)}
                </td>
                <td />
              </tr>
            </tfoot>
          ) : null}
        </table>
      </div>
    </section>
  )
}
