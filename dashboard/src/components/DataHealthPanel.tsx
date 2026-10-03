import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { usePollMs } from '../hooks/usePageVisible'
import { api } from '../lib/api'
import { cn } from '../lib/cn'
import { fx } from '../lib/theme'
import { StatTile } from './ui/StatTile'

// The server decides every status word (ok / slow / stale ...) from its own clock
// and the NSE holiday calendar; this file only maps a word to a colour and text.
type Status = 'ok' | 'slow' | 'stale' | 'closed' | 'off' | 'none'

type Line = {
  status: Status
  age_seconds: number | null
  last_seen: string | null
  by_index: Record<string, number | null>
}

type DataHealth = {
  as_of_display: string
  market_open: boolean
  square_off_window: boolean
  ticks: { status: Status; age_seconds: number | null }
  chain: Line
  spread: Line
  feed: { status: Status; connected: boolean; reconnects: number }
}

const TONE: Record<Status, string> = {
  ok: 'text-[var(--up)]',
  slow: 'text-[var(--warn)]',
  stale: 'text-[var(--down)]',
  closed: 'text-slate-400',
  off: 'text-slate-400',
  none: 'text-slate-400',
}

function ageText(sec: number | null): string {
  if (sec == null) return '—'
  if (sec < 90) return `${Math.round(sec)} s ago`
  if (sec < 5400) return `${Math.round(sec / 60)} min ago`
  if (sec < 172800) return `${Math.round(sec / 3600)} h ago`
  return `${Math.round(sec / 86400)} days ago`
}

const PRICES_SUB: Record<Status, string> = {
  off: 'Switched off — stops are checked every 20 seconds',
  closed: 'Market closed',
  ok: 'Prices arriving',
  slow: 'Prices running late',
  stale: 'No new prices — stops checked every 20 seconds',
  none: 'No prices received yet',
}

const LINE_WORDS: Record<Status, string> = {
  ok: 'Up to date',
  slow: 'Running late',
  stale: 'Not updating',
  none: 'No readings found',
  off: 'Off',
  closed: 'Market closed',
}

function lineSub(line: Line, pausedForClose: boolean): string {
  const word = pausedForClose ? 'Paused for the close' : LINE_WORDS[line.status]
  return word + (line.last_seen ? ` · last ${line.last_seen}` : '')
}

function perIndex(line: Line): string {
  return Object.entries(line.by_index)
    .map(([index, age]) => `${index} ${ageText(age)}`)
    .join(' · ')
}

function feedSub(d: DataHealth['feed']): string {
  switch (d.status) {
    case 'off':
      return 'Turned off in settings — a change needs a restart'
    case 'closed':
      return 'Market closed'
    case 'ok':
      return `Working · ${d.reconnects} reconnects since start`
    case 'slow':
      return 'Reconnecting — stops checked every 20 seconds'
    default:
      return 'No status yet'
  }
}

export function DataHealthPanel() {
  const poll = usePollMs(20_000)
  const q = useQuery({
    queryKey: ['data-health'],
    queryFn: () => api<DataHealth>('/api/data-health'),
    refetchInterval: poll,
    staleTime: 10_000,
    placeholderData: keepPreviousData,
  })
  const d = q.data

  return (
    <section className={cn(fx.panel, 'mb-5 p-4')}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-bold text-slate-100">Data health</h3>
        {d ? (
          <span className="font-mono text-[11px] tabular-nums text-slate-500">
            {`${d.market_open ? 'Market open' : 'Market closed'} · checked ${d.as_of_display}`}
          </span>
        ) : null}
      </div>
      {q.isError && !d ? (
        <p className="mt-3 text-[12.5px] text-slate-500">
          Data health not available yet — the app may need a restart.
        </p>
      ) : !d ? (
        <p className="mt-3 text-[12.5px] text-slate-500">Loading…</p>
      ) : (
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <StatTile
            label="Live prices"
            value={d.ticks.status === 'off' ? 'Off' : ageText(d.ticks.age_seconds)}
            sub={PRICES_SUB[d.ticks.status]}
            valueClass={TONE[d.ticks.status]}
          />
          <StatTile
            label="Option chain"
            value={ageText(d.chain.age_seconds)}
            sub={lineSub(d.chain, d.chain.status === 'closed' && d.market_open && d.square_off_window)}
            valueClass={TONE[d.chain.status]}
          />
          <StatTile
            label="Option spreads"
            value={ageText(d.spread.age_seconds)}
            sub={lineSub(d.spread, false)}
            valueClass={TONE[d.spread.status]}
          />
          <StatTile
            label="Dhan live feed"
            value={d.feed.status === 'off' ? 'Off' : d.feed.connected ? 'Connected' : 'Not connected'}
            sub={feedSub(d.feed)}
            valueClass={TONE[d.feed.status]}
          />
        </div>
      )}
      {d ? (
        <>
          <p className="mt-2 font-mono text-[11px] tabular-nums text-slate-500">
            {`Option chain — ${perIndex(d.chain)}`}
          </p>
          <p className="mt-1 font-mono text-[11px] tabular-nums text-slate-500">
            {`Option spreads — ${perIndex(d.spread)}`}
          </p>
        </>
      ) : null}
    </section>
  )
}
