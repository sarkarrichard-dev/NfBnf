import { useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
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
  const qc = useQueryClient()

  const closePos = useMutation({
    mutationFn: (trade_id: string) =>
      api<{ status: string; pnl?: number }>('/api/trades/positions/close', {
        method: 'POST',
        body: JSON.stringify({ trade_id }),
      }),
    onSuccess: (res) => {
      if (res.status === 'ALREADY_CLOSED') {
        toast.info('Already closed')
      } else {
        toast.success(res.pnl != null ? `Closed — ${money(res.pnl)}` : 'Closed')
      }
      void qc.invalidateQueries({ queryKey: ['status'] })
      void qc.invalidateQueries({ queryKey: ['analytics'] })
      void qc.invalidateQueries({ queryKey: ['journal'] })
      void qc.invalidateQueries({ queryKey: ['live-mtm'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const closeAllPos = useMutation({
    mutationFn: () =>
      api<{ ok: boolean; attempted: number; results: Record<string, { status: string }> }>(
        '/api/trades/positions/close-all',
        { method: 'POST' },
      ),
    onSuccess: (res) => {
      if (res.attempted === 0) {
        toast.success('Nothing open to close')
      } else {
        const closed = Object.values(res.results).filter((r) => r.status === 'CLOSED').length
        toast.success(`Closed ${closed} of ${res.attempted} open trade${res.attempted === 1 ? '' : 's'}`)
      }
      void qc.invalidateQueries({ queryKey: ['status'] })
      void qc.invalidateQueries({ queryKey: ['analytics'] })
      void qc.invalidateQueries({ queryKey: ['journal'] })
      void qc.invalidateQueries({ queryKey: ['live-mtm'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

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
            <div className="flex flex-wrap items-center gap-4 text-sm">
              <div className="text-right">
                <span className="block text-xs text-cyan-200/45">P&amp;L</span>
                <strong className={cn('font-mono text-lg tabular-nums', pnlClass(openTotalPnl))}>
                  {money(openTotalPnl)}
                </strong>
              </div>
              <div className="text-right">
                <span className="block text-xs text-cyan-200/45">Open legs</span>
                <strong className="text-lg text-cyan-50">{allOpen.length}</strong>
              </div>
              {allOpen.length ? (
                <button
                  type="button"
                  disabled={closeAllPos.isPending}
                  onClick={() => {
                    const n = new Set(allOpen.map((r) => r.trade_id)).size
                    if (!window.confirm(`Close all ${n} open trade${n === 1 ? '' : 's'} now, at current prices?`))
                      return
                    closeAllPos.mutate()
                  }}
                  className="rounded-md border border-[var(--down)]/40 px-2 py-1 font-sans text-[11px] font-semibold text-[var(--down)] transition hover:bg-[var(--down)]/10 disabled:opacity-40"
                >
                  {closeAllPos.isPending ? 'Closing all…' : 'Close all'}
                </button>
              ) : null}
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
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {!allOpen.length ? (
                  <tr>
                    <td colSpan={9} className="px-3 py-6 text-center text-slate-500">
                      No open positions in this period.
                    </td>
                  </tr>
                ) : !visibleOpen.length ? (
                  <tr>
                    <td colSpan={9} className="px-3 py-6 text-center text-slate-500">
                      No legs match this filter.
                    </td>
                  </tr>
                ) : (
                  (() => {
                    const seenTrades = new Set<string>()
                    return visibleOpen.map((row) => {
                      const tradeId = row.trade_id
                      const isFirstLeg = tradeId ? !seenTrades.has(tradeId) : false
                      if (tradeId) seenTrades.add(tradeId)
                      return (
                        <PositionRow
                          key={`${row.trade_id}:${row.leg_index ?? 0}`}
                          row={row}
                          showClose={isFirstLeg}
                          closing={closePos.isPending && closePos.variables === tradeId}
                          onClose={
                            tradeId
                              ? () => {
                                  if (!window.confirm('Close this trade now, at the current price?')) return
                                  closePos.mutate(tradeId)
                                }
                              : undefined
                          }
                        />
                      )
                    })
                  })()
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
