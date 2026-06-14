import { Activity, TrendingUp, Wallet, LineChart } from 'lucide-react'
import { cn } from '../lib/cn'
import { money, pctRate, periodBlock, pnlClass } from '../lib/pnl'
import type { AnalyticsResponse, PeriodKey } from '../types/analytics'

type Props = {
  analytics: AnalyticsResponse | null | undefined
  period: PeriodKey
  openMtmRupees: number
}

export function StatsRail({ analytics, period, openMtmRupees }: Props) {
  const block = periodBlock(analytics ?? null, period)
  const cards = [
    { label: 'Trades', value: String(block.trades ?? 0), icon: Activity },
    {
      label: 'Closed / Open',
      value: `${block.closed ?? 0} / ${block.open ?? 0}`,
      icon: LineChart,
    },
    {
      label: 'Wins / Losses',
      value: `${block.wins ?? 0} / ${block.losses ?? 0}`,
      icon: TrendingUp,
    },
    { label: 'Win rate', value: pctRate(block.win_rate), icon: TrendingUp },
    {
      label: 'Realized PnL',
      value: money(block.pnl_rupees),
      valueClass: pnlClass(block.pnl_rupees),
      icon: Wallet,
    },
    {
      label: 'Open MTM',
      value: money(openMtmRupees),
      valueClass: pnlClass(openMtmRupees),
      icon: Wallet,
    },
  ]

  return (
    <section className="mb-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
      {cards.map((card) => (
        <article
          key={card.label}
          className="rounded-xl border border-slate-800 bg-slate-900/60 p-4"
        >
          <div className="mb-2 flex items-center gap-2 text-slate-400">
            <card.icon size={15} />
            <span className="text-xs">{card.label}</span>
          </div>
          <p className={cn('text-xl font-semibold tabular-nums', card.valueClass || 'text-slate-100')}>
            {card.value}
          </p>
        </article>
      ))}
    </section>
  )
}

const PERIODS: { id: PeriodKey; label: string }[] = [
  { id: 'today', label: 'Today' },
  { id: 'week', label: 'Week' },
  { id: 'month', label: 'Month' },
  { id: 'all', label: 'All' },
]

export function PeriodTabs({
  period,
  onChange,
  updatedLabel,
}: {
  period: PeriodKey
  onChange: (p: PeriodKey) => void
  updatedLabel?: string
}) {
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      {PERIODS.map((p) => (
        <button
          key={p.id}
          type="button"
          onClick={() => onChange(p.id)}
          className={cn(
            'rounded-lg border px-3 py-1.5 text-sm transition',
            period === p.id
              ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200'
              : 'border-slate-700 text-slate-400 hover:text-slate-200',
          )}
        >
          {p.label}
        </button>
      ))}
      {updatedLabel ? (
        <span className="ml-auto text-xs text-slate-500">{updatedLabel}</span>
      ) : null}
    </div>
  )
}
