import { useMemo, useState } from 'react'
import { cn } from '../lib/cn'
import {
  legPnlValue,
  logRowsForPeriod,
  money,
  openLegRows,
  pnlClass,
} from '../lib/pnl'
import { fx } from '../lib/theme'
import type { DateRange, LogRow, PeriodKey, PositionsFilter, TradeRow } from '../types/analytics'
import { PositionRow } from './PositionRow'
import { TradeLogTable } from './TradeLogTable'
import { Button } from './ui/Button'
import { EmptyState } from './ui/EmptyState'

type Tab = 'open' | 'history'

type Props = {
  logRows: LogRow[]
  trades: TradeRow[]
  period: PeriodKey
  range: DateRange
  mtmUpdatedAt?: string
}

function passesFilter(row: LogRow, filter: PositionsFilter): boolean {
  const pnl = legPnlValue(row)
  if (filter === 'profit') return pnl != null && pnl > 0
  if (filter === 'loss') return pnl != null && pnl < 0
  return true
}

export function JournalPanel({ logRows, trades, period, range, mtmUpdatedAt }: Props) {
  const [tab, setTab] = useState<Tab>('open')
  const [filter, setFilter] = useState<PositionsFilter>('all')

  const allOpen = useMemo(
    () => openLegRows(logRows, trades, period),
    [logRows, trades, period],
  )

  const visibleOpen = useMemo(
    () => allOpen.filter((row) => passesFilter(row, filter)),
    [allOpen, filter],
  )

  const openTotalPnl = useMemo(
    () => visibleOpen.reduce((s, r) => s + (legPnlValue(r) ?? 0), 0),
    [visibleOpen],
  )

  const filterCounts = useMemo(() => {
    const profit = allOpen.filter((r) => (legPnlValue(r) ?? 0) > 0).length
    const loss = allOpen.filter((r) => (legPnlValue(r) ?? 0) < 0).length
    return { all: allOpen.length, profit, loss }
  }, [allOpen])

  const historyCount = useMemo(
    () => logRowsForPeriod(logRows, trades, period, range).length,
    [logRows, trades, period, range],
  )

  const filters: { id: PositionsFilter; label: string }[] = [
    { id: 'all', label: 'Open' },
    { id: 'profit', label: 'In profit' },
    { id: 'loss', label: 'In loss' },
  ]

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2 border-b border-cyan-500/15 pb-2">
        <Button
          onClick={() => setTab('open')}
          className={cn(
            'rounded-md px-3 py-1.5 text-sm transition',
            tab === 'open'
              ? 'bg-cyan-400/15 text-cyan-100 ring-1 ring-cyan-400/30'
              : 'text-slate-400 hover:text-cyan-100',
          )}
        >
          Open positions ({allOpen.length})
        </Button>
        <Button
          onClick={() => setTab('history')}
          className={cn(
            'rounded-md px-3 py-1.5 text-sm transition',
            tab === 'history'
              ? 'bg-cyan-400/15 text-cyan-100 ring-1 ring-cyan-400/30'
              : 'text-slate-400 hover:text-cyan-100',
          )}
        >
          Trade history ({historyCount})
        </Button>
      </div>

      {tab === 'open' ? (
        <section className={cn(fx.panel, 'p-4')}>
          <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-cyan-50/95">Open positions</h2>
              <p className="mt-1 text-xs text-cyan-200/45">
                Paper / live journal legs with live MTM
                {mtmUpdatedAt ? ` · ${mtmUpdatedAt}` : ''}
                {' · '}
                All open legs (not limited by Today/Week/Month filter)
              </p>
            </div>
            <div className="flex flex-wrap gap-4 text-sm">
              <div className="text-right">
                <span className="block text-xs text-cyan-200/45">P&amp;L</span>
                <strong className={cn('text-lg tabular-nums', pnlClass(openTotalPnl))}>
                  {money(openTotalPnl)}
                </strong>
              </div>
              <div className="text-right">
                <span className="block text-xs text-cyan-200/45">Open legs</span>
                <strong className="text-lg text-cyan-50">{allOpen.length}</strong>
              </div>
            </div>
          </div>

          <div className="mb-3 flex flex-wrap gap-2">
            {filters.map((f) => {
              const count = filterCounts[f.id]
              const label = count ? `${f.label} [${count}]` : f.label
              return (
                <Button
            key={f.id}
                  onClick={() => setFilter(f.id)}
                  className={cn(
                    'rounded-full border px-3 py-1 text-xs transition',
                    filter === f.id
                      ? 'border-cyan-400/40 bg-cyan-400/10 text-cyan-100'
                      : 'border-slate-700/60 bg-black/20 text-slate-400 hover:text-cyan-100',
                  )}
                >
                  {label}
                </Button>
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
                ) : !visibleOpen.length ? (
                  <tr>
                    <td colSpan={8} className="p-3">
                      <EmptyState>No legs match this filter.</EmptyState>
                    </td>
                  </tr>
                ) : (
                  visibleOpen.map((row) => (
                    <PositionRow key={`${row.trade_id}:${row.leg_index ?? 0}`} row={row} />
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      ) : (
        <TradeLogTable
          logRows={logRows}
          trades={trades}
          period={period}
          range={range}
          mtmUpdatedAt={mtmUpdatedAt}
          hideTitle
        />
      )}
    </div>
  )
}
