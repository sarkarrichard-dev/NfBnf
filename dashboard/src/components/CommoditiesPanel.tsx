import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePollMs } from '../hooks/usePageVisible'
import { useCommoditiesJournal } from '../hooks/useCommoditiesJournal'
import { cn } from '../lib/cn'
import { fx } from '../lib/theme'
import type { DateRange, PeriodKey } from '../types/analytics'
import { PeriodBar } from './PeriodBar'
import { TradeLogTable } from './TradeLogTable'

type OpenPos = {
  dir?: string
  entry?: number
  stop?: number
  entry_time?: string
  trend_reason?: string
  mark?: number | null
  unrealized_rupees?: number | null
  unrealized_pct?: number | null
} | null

type Trade = {
  instrument?: string
  label?: string
  direction?: string
  entry?: number
  exit?: number
  exit_reason?: string
  net_rupees?: number
  exit_time?: string
}

type Status = {
  enabled: boolean
  symbols: string[]
  lots: number
  contracts: Record<
    string,
    {
      label?: string
      expiry?: string | null
      trading_symbol?: string | null
      multiplier?: number | null
      initial_stop_pct?: number
      trail_pct?: number
      risk_source?: 'measured' | 'default'
    }
  >
  open_positions: Record<string, OpenPos>
  today: { closed: number; net_rupees: number; wins: number; open_unrealized_rupees?: number }
  all_time: { closed: number; net_rupees: number }
  recent_trades: Trade[]
  generated_at_ist?: string
}

const rupee = (v?: number | null) =>
  v == null ? '—' : `${v < 0 ? '-' : ''}₹${Math.abs(Math.round(v)).toLocaleString('en-IN')}`
const pnlClass = (v?: number | null) =>
  v == null || v === 0 ? 'text-slate-300' : v > 0 ? 'text-[var(--up)]' : 'text-[var(--down)]'

function Stat({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className={fx.card}>
      <p className={fx.cardLabel}>{label}</p>
      <p className={cn(fx.cardValue, cls || 'text-slate-100')}>{value}</p>
    </div>
  )
}

export function CommoditiesPanel() {
  const poll = usePollMs(30_000)
  const q = useQuery({
    queryKey: ['commodities', 'status'],
    queryFn: () => api<Status>('/api/commodities/status'),
    refetchInterval: poll,
    placeholderData: keepPreviousData,
  })
  const s = q.data
  const opens = Object.entries(s?.open_positions ?? {}).filter(([, p]) => p)

  const [period, setPeriod] = useState<PeriodKey>('month')
  const [range, setRange] = useState<DateRange>({ from: '', to: '' })
  const journal = useCommoditiesJournal(true)

  return (
    <div className={cn(fx.panel, 'space-y-5 p-4')}>
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-base font-bold text-slate-100">MCX commodity futures</h3>
          <p className="mt-0.5 text-[11px] text-slate-500">
            Directional (CPR + EMA + Supertrend) · {s?.lots ?? 1} lot · 09:00–23:30 IST · paper
          </p>
        </div>
        <span
          className={cn(
            'rounded-full px-2 py-0.5 text-[10px] font-semibold',
            s?.enabled ? 'bg-[var(--up)]/15 text-[var(--up)]' : 'bg-white/10 text-slate-400',
          )}
        >
          {s?.enabled ? 'running · paper' : 'off'}
        </span>
      </header>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <Stat label="Today closed" value={String(s?.today.closed ?? 0)} />
        <Stat
          label="Today net"
          value={rupee(s?.today.net_rupees)}
          cls={pnlClass(s?.today.net_rupees)}
        />
        <Stat label="Open" value={String(opens.length)} />
        <Stat
          label="Open MTM"
          value={rupee(s?.today.open_unrealized_rupees)}
          cls={pnlClass(s?.today.open_unrealized_rupees)}
        />
        <Stat
          label="All-time net"
          value={rupee(s?.all_time.net_rupees)}
          cls={pnlClass(s?.all_time.net_rupees)}
        />
      </div>

      <div>
        <p className={fx.cardLabel}>Contracts</p>
        <div className="mt-1 grid gap-1.5 sm:grid-cols-2">
          {Object.entries(s?.contracts ?? {}).map(([k, c]) => (
            <div
              key={k}
              className="flex flex-col gap-0.5 rounded-lg border border-[var(--hair-soft)] px-3 py-1.5"
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-xs font-semibold text-slate-200">{c.label || k}</span>
                <span className="font-mono text-[10.5px] text-slate-500">
                  {c.trading_symbol || k} · exp {c.expiry ?? '—'}
                </span>
              </div>
              {c.initial_stop_pct != null ? (
                <span className="font-mono text-[10px] text-slate-500">
                  stop {c.initial_stop_pct}% · trail {c.trail_pct}%{' '}
                  <span className={c.risk_source === 'measured' ? 'text-[var(--up)]' : 'text-slate-600'}>
                    ({c.risk_source === 'measured' ? "this contract's own volatility" : 'default — not yet measured'})
                  </span>
                </span>
              ) : null}
            </div>
          ))}
        </div>
      </div>

      {opens.length ? (
        <div>
          <p className={fx.cardLabel}>Open positions</p>
          <ul className="mt-1 space-y-1">
            {opens.map(([k, p]) => (
              <li
                key={k}
                className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5 rounded-lg bg-white/[0.02] px-3 py-1.5 text-xs"
              >
                <span className="font-semibold text-slate-100">
                  {k} <span className="text-slate-400">{(p?.dir || '').toUpperCase()}</span>
                </span>
                <span className="font-mono text-[11px] text-slate-400">
                  @ {p?.entry?.toFixed(1)} · SL {p?.stop?.toFixed(1)}
                  {p?.mark != null ? <> · LTP {p.mark.toFixed(1)}</> : null}
                </span>
                <span className={cn('ml-auto font-mono text-[11px] font-semibold', pnlClass(p?.unrealized_rupees))}>
                  {p?.unrealized_rupees != null
                    ? `${rupee(p.unrealized_rupees)}${p.unrealized_pct != null ? ` (${p.unrealized_pct > 0 ? '+' : ''}${p.unrealized_pct.toFixed(2)}%)` : ''}`
                    : 'no live mark'}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className={fx.cardLabel}>Trade history</p>
          <PeriodBar period={period} onPeriodChange={setPeriod} range={range} onRangeChange={setRange} />
        </div>
        {/* same shared table + entry/exit times as every other section (Index,
            Crypto, Futures) and the combined Trade History tab */}
        <TradeLogTable
          logRows={journal.logRows}
          trades={journal.trades}
          period={period}
          range={range}
          hideTitle
        />
      </div>

      <p className="text-[11px] leading-relaxed text-slate-600">
        Runs the same directional signal as the index futures lane on MCX mini/micro contracts, the
        evening the equity scanner is shut. Paper only, never armed — the signal backtests negative on
        index/stock futures; this is a forward-record lane. Charges are the MCX schedule (₹20/order +
        0.0021% txn + 0.01% CTT + SEBI + stamp + GST).
      </p>
    </div>
  )
}
