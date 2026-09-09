import { useMemo } from 'react'
import { cn } from '../../lib/cn'
import { fx } from '../../lib/theme'
import { pnlClass } from '../../lib/pnl'
import {
  useStrategyPerformance,
  type PerfRow,
  type PerfTotals,
} from '../../hooks/useStrategyPerformance'

function fmt(v: number | null | undefined, currency: 'INR' | 'USD', signed = false): string {
  if (v == null || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  const sign = signed && n > 0 ? '+' : n < 0 ? '-' : ''
  const sym = currency === 'INR' ? '₹' : '$'
  const digits = currency === 'INR' ? 0 : 2
  return `${sign}${sym}${Math.abs(n).toLocaleString('en-IN', { maximumFractionDigits: digits })}`
}

function pct(v: number | null | undefined): string {
  return v == null ? '—' : `${Math.round(Number(v) * 100)}%`
}

type Group = {
  strategy: string
  rollup: {
    trades: number
    wins: number
    losses: number
    gross: number
    charges: number
    slippage: number
    net: number
  }
  rows: PerfRow[]
}

function groupByStrategy(rows: PerfRow[]): Group[] {
  const map = new Map<string, Group>()
  for (const r of rows) {
    let g = map.get(r.strategy)
    if (!g) {
      g = {
        strategy: r.strategy,
        rollup: { trades: 0, wins: 0, losses: 0, gross: 0, charges: 0, slippage: 0, net: 0 },
        rows: [],
      }
      map.set(r.strategy, g)
    }
    g.rows.push(r)
    g.rollup.trades += r.trades
    g.rollup.wins += r.wins
    g.rollup.losses += r.losses
    g.rollup.gross += r.gross
    g.rollup.charges += r.charges
    g.rollup.slippage += r.slippage
    g.rollup.net += r.net
  }
  const groups = [...map.values()]
  for (const g of groups) g.rows.sort((a, b) => b.net - a.net)
  groups.sort((a, b) => b.rollup.net - a.rollup.net)
  return groups
}

const HEAD = ['Strategy', 'Instrument', 'Mode', 'Trades', 'Win', 'Gross', 'Charges', 'Slippage', 'Net', 'Per trade']

function Cell({ children, className }: { children?: React.ReactNode; className?: string }) {
  return <td className={cn('px-3 py-1.5 tabular-nums', className)}>{children}</td>
}

function VenueTable({
  title,
  rows,
  totals,
  currency,
}: {
  title: string
  rows: PerfRow[]
  totals: PerfTotals
  currency: 'INR' | 'USD'
}) {
  const groups = useMemo(() => groupByStrategy(rows), [rows])

  return (
    <section className={cn(fx.panel, 'overflow-hidden')}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-[var(--hair)] px-4 py-3">
        <h3 className="text-sm font-bold text-slate-100">{title}</h3>
        <div className="flex flex-wrap gap-x-4 font-mono text-[11px] text-slate-400">
          <span>
            {totals.strategies} strategies · {totals.instruments} instruments · {totals.trades} trades
          </span>
          <span>
            net <span className={cn('font-semibold', pnlClass(totals.net))}>{fmt(totals.net, currency, true)}</span>
          </span>
          <span>charges {fmt(totals.charges, currency)}</span>
        </div>
      </div>

      {rows.length === 0 ? (
        <p className="px-4 py-6 text-sm text-slate-500">No closed trades yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-[12.5px]">
            <thead>
              <tr className="border-b border-[var(--hair)] font-mono text-[10.5px] uppercase tracking-[0.06em] text-slate-500">
                {HEAD.map((h) => (
                  <th
                    key={h}
                    className={cn('px-3 py-2 font-medium', h !== 'Strategy' && h !== 'Instrument' && 'text-right')}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            {groups.map((g) => (
              <tbody key={g.strategy} className="border-b border-[var(--hair)] last:border-0">
                <tr className="bg-white/[0.02] font-semibold text-slate-100">
                  <Cell className="whitespace-nowrap">{g.strategy}</Cell>
                  <Cell>—</Cell>
                  <Cell />
                  <Cell className="text-right">{g.rollup.trades}</Cell>
                  <Cell className="text-right">
                    {pct(g.rollup.trades ? g.rollup.wins / g.rollup.trades : null)}
                  </Cell>
                  <Cell className={cn('text-right', pnlClass(g.rollup.gross))}>
                    {fmt(g.rollup.gross, currency, true)}
                  </Cell>
                  <Cell className="text-right text-slate-400">{fmt(g.rollup.charges, currency)}</Cell>
                  <Cell className="text-right text-slate-400">{fmt(g.rollup.slippage, currency)}</Cell>
                  <Cell className={cn('text-right', pnlClass(g.rollup.net))}>
                    {fmt(g.rollup.net, currency, true)}
                  </Cell>
                  <Cell className={cn('text-right', pnlClass(g.rollup.net))}>
                    {fmt(g.rollup.trades ? g.rollup.net / g.rollup.trades : 0, currency, true)}
                  </Cell>
                </tr>
                {g.rows.map((r) => (
                  <tr key={`${r.instrument}-${r.mode}`} className="text-slate-300">
                    <Cell />
                    <Cell className="whitespace-nowrap text-slate-200">{r.instrument}</Cell>
                    <Cell>
                      <span
                        className={cn(
                          'rounded px-1.5 py-0.5 font-mono text-[10px]',
                          r.mode === 'LIVE'
                            ? 'bg-[var(--acc)]/15 text-[var(--acc)]'
                            : 'bg-white/[0.04] text-slate-400',
                        )}
                      >
                        {r.mode}
                      </span>
                    </Cell>
                    <Cell className="text-right">{r.trades}</Cell>
                    <Cell className="text-right">{pct(r.win_rate)}</Cell>
                    <Cell className={cn('text-right', pnlClass(r.gross))}>{fmt(r.gross, currency, true)}</Cell>
                    <Cell className="text-right text-slate-500">
                      {fmt(r.charges, currency)}
                      {r.priced_pct != null && r.priced_pct < 0.95 ? (
                        <span className="ml-1 text-amber-500" title="some rows had no premium to cost">
                          ~
                        </span>
                      ) : null}
                    </Cell>
                    <Cell className="text-right text-slate-500">{fmt(r.slippage, currency)}</Cell>
                    <Cell className={cn('text-right font-semibold', pnlClass(r.net))}>
                      {fmt(r.net, currency, true)}
                    </Cell>
                    <Cell className={cn('text-right', pnlClass(r.expectancy))}>
                      {fmt(r.expectancy, currency, true)}
                    </Cell>
                  </tr>
                ))}
              </tbody>
            ))}
          </table>
        </div>
      )}
    </section>
  )
}

export function StrategyPerformancePage() {
  const q = useStrategyPerformance(true)
  const data = q.data

  const best = useMemo(() => {
    if (!data) return null
    const all = [...groupByStrategy(data.india.rows), ...groupByStrategy(data.crypto.rows)]
    const withCur = all.map((g) => ({
      g,
      currency: (data.india.rows.some((r) => r.strategy === g.strategy) ? 'INR' : 'USD') as 'INR' | 'USD',
    }))
    const pos = withCur.filter((x) => x.g.rollup.net > 0).sort((a, b) => b.g.rollup.net - a.g.rollup.net)
    const neg = withCur.filter((x) => x.g.rollup.net < 0).sort((a, b) => a.g.rollup.net - b.g.rollup.net)
    return { winners: pos.slice(0, 3), losers: neg.slice(0, 3) }
  }, [data])

  return (
    <div className="space-y-4">
      {q.isError ? (
        <p className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
          {q.error instanceof Error ? q.error.message : 'Failed to load'}
        </p>
      ) : null}

      {best && (best.winners.length > 0 || best.losers.length > 0) ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className={cn(fx.panel, 'p-3')}>
            <p className="mb-2 font-mono text-[10.5px] uppercase tracking-[0.09em] text-emerald-400">
              Making money
            </p>
            {best.winners.length === 0 ? (
              <p className="text-xs text-slate-500">Nothing is net positive.</p>
            ) : (
              <ul className="space-y-1 text-[12.5px]">
                {best.winners.map(({ g, currency }) => (
                  <li key={g.strategy} className="flex justify-between gap-2">
                    <span className="text-slate-200">{g.strategy}</span>
                    <span className="font-mono font-semibold text-emerald-400">
                      {fmt(g.rollup.net, currency, true)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className={cn(fx.panel, 'p-3')}>
            <p className="mb-2 font-mono text-[10.5px] uppercase tracking-[0.09em] text-red-400">
              Losing money
            </p>
            {best.losers.length === 0 ? (
              <p className="text-xs text-slate-500">Nothing is net negative.</p>
            ) : (
              <ul className="space-y-1 text-[12.5px]">
                {best.losers.map(({ g, currency }) => (
                  <li key={g.strategy} className="flex justify-between gap-2">
                    <span className="text-slate-200">{g.strategy}</span>
                    <span className="font-mono font-semibold text-red-400">
                      {fmt(g.rollup.net, currency, true)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      ) : null}

      {data ? (
        <>
          <VenueTable
            title="Indian options (Dhan)"
            rows={data.india.rows}
            totals={data.india.totals}
            currency="INR"
          />
          <VenueTable
            title="Crypto (Delta Exchange)"
            rows={data.crypto.rows}
            totals={data.crypto.totals}
            currency="USD"
          />
          <p className="px-1 text-[11px] leading-relaxed text-slate-500">{data.note}</p>
        </>
      ) : (
        <p className="text-sm text-slate-500">Loading…</p>
      )}
    </div>
  )
}
