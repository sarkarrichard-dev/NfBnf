import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePollMs } from '../hooks/usePageVisible'
import { useFuturesJournal } from '../hooks/useFuturesJournal'
import { cn } from '../lib/cn'
import { fx } from '../lib/theme'
import type { DateRange, PeriodKey } from '../types/analytics'
import { LaneCard, pnlClass, rupees, type LaneStatus } from './LanesPanel'
import { PeriodBar } from './PeriodBar'
import { TradeLogTable } from './TradeLogTable'
import { StatTile } from './ui/StatTile'

type Agg = {
  trades?: number
  win_rate_pct?: number
  net_rupees?: number
  gross_rupees?: number
  friction_rupees?: number
  expectancy?: number
  profit_factor?: number | null
  max_drawdown?: number
  long?: number
  short?: number
  span?: string
}

type StockBacktest = {
  params?: { months?: number; half_spread_bps?: number; k?: number[]; names?: string[] }
  portfolio?: Agg
  per_stock?: Record<string, Agg>
  halves?: { first?: number; second?: number }
  half_spread_sweep?: Record<string, number>
  verdict?: { edge?: boolean; reason?: string }
  generated_at_ist?: string
}

type BacktestResponse = {
  stock?: StockBacktest
  index?: { per_instrument?: Record<string, Agg>; instruments?: Record<string, Agg> }
  generated_at_ist?: string
}

function VerdictBanner({ bt }: { bt: StockBacktest }) {
  const v = bt.verdict ?? {}
  const net = bt.portfolio?.net_rupees ?? 0
  const edge = !!v.edge
  return (
    <div
      className={cn(
        'rounded-md border p-4',
        edge
          ? 'border-[var(--up)]/30 bg-[var(--up)]/[0.06]'
          : 'border-[var(--down)]/30 bg-[var(--down)]/[0.06]',
      )}
    >
      <p className={cn('text-sm font-bold', edge ? 'text-[var(--up)]' : 'text-[var(--down)]')}>
        {edge ? 'Still profitable after real broker charges — proceed' : 'No edge / too fragile'} ·
        net {rupees(net)}
      </p>
      <p className="mt-1 text-xs text-slate-400">{v.reason || '—'}</p>
      {bt.params ? (
        <p className="mt-2 text-[11px] text-slate-500">
          {bt.params.months}-month window · half-spread {bt.params.half_spread_bps} bp/side ·
          K {JSON.stringify(bt.params.k)} · as of {bt.generated_at_ist}
        </p>
      ) : null}
    </div>
  )
}

