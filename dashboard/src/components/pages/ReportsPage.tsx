import { useMemo, useState } from 'react'
import { cn } from '../../lib/cn'
import { fx } from '../../lib/theme'
import { money, pctRate, pnlClass, tradesForPeriod } from '../../lib/pnl'
import { PeriodBar } from '../PeriodBar'
import { EquityCurve } from '../charts/EquityCurve'
import { PnlCalendar } from '../charts/PnlCalendar'
import type {
  AnalyticsResponse,
  DateRange,
  PeriodKey,
  TradeRow,
} from '../../types/analytics'

function Tile({
  label,
  value,
  cls,
}: {
  label: string
  value: string
  cls?: string
}) {
  return (
    <div className={fx.card}>
      <p className={fx.cardLabel}>{label}</p>
      <p className={cn(fx.cardValue, 'text-base', cls || 'text-slate-100')}>{value}</p>
    </div>
  )
}

function Panel({
  title,
  hint,
  children,
}: {
  title: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <section className={cn(fx.panel, 'p-4')}>
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-bold text-slate-100">{title}</h3>
        {hint ? <span className="text-[11px] text-slate-500">{hint}</span> : null}
      </div>
      {children}
    </section>
  )
}

/** Reports & PnL — the deep read on your own trading, modelled on Cryptomaty's
 *  Performance breakdown: headline tiles, equity curve, the P&L calendar
 *  heat-grid, a distribution histogram, and P&L by instrument. */
