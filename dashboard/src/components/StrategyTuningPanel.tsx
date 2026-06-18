import { cn } from '../lib/cn'
import { fx } from '../lib/theme'

type Props = {
  strategy?: Record<string, unknown> | null
}

export function StrategyTuningPanel({ strategy }: Props) {
  const data = strategy

  if (!data) return <p className="text-sm text-cyan-200/40">Loading strategy settings…</p>

  const note = [
    data.candle_bar_note,
    data.ema_cross_note,
    data.cpr_width_note,
    data.strategy_style_note,
  ]
    .filter(Boolean)
    .join(' ')

  const rows: [string, unknown][] = [
    ['Interval', data.candle_interval_minutes ? `${data.candle_interval_minutes}m` : '—'],
    ['Style', data.strategy_style],
    ['EMA', `${data.ema_fast_period} / ${data.ema_slow_period}`],
    ['Buy first', data.auto_trend_buy_first ? 'Yes' : 'No'],
    ['Sideways credit', data.auto_credit_sideways_only ? 'Yes' : 'No'],
    ['Breakout', data.breakout_lookback],
    ['Confirm bars', data.entry_confirmation_bars],
    ['Supertrend', `${data.supertrend_period} / ${data.supertrend_multiplier}`],
    ['Credit conf.', data.credit_min_confidence],
    ['Credit stop', data.credit_stop_loss_pct],
    ['Credit profit', data.credit_profit_target_pct],
  ]

  return (
    <div className="space-y-2">
      <p className="text-[11px] leading-relaxed text-cyan-100/45">
        {note || 'Read-only from .env — restart server after edits.'}
      </p>
      <dl className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6">
        {rows.map(([label, val]) => (
          <div key={String(label)} className={fx.card}>
            <dt className={fx.cardLabel}>{label}</dt>
            <dd className={cn(fx.cardValue, 'text-cyan-50')}>{String(val ?? '—')}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
