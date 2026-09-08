import { useEffect } from 'react'
import { cn } from '../lib/cn'
import { computePeriodStats, money, pctRate, periodBlock, pnlClass, tradesForPeriod } from '../lib/pnl'
import { fx } from '../lib/theme'
import { getSeries, pushPoint } from '../hooks/useSeries'
import { PeriodBar } from './PeriodBar'
import { Sparkline } from './Sparkline'
import { EquityCurve } from './charts/EquityCurve'
import type { AnalyticsResponse, DateRange, PeriodKey, TradeRow } from '../types/analytics'

const PERIOD_LABEL: Record<PeriodKey, string> = {
  today: 'today',
  week: 'this week',
  month: 'this month',
  all: 'all time',
  custom: 'in range',
}

type Props = {
  period: PeriodKey
  onPeriodChange: (p: PeriodKey) => void
  range: DateRange
  onRangeChange: (r: DateRange) => void
  updatedLabel?: string
  marketMessage?: string
  marketOpen?: boolean
  analytics: AnalyticsResponse | null | undefined
  trades: TradeRow[]
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
  range,
  onRangeChange,
  updatedLabel,
  marketMessage,
  marketOpen,
  analytics,
  trades,
  openMtmRupees,
  isLoading,
}: Props) {
  const block =
    period === 'custom'
      ? computePeriodStats(tradesForPeriod(trades, 'custom', range))
      : periodBlock(analytics ?? null, period)

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
  const net = block.pnl_rupees ?? 0

  return (
    <section className={cn(fx.panel, 'mb-6 space-y-3 p-4')}>
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <PeriodBar
          period={period}
          onPeriodChange={onPeriodChange}
          range={range}
          onRangeChange={onRangeChange}
        />
        {updatedLabel ? (
          <span className="font-mono text-[10px] text-slate-500">{updatedLabel}</span>
        ) : null}
      </div>

      {period === 'today' && marketOpen === false && marketMessage ? (
        <p className="text-[11px] text-[var(--warn)]/80">
          Market closed — {marketMessage}. Today counts IST session entries only (9:20–15:10 Mon–Fri).
        </p>
      ) : null}

      {/* cockpit headline: period P&L + a proper equity curve */}
      <div className="grid items-center gap-4 rounded-lg border border-[var(--hair-soft)] bg-white/[0.015] p-3.5 lg:grid-cols-[minmax(0,1fr),1.5fr]">
        <div>
          <p className={fx.cardLabel}>Realised P&amp;L · {PERIOD_LABEL[period]}</p>
          <p className={cn('mt-1 font-mono text-[2rem] font-extrabold leading-none tabular-nums', pnlClass(net))}>
            {money(net)}
          </p>
          <p className="mt-1.5 font-mono text-[11px] text-slate-500">
            {block.closed ?? 0} closed · {block.wins ?? 0}W / {block.losses ?? 0}L ·{' '}
            {pctRate(block.win_rate)} win
          </p>
        </div>
        <div className="min-w-0">
          {equity.length > 1 ? (
            <EquityCurve values={equity} height={96} className="w-full" />
          ) : (
            <p className="text-right font-mono text-[11px] text-slate-600">building the curve…</p>
          )}
        </div>
      </div>

      {isLoading && !analytics ? (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="h-16 animate-pulse rounded-xl border border-white/[0.04] bg-white/[0.03]"
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
