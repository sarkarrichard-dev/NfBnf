import { useEffect } from 'react'
import { cn } from '../lib/cn'
import { money, pctRate, periodBlock, pnlClass } from '../lib/pnl'
import { fx } from '../lib/theme'
import { getSeries, pushPoint } from '../hooks/useSeries'
import { Sparkline } from './Sparkline'
import type { AnalyticsResponse, PeriodKey } from '../types/analytics'

const PERIODS: { id: PeriodKey; label: string }[] = [
  { id: 'today', label: 'Today' },
  { id: 'week', label: 'Week' },
  { id: 'month', label: 'Month' },
  { id: 'all', label: 'All' },
]

type Props = {
  period: PeriodKey
  onPeriodChange: (p: PeriodKey) => void
  updatedLabel?: string
  marketMessage?: string
  marketOpen?: boolean
  analytics: AnalyticsResponse | null | undefined
  openMtmRupees: number
  isLoading?: boolean
}

/** Cumulative realized-PnL curve from the backend daily buckets (oldest → newest). */
function equityCurve(analytics: AnalyticsResponse | null | undefined): number[] {
  const daily = analytics?.daily_series
  if (!daily?.length) return []
  let running = 0
  return [...daily]
    .reverse()
    .map((d) => (running += Number(d.pnl_rupees) || 0))
}

export function StatsOverview({
  period,
  onPeriodChange,
  updatedLabel,
  marketMessage,
  marketOpen,
  analytics,
  openMtmRupees,
  isLoading,
}: Props) {
  const block = periodBlock(analytics ?? null, period)

  const stats = [
    { key: 'trades', label: 'Trades', value: String(block.trades ?? 0), n: block.trades ?? 0 },
    {
      key: 'open',
      label: 'Closed / Open',
      value: `${block.closed ?? 0} / ${block.open ?? 0}`,
      n: block.open ?? 0,
    },
    {
      key: 'wins',
      label: 'Wins / Losses',
      value: `${block.wins ?? 0} / ${block.losses ?? 0}`,
      n: block.wins ?? 0,
    },
    { key: 'winrate', label: 'Win rate', value: pctRate(block.win_rate), n: block.win_rate ?? 0 },
    {
      key: 'realized',
      label: 'Realized PnL',
      value: money(block.pnl_rupees),
      n: block.pnl_rupees ?? 0,
      valueClass: pnlClass(block.pnl_rupees),
    },
    {
      key: 'mtm',
      label: 'Open MTM',
      value: money(openMtmRupees),
      n: openMtmRupees,
      valueClass: pnlClass(openMtmRupees),
    },
  ]

  // Accumulate an intraday history point per metric on every data tick.
  useEffect(() => {
    for (const s of stats) pushPoint(`stat:${period}:${s.key}`, s.n)
  })

  const equity = equityCurve(analytics)

  return (
    <section className={cn(fx.panel, 'mb-4 px-3 py-2.5')}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <div
          className="flex items-center gap-0.5 rounded-lg border border-white/[0.06] bg-white/[0.02] p-0.5"
          role="tablist"
          aria-label="Stats period"
        >
          {PERIODS.map((p) => (
            <button
              key={p.id}
              type="button"
              role="tab"
              aria-selected={period === p.id}
              onClick={() => onPeriodChange(p.id)}
              className={cn(
                'rounded-md px-3 py-1 text-xs font-medium transition',
                period === p.id
                  ? 'bg-blue-600 text-white'
                  : 'text-slate-400 hover:text-slate-200',
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3">
          {equity.length > 1 ? (
            <div className="flex items-center gap-2" title="Cumulative realized PnL (last 31 days)">
              <span className="text-[11px] text-slate-500">Equity</span>
              <Sparkline points={equity} width={120} height={26} />
              <span className={cn('text-xs font-semibold tabular-nums', pnlClass(equity[equity.length - 1]))}>
                {money(equity[equity.length - 1])}
              </span>
            </div>
          ) : null}
          {updatedLabel ? (
            <span className="text-[11px] text-slate-500">{updatedLabel}</span>
          ) : null}
        </div>
      </div>
      {period === 'today' && marketOpen === false && marketMessage ? (
        <p className="mb-2 text-[11px] text-amber-200/70">
          Market closed — {marketMessage}. Today counts IST session entries only (9:30–15:15 Mon–Fri).
        </p>
      ) : null}

      {isLoading && !analytics ? (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="h-14 animate-pulse rounded-lg border border-white/[0.04] bg-white/[0.03]"
            />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          {stats.map((stat) => {
            const points = getSeries(`stat:${period}:${stat.key}`)
            return (
              <article key={stat.label} className={fx.card}>
                <p className={fx.cardLabel}>{stat.label}</p>
                <p className={cn(fx.cardValue, stat.valueClass || 'text-slate-100')}>
                  {stat.value}
                </p>
                {points.length > 1 ? (
                  <Sparkline points={points} className="mt-1 w-full" height={14} />
                ) : null}
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}
