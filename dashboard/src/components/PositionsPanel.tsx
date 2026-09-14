import { useMemo } from 'react'
import { cn } from '../lib/cn'
import {
  legPnlValue,
  money,
  openLegRows,
  pnlClass,
} from '../lib/pnl'
import { fx } from '../lib/theme'
import type { LogRow, PeriodKey, PositionsFilter, TradeRow } from '../types/analytics'
import { PositionRow } from './PositionRow'
import { EmptyState } from './ui/EmptyState'

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

  const filterCounts = useMemo(() => {
    const profit = allOpen.filter((r) => (legPnlValue(r) ?? 0) > 0).length
    const loss = allOpen.filter((r) => (legPnlValue(r) ?? 0) < 0).length
    return { all: allOpen.length, profit, loss }
  }, [allOpen])

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
    <section className={cn(fx.panel, 'p-4')}>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-cyan-50/95">Today&apos;s positions</h2>
          <p className="mt-1 text-xs text-cyan-200/45">
            Live MTM from Dhan option LTP
            {mtmUpdatedAt ? ` · ${mtmUpdatedAt}` : ''}
          </p>
        </div>
        <div className="flex flex-wrap gap-4 text-sm">
          <div className="text-right">
            <span className="block text-xs text-cyan-200/45">P&amp;L</span>
            <strong className={cn('text-lg tabular-nums', pnlClass(totalPnl))}>
              {money(totalPnl)}
            </strong>
          </div>
          <div className="text-right">
            <span className="block text-xs text-cyan-200/45">Open legs</span>
            <strong className="text-lg text-cyan-50">{allOpen.length}</strong>
          </div>
        </div>
      </div>

      <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label="Position filters">
        {filters.map((f) => {
          const count = filterCounts[f.id]
          const label = count ? `${f.label} [${count}]` : f.label
          return (
            <button
              key={f.id}
              type="button"
              onClick={() => onFilterChange(f.id)}
              className={cn(
                'rounded-full border px-3 py-1 text-xs transition',
                filter === f.id
                  ? 'border-cyan-400/40 bg-cyan-400/10 text-cyan-100 shadow-[0_0_10px_-4px_rgba(34,211,238,0.5)]'
                  : 'border-slate-700/60 bg-black/20 text-slate-400 hover:border-cyan-500/30 hover:text-cyan-100',
              )}
            >
              {label}
            </button>
          )
        })}
      </div>

      <div className="max-h-80 overflow-auto rounded-lg border border-cyan-500/15 bg-black/25">
        <table className="min-w-full text-sm">
          <thead className="sticky top-0 z-10 bg-slate-950/95 text-xs text-cyan-200/50">
            <tr className="border-b border-cyan-500/10">
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
                <td colSpan={8} className="p-3">
                  <EmptyState>No open positions in this period.</EmptyState>
                </td>
              </tr>
            ) : !visible.length ? (
              <tr>
                <td colSpan={8} className="p-3">
                  <EmptyState>No legs match this filter.</EmptyState>
                </td>
              </tr>
            ) : (
              visible.map((row) => (
                <PositionRow key={`${row.trade_id}:${row.leg_index ?? 0}`} row={row} />
              ))
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
