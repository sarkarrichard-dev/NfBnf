import { useMemo, useState } from 'react'
import { cn } from '../../lib/cn'
import { fx } from '../../lib/theme'
import { money, pctRate, pnlClass, tradesForPeriod } from '../../lib/pnl'
import { PeriodBar } from '../PeriodBar'
import { SourceToggle, useTradeSource } from '../SourceToggle'
import { StatTile } from '../ui/StatTile'
import { EquityCurve } from '../charts/EquityCurve'
import { PnlCalendar } from '../charts/PnlCalendar'
import { useCryptoJournal } from '../../hooks/useCryptoJournal'
import { useFuturesJournal } from '../../hooks/useFuturesJournal'
import { useCommoditiesJournal } from '../../hooks/useCommoditiesJournal'
import {
  dailySeriesFromTrades,
  mergeDailySeries,
  type DailyPoint,
} from '../../lib/cryptoRows'
import type {
  AnalyticsResponse,
  DateRange,
  PeriodKey,
  TradeRow,
} from '../../types/analytics'

/** Short axis-label form of a rupee amount — enough precision that adjacent
 *  histogram bins stay distinguishable (money() is too wide, plain k-rounding
 *  collapses everything under ~500 to "0k"). */
function compactRupees(v: number): string {
  const sign = v >= 0 ? '+' : '-'
  const abs = Math.abs(v)
  if (abs >= 1000) return `${sign}${(abs / 1000).toFixed(1)}k`
  return `${sign}${Math.round(abs)}`
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
  const [source, setSource] = useTradeSource()
  const [hoverBin, setHoverBin] = useState<number | null>(null)
  const [hoverInst, setHoverInst] = useState<number | null>(null)
  const crypto = useCryptoJournal(source === 'all' || source === 'crypto')
  const futures = useFuturesJournal(source === 'all' || source === 'futures')
  const commodities = useCommoditiesJournal(source === 'all' || source === 'commodities')

  const allTrades = useMemo<TradeRow[]>(() => {
    if (source === 'index') return trades
    if (source === 'crypto') return crypto.trades
    if (source === 'futures') return futures.trades
    if (source === 'commodities') return commodities.trades
    return [...trades, ...crypto.trades, ...futures.trades, ...commodities.trades]
  }, [source, trades, crypto.trades, futures.trades, commodities.trades])

  const scoped = useMemo(
    () => tradesForPeriod(allTrades, period, range).filter((t) => t.pnl != null),
    [allTrades, period, range],
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
    const caps = scoped
      .map((t) => Number(t.capital_deployed))
      .filter((c) => Number.isFinite(c) && c > 0)
    return {
      net: grossWin - grossLoss,
      count: pnls.length,
      winRate: pnls.length ? wins.length / pnls.length : null,
      avgWin: wins.length ? grossWin / wins.length : 0,
      avgLoss: losses.length ? -grossLoss / losses.length : 0,
      pf: grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : 0,
      maxDd,
      avgCapital: caps.length ? caps.reduce((s, c) => s + c, 0) / caps.length : null,
      roc: caps.length ? (grossWin - grossLoss) / caps.reduce((s, c) => s + c, 0) : null,
    }
  }, [scoped])

  const daily = useMemo<DailyPoint[]>(() => {
    const index = analytics?.daily_series ?? []
    if (source === 'index') return index
    if (source === 'crypto') return dailySeriesFromTrades(crypto.trades)
    if (source === 'futures') return dailySeriesFromTrades(futures.trades)
    if (source === 'commodities') return dailySeriesFromTrades(commodities.trades)
    return mergeDailySeries(
      index,
      dailySeriesFromTrades([...crypto.trades, ...futures.trades, ...commodities.trades]),
    )
  }, [source, analytics?.daily_series, crypto.trades, futures.trades, commodities.trades])

  const equity = useMemo(() => {
    if (!daily.length) return []
    let running = 0
    return [...daily]
      .reverse()
      .map((d) => ({ date: d.period, value: (running += Number(d.pnl_rupees) || 0) }))
  }, [daily])

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
        <div className="flex flex-wrap items-center gap-3">
          <PeriodBar
            period={period}
            onPeriodChange={setPeriod}
            range={range}
            onRangeChange={setRange}
          />
          <SourceToggle value={source} onChange={setSource} />
        </div>
        <span className="text-[11px] text-slate-500">
          {m.count} closed trade{m.count === 1 ? '' : 's'} in range
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
        <StatTile label="Realised P&L" value={money(m.net)} valueClass={pnlClass(m.net)} />
        <StatTile label="Trades" value={String(m.count)} />
        <StatTile label="Win rate" value={pctRate(m.winRate)} />
        <StatTile label="Avg win" value={money(m.avgWin)} valueClass="text-[var(--up)]" />
        <StatTile label="Avg loss" value={money(m.avgLoss)} valueClass="text-[var(--down)]" />
        <StatTile
          label="Profit factor"
          value={m.pf === Infinity ? '∞' : m.pf ? m.pf.toFixed(2) : '—'}
          valueClass={m.pf >= 1 ? 'text-[var(--up)]' : 'text-[var(--down)]'}
        />
        <StatTile
          label="Avg capital / trade"
          value={
            m.avgCapital != null
              ? `₹${Math.round(m.avgCapital).toLocaleString('en-IN')}`
              : '—'
          }
        />
        <StatTile
          label="Return on capital"
          value={m.roc != null ? `${(m.roc * 100).toFixed(1)}%` : '—'}
          valueClass={(m.roc ?? 0) >= 0 ? 'text-[var(--up)]' : 'text-[var(--down)]'}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.5fr,1fr]">
        <Panel title="Equity curve" hint="cumulative realised P&L · last 31 days">
          {equity.length > 1 ? (
            <EquityCurve points={equity} height={150} className="w-full" />
          ) : (
            <p className="py-8 text-center text-xs text-slate-500">
              Not enough closed days yet.
            </p>
          )}
        </Panel>
        <Panel title="P&L calendar" hint="by IST day">
          <PnlCalendar days={daily} />
        </Panel>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="P&L distribution" hint="closed trades by outcome">
          {dist.length ? (
            <div className="relative">
              {/* y-axis count gridlines */}
              <div className="pointer-events-none absolute inset-x-8 inset-y-0">
                {[0, 0.5, 1].map((f) => (
                  <div
                    key={f}
                    className="absolute inset-x-0 border-t border-[var(--hair-soft)]"
                    style={{ top: `${(1 - f) * 100}%` }}
                  />
                ))}
              </div>
              <div className="mr-1 flex h-32 gap-1 pl-7">
                <div className="absolute left-0 top-0 flex h-32 flex-col justify-between font-mono text-[8px] text-slate-600">
                  <span>{distMax}</span>
                  <span>{Math.round(distMax / 2)}</span>
                  <span>0</span>
                </div>
                {dist.map((d, i) => (
                  <div
                    key={i}
                    className="relative flex flex-1 flex-col items-center justify-end gap-1"
                    onMouseEnter={() => setHoverBin(i)}
                    onMouseLeave={() => setHoverBin(null)}
                  >
                    {hoverBin === i ? (
                      <div className="pointer-events-none absolute -top-7 z-10 whitespace-nowrap rounded-md border border-[var(--hair)] bg-[var(--panel)] px-2 py-1 text-[10px] shadow-lg">
                        <span className="text-slate-400">~{money(d.mid)}</span>{' '}
                        <span className="font-semibold text-slate-100">{d.c} trades</span>
                      </div>
                    ) : null}
                    <div
                      className={cn(
                        'w-full rounded-t transition-opacity',
                        hoverBin === i || hoverBin === null ? 'opacity-85' : 'opacity-40',
                      )}
                      style={{
                        height: `${(d.c / distMax) * 100}%`,
                        minHeight: d.c ? 3 : 0,
                        background: d.mid >= 0 ? 'var(--up)' : 'var(--down)',
                      }}
                    />
                    <span className="whitespace-nowrap font-mono text-[8px] text-slate-600">
                      {compactRupees(d.mid)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <p className="py-8 text-center text-xs text-slate-500">No closed trades in range.</p>
          )}
        </Panel>

        <Panel title="P&L by instrument">
          {byInstrument.rows.length ? (
            <div className="space-y-2">
              <div className="flex items-center gap-3 pl-[5.5rem] pr-24 font-mono text-[8px] text-slate-600">
                <span>-{money(byInstrument.peak).replace('+', '')}</span>
                <span className="ml-auto">0</span>
                <span className="ml-auto">+{money(byInstrument.peak).replace('+', '')}</span>
              </div>
              {byInstrument.rows.map(([k, v], i) => (
                <div
                  key={k}
                  className="relative flex items-center gap-3 text-xs"
                  onMouseEnter={() => setHoverInst(i)}
                  onMouseLeave={() => setHoverInst(null)}
                >
                  <span className="w-20 shrink-0 font-medium text-slate-300">{k}</span>
                  <div
                    className={cn(
                      'relative h-4 flex-1 rounded bg-white/[0.03] transition-colors',
                      hoverInst === i && 'bg-white/[0.06]',
                    )}
                  >
                    <div
                      className="absolute inset-y-0 rounded"
                      style={{
                        width: `${(Math.abs(v) / byInstrument.peak) * 50}%`,
                        left: v >= 0 ? '50%' : undefined,
                        right: v < 0 ? '50%' : undefined,
                        background: v >= 0 ? 'var(--up)' : 'var(--down)',
                        opacity: hoverInst === i || hoverInst === null ? 0.8 : 0.4,
                      }}
                    />
                    <div className="absolute inset-y-0 left-1/2 w-px bg-[var(--hair)]" />
                    {hoverInst === i ? (
                      <div
                        className="pointer-events-none absolute -top-8 z-10 -translate-x-1/2 whitespace-nowrap rounded-md border border-[var(--hair)] bg-[var(--panel)] px-2 py-1 text-[10px] font-semibold shadow-lg"
                        style={{
                          left: `${50 + (v >= 0 ? 1 : -1) * (Math.abs(v) / byInstrument.peak) * 50}%`,
                        }}
                      >
                        <span className={pnlClass(v)}>
                          {k}: {money(v)}
                        </span>
                      </div>
                    ) : null}
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
        {source === 'crypto'
          ? 'Crypto P&L is net of Delta fees, converted to ₹ at the trade’s USD/INR rate. Per-exit and fee-drag breakdowns are on the Crypto tab’s day review.'
          : source === 'commodities'
            ? 'Commodity P&L is net of the MCX schedule (₹20/order + txn + CTT + SEBI + stamp + GST) plus one tick of slippage each side, applied at exit.'
            : 'Fee drag and P&L-by-exit breakdowns are on the Crypto tab’s day review — the index journal stores net P&L only.'}
      </p>
    </div>
  )
}
