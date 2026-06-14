type Props = {
  strategy?: Record<string, unknown> | null
}

export function StrategyTuningPanel({ strategy }: Props) {
  const data = strategy

  if (!data) return <p className="text-sm text-slate-500">Loading strategy settings…</p>

  const note = [
    data.candle_bar_note,
    data.ema_cross_note,
    data.cpr_width_note,
    data.strategy_style_note,
  ]
    .filter(Boolean)
    .join(' ')

  const rows: [string, unknown][] = [
    ['Chart interval', data.candle_interval_minutes ? `${data.candle_interval_minutes}m` : '—'],
    ['Style', data.strategy_style],
    ['EMA', `${data.ema_fast_period} / ${data.ema_slow_period}`],
    ['Trend → buy first', data.auto_trend_buy_first ? 'Yes' : 'No'],
    ['Credit sideways only', data.auto_credit_sideways_only ? 'Yes' : 'No'],
    ['Breakout lookback', data.breakout_lookback],
    ['Entry confirm bars', data.entry_confirmation_bars],
    ['Supertrend', `${data.supertrend_period} / ${data.supertrend_multiplier}`],
    ['Credit min conf.', data.credit_min_confidence],
    ['Credit stop', data.credit_stop_loss_pct],
    ['Credit profit', data.credit_profit_target_pct],
  ]

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">
        {note || 'Read-only view from .env — restart server after edits.'}
      </p>
      <dl className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {rows.map(([label, val]) => (
          <div key={String(label)} className="rounded-lg border border-slate-800 bg-slate-950/40 p-2">
            <dt className="text-[11px] text-slate-500">{label}</dt>
            <dd className="text-sm text-slate-200">{String(val ?? '—')}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
