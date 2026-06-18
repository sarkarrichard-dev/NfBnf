import { cn } from '../lib/cn'
import { money, pctRate, periodBlock, pnlClass } from '../lib/pnl'
import { fx } from '../lib/theme'
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
    { label: 'Trades', value: String(block.trades ?? 0) },
    { label: 'Closed / Open', value: `${block.closed ?? 0} / ${block.open ?? 0}` },
    { label: 'Wins / Losses', value: `${block.wins ?? 0} / ${block.losses ?? 0}` },
    { label: 'Win rate', value: pctRate(block.win_rate) },
    {
      label: 'Realized PnL',
      value: money(block.pnl_rupees),
      valueClass: pnlClass(block.pnl_rupees),
    },
    {
      label: 'Open MTM',
      value: money(openMtmRupees),
      valueClass: pnlClass(openMtmRupees),
    },
  ]

  return (
    <section className={cn(fx.panel, 'mb-4 px-3 py-2.5')}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <div className="flex items-center gap-1" role="tablist" aria-label="Stats period">
          {PERIODS.map((p) => (
            <button
              key={p.id}
              type="button"
              role="tab"
              aria-selected={period === p.id}
              onClick={() => onPeriodChange(p.id)}
              className={cn(
                'rounded-md border px-2.5 py-1 text-xs font-medium transition',
                period === p.id ? fx.tabActive : fx.tabIdle,
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
        {updatedLabel ? (
          <span className="text-[11px] text-cyan-200/40">{updatedLabel}</span>
        ) : null}
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
              className="h-14 animate-pulse rounded-lg border border-cyan-500/5 bg-cyan-500/5"
            />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          {stats.map((stat) => (
            <article key={stat.label} className={fx.card}>
              <p className={fx.cardLabel}>{stat.label}</p>
              <p className={cn(fx.cardValue, stat.valueClass || 'text-slate-100')}>
                {stat.value}
              </p>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
