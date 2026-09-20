import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { usePollMs } from '../../hooks/usePageVisible'
import { cn } from '../../lib/cn'

type Row = { symbol: string; price: number; change_pct: number | null }

const INDEX_SYMBOLS = new Set(['NIFTY', 'BANKNIFTY', 'SENSEX'])

function fmtPrice(row: Row): string {
  const isIndex = INDEX_SYMBOLS.has(row.symbol)
  const n = row.price.toLocaleString(isIndex ? 'en-IN' : 'en-US', {
    maximumFractionDigits: isIndex ? 2 : row.price < 100 ? 4 : 2,
  })
  return isIndex ? n : `$${n}`
}

function Tile({ r }: { r: Row }) {
  return (
    <span className="flex shrink-0 items-center gap-2 px-5">
      <span className="font-semibold text-slate-400">{r.symbol}</span>
      <span className="font-bold text-slate-100">{fmtPrice(r)}</span>
      {r.change_pct != null ? (
        <span
          className={cn(
            'font-semibold',
            r.change_pct >= 0 ? 'text-[var(--up)]' : 'text-[var(--down)]',
          )}
        >
          {r.change_pct >= 0 ? '+' : ''}
          {r.change_pct.toFixed(2)}%
        </span>
      ) : null}
      <span className="text-[var(--hair)]">•</span>
    </span>
  )
}

/** A turntable-style live market-pulse ribbon under the header — the one
 *  thing every Bloomberg-style terminal has and this dashboard didn't: live
 *  numbers scrolling past without opening a page. Polls regardless of tab
 *  focus, matching every other live figure in this app (Richard,
 *  2026-09-16). The track is rendered twice back to back and slid exactly
 *  one copy's width so the loop is seamless; hover pauses it to read a
 *  number. Speed scales with row count so it never feels rushed or crawls. */
export function TickerStrip() {
  const poll = usePollMs(15_000)
  const { data } = useQuery({
    queryKey: ['ticker'],
    queryFn: () => api<{ rows: Row[] }>('/api/ticker'),
    refetchInterval: poll,
  })

  const rows = data?.rows ?? []
  const duration = Math.max(18, rows.length * 4)

  if (!rows.length) {
    return (
      <div className="border-b border-[var(--hair)] bg-black/40 px-4 md:px-6">
        <div className="mx-auto flex h-10 max-w-[92rem] items-center font-mono text-sm text-slate-600">
          syncing quotes…
        </div>
      </div>
    )
  }

  return (
    <div className="ticker-wrap overflow-hidden border-b border-[var(--hair)] bg-black/40">
      <div
        className="ticker-track flex h-10 w-max items-center font-mono text-sm tabular-nums"
        style={{ '--ticker-duration': `${duration}s` } as React.CSSProperties}
      >
        {[...rows, ...rows].map((r, i) => (
          <Tile key={i} r={r} />
        ))}
      </div>
    </div>
  )
}