function PerStockTable({ rows }: { rows: [string, Agg][] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--hair)] bg-black/25">
      <table className="min-w-full text-xs">
        <thead className="text-slate-400">
          <tr className="border-b border-slate-800 [&>th]:px-3 [&>th]:py-2 [&>th]:font-medium">
            <th className="text-left">Symbol</th>
            <th className="text-left">Span</th>
            <th className="text-right">Trades</th>
            <th className="text-right">L/S</th>
            <th className="text-right">Win%</th>
            <th className="text-right">PF</th>
            <th className="text-right">Net ₹</th>
            <th className="text-right">₹/trade</th>
            <th className="text-right">Max DD</th>
          </tr>
        </thead>
        <tbody className="font-mono text-slate-200">
          {rows.map(([sym, a]) => (
            <tr key={sym} className="border-b border-slate-800/60 [&>td]:px-3 [&>td]:py-1.5">
              <td className="font-sans font-medium text-slate-100">{sym}</td>
              <td className="text-slate-500">{a.span || '—'}</td>
              <td className="text-right">{a.trades ?? 0}</td>
              <td className="text-right text-slate-400">
                {a.long ?? 0}/{a.short ?? 0}
              </td>
              <td className="text-right">{a.win_rate_pct ?? 0}</td>
              <td className="text-right">{a.profit_factor ?? '—'}</td>
              <td className={cn('text-right font-semibold', pnlClass(a.net_rupees))}>
                {rupees(a.net_rupees)}
              </td>
              <td className={cn('text-right', pnlClass(a.expectancy))}>{rupees(a.expectancy)}</td>
              <td className="text-right text-[var(--down)]/80">{rupees(a.max_drawdown)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}


export function FuturesPanel() {
  const poll = usePollMs(20_000)
  const status = useQuery({
    queryKey: ['futures-status'],
    queryFn: () => api<LaneStatus>('/api/futures/status'),
    refetchInterval: poll,
    placeholderData: keepPreviousData,
  })
  const backtest = useQuery({
    queryKey: ['futures-backtest'],
    queryFn: () => api<BacktestResponse>('/api/futures/backtest'),
    staleTime: Infinity,
  })
  const [period, setPeriod] = useState<PeriodKey>('month')
  const [range, setRange] = useState<DateRange>({ from: '', to: '' })
  const journal = useFuturesJournal(true)

  const bt = backtest.data?.stock
  const perStock = Object.entries(bt?.per_stock ?? {}).sort(
    (a, b) => (b[1].net_rupees ?? 0) - (a[1].net_rupees ?? 0),
  )
  const p = bt?.portfolio ?? {}
  const idx = backtest.data?.index?.per_instrument ?? backtest.data?.index?.instruments ?? {}
  const sweep = bt?.half_spread_sweep ?? {}

  return (
    <div className="space-y-6">
      {/* live paper lane */}
      <section className={cn(fx.panel, 'space-y-3 p-4')}>
        <h3 className="text-sm font-bold text-slate-100">Directional index-futures — paper lane</h3>
        <LaneCard title="Directional futures (paper)" data={status.data} />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className={fx.cardLabel}>Trade history</p>
          <PeriodBar period={period} onPeriodChange={setPeriod} range={range} onRangeChange={setRange} />
        </div>
        {/* same shared table + entry/exit times as every other section (Index,
            Crypto, Commodities) and the combined Trade History tab */}
        <TradeLogTable
          logRows={journal.logRows}
          trades={journal.trades}
          period={period}
          range={range}
          hideTitle
        />
      </section>

      {/* stock-futures backtest */}
      <section className={cn(fx.panel, 'space-y-4 p-4')}>
        <h3 className="text-sm font-bold text-slate-100">
          Stock futures — intraday replay (decision gate)
        </h3>
        {!bt || !bt.per_stock || !perStock.length ? (
          <p className="text-xs text-slate-500">
            No backtest run yet. Run <code className="text-slate-400">python -m
            scripts.backtest_stock_futures</code>.
          </p>
        ) : (
          <>
            <VerdictBanner bt={bt} />
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
              <StatTile label="Portfolio net" value={rupees(p.net_rupees)} valueClass={pnlClass(p.net_rupees)} />
              <StatTile label="Trades" value={String(p.trades ?? 0)} />
              <StatTile label="Win rate" value={`${p.win_rate_pct ?? 0}%`} />
              <StatTile label="Gross" value={rupees(p.gross_rupees)} valueClass={pnlClass(p.gross_rupees)} />
              <StatTile label="Broker charges" value={rupees(p.friction_rupees)} />
              <StatTile
                label="Halves 1 / 2"
                value={`${rupees(bt.halves?.first)} / ${rupees(bt.halves?.second)}`}
              />
            </div>
            <PerStockTable rows={perStock} />
            {Object.keys(sweep).length ? (
              <p className="text-[11px] text-slate-500">
                Half-spread sensitivity (portfolio net):{' '}
                {Object.entries(sweep)
                  .sort((a, b) => Number(a[0]) - Number(b[0]))
                  .map(([bp, v]) => `${bp}bp ${rupees(v)}`)
                  .join('  ·  ')}
              </p>
            ) : null}
          </>
        )}
        {Object.keys(idx).length ? (
          <p className="text-[11px] text-slate-500">
            Index replay for comparison:{' '}
            {Object.entries(idx)
              .map(([k, a]) => `${k} ${rupees(a.net_rupees)}`)
              .join('  ·  ')}
          </p>
        ) : (
          <p className="text-[11px] text-slate-500">
            Index replay (recorded, ~2yr): NIFTY −₹62,827 · BANKNIFTY −₹186,275 · SENSEX −₹81,932 —
            broker charges run about 10× the gross profit before costs.
          </p>
        )}
      </section>
    </div>
  )
}