export function ReportsPage({
  analytics,
  trades,
}: {
  analytics: AnalyticsResponse | null | undefined
  trades: TradeRow[]
}) {
  const [period, setPeriod] = useState<PeriodKey>('all')
  const [range, setRange] = useState<DateRange>({ from: '', to: '' })

  const scoped = useMemo(
    () => tradesForPeriod(trades, period, range).filter((t) => t.pnl != null),
    [trades, period, range],
  )

  const m = useMemo(() => {
    const pnls = scoped.map((t) => Number(t.pnl) || 0)
    const wins = pnls.filter((p) => p > 0)
    const losses = pnls.filter((p) => p < 0)
    const grossWin = wins.reduce((s, p) => s + p, 0)
    const grossLoss = Math.abs(losses.reduce((s, p) => s + p, 0))
    let equity = 0
    let peak = 0
    let maxDd = 0
    for (const p of pnls) {
      equity += p
      peak = Math.max(peak, equity)
      maxDd = Math.min(maxDd, equity - peak)
    }
    return {
      net: grossWin - grossLoss,
      count: pnls.length,
      winRate: pnls.length ? wins.length / pnls.length : null,
      avgWin: wins.length ? grossWin / wins.length : 0,
      avgLoss: losses.length ? -grossLoss / losses.length : 0,
      pf: grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : 0,
      maxDd,
    }
  }, [scoped])

  const equity = useMemo(() => {
    const daily = analytics?.daily_series
    if (!daily?.length) return []
    let running = 0
    return [...daily].reverse().map((d) => (running += Number(d.pnl_rupees) || 0))
  }, [analytics?.daily_series])

  const dist = useMemo(() => {
    const pnls = scoped.map((t) => Number(t.pnl) || 0)
    if (!pnls.length) return []
    const lo = Math.min(...pnls)
    const hi = Math.max(...pnls)
    const span = hi - lo || 1
    const BINS = 9
    const counts = Array.from({ length: BINS }, () => 0)
    for (const p of pnls) {
      const idx = Math.min(BINS - 1, Math.floor(((p - lo) / span) * BINS))
      counts[idx]++
    }
    const step = span / BINS
    return counts.map((c, i) => ({
      c,
      mid: lo + step * (i + 0.5),
    }))
  }, [scoped])
  const distMax = Math.max(1, ...dist.map((d) => d.c))

  const byInstrument = useMemo(() => {
    const agg = new Map<string, number>()
    for (const t of scoped) {
      const k = String(t.instrument || '—').toUpperCase()
      agg.set(k, (agg.get(k) || 0) + (Number(t.pnl) || 0))
    }
    const rows = [...agg.entries()].sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
    const peak = Math.max(1, ...rows.map(([, v]) => Math.abs(v)))
    return { rows, peak }
  }, [scoped])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PeriodBar
          period={period}
          onPeriodChange={setPeriod}
          range={range}
          onRangeChange={setRange}
        />
        <span className="text-[11px] text-slate-500">
          {m.count} closed trade{m.count === 1 ? '' : 's'} in range
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Tile label="Realised P&L" value={money(m.net)} cls={pnlClass(m.net)} />
        <Tile label="Trades" value={String(m.count)} />
        <Tile label="Win rate" value={pctRate(m.winRate)} />
        <Tile label="Avg win" value={money(m.avgWin)} cls="text-[var(--up)]" />
        <Tile label="Avg loss" value={money(m.avgLoss)} cls="text-[var(--down)]" />
        <Tile
          label="Profit factor"
          value={m.pf === Infinity ? '∞' : m.pf ? m.pf.toFixed(2) : '—'}
          cls={m.pf >= 1 ? 'text-[var(--up)]' : 'text-[var(--down)]'}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.5fr,1fr]">
        <Panel title="Equity curve" hint="cumulative realised P&L · last 31 days">
          {equity.length > 1 ? (
            <EquityCurve values={equity} height={150} className="w-full" />
          ) : (
            <p className="py-8 text-center text-xs text-slate-500">
              Not enough closed days yet.
            </p>
          )}
        </Panel>
        <Panel title="P&L calendar" hint="by IST day">
          <PnlCalendar days={analytics?.daily_series ?? []} />
        </Panel>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="P&L distribution" hint="closed trades by outcome">
          {dist.length ? (
            <div className="flex h-32 items-end gap-1">
              {dist.map((d, i) => (
                <div key={i} className="flex flex-1 flex-col items-center gap-1" title={`~${money(d.mid)} · ${d.c}`}>
                  <div
                    className="w-full rounded-t"
                    style={{
                      height: `${(d.c / distMax) * 100}%`,
                      minHeight: d.c ? 3 : 0,
                      background: d.mid >= 0 ? 'var(--up)' : 'var(--down)',
                      opacity: 0.85,
                    }}
                  />
                  <span className="font-mono text-[8px] text-slate-600">
                    {d.mid >= 0 ? '+' : ''}
                    {Math.round(d.mid / 1000)}k
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="py-8 text-center text-xs text-slate-500">No closed trades in range.</p>
          )}
        </Panel>

        <Panel title="P&L by instrument">
          {byInstrument.rows.length ? (
            <div className="space-y-2">
              {byInstrument.rows.map(([k, v]) => (
                <div key={k} className="flex items-center gap-3 text-xs">
                  <span className="w-20 shrink-0 font-medium text-slate-300">{k}</span>
                  <div className="relative h-4 flex-1 rounded bg-white/[0.03]">
                    <div
                      className="absolute inset-y-0 rounded"
                      style={{
                        width: `${(Math.abs(v) / byInstrument.peak) * 100}%`,
                        left: v >= 0 ? '50%' : undefined,
                        right: v < 0 ? '50%' : undefined,
                        background: v >= 0 ? 'var(--up)' : 'var(--down)',
                        opacity: 0.8,
                      }}
                    />
                    <div className="absolute inset-y-0 left-1/2 w-px bg-[var(--hair)]" />
                  </div>
                  <span className={cn('w-24 shrink-0 text-right font-mono tabular-nums', pnlClass(v))}>
                    {money(v)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="py-8 text-center text-xs text-slate-500">No closed trades in range.</p>
          )}
        </Panel>
      </div>

      <p className="text-[11px] text-slate-600">
        Fee drag and P&L-by-exit breakdowns are on the Crypto tab's day review — the
        index journal stores net P&L only.
      </p>
    </div>
  )
}
