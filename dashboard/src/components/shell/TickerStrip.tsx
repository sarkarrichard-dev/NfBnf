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

/** A thin, always-visible market-pulse bar under the header — the one thing
 *  every Bloomberg-style terminal has and this dashboard didn't: live
 *  numbers ticking without opening a page. Polls regardless of tab focus,
 *  matching every other live figure in this app (Richard, 2026-09-16). */
export function TickerStrip() {
  const poll = usePollMs(15_000)
  const { data } = useQuery({
    queryKey: ['ticker'],
    queryFn: () => api<{ rows: Row[] }>('/api/ticker'),
    refetchInterval: poll,
  })

  const rows = data?.rows ?? []

  return (
    <div className="border-b border-[var(--hair)] bg-black/40 px-4 md:px-6">
      <div className="mx-auto flex h-7 max-w-[92rem] items-center gap-5 overflow-x-auto font-mono text-[11px] tabular-nums">
        {rows.length === 0 ? <span className="text-slate-600">syncing quotes…</span> : null}
        {rows.map((r) => (
          <span key={r.symbol} className="flex shrink-0 items-center gap-1.5">
            <span className="text-slate-500">{r.symbol}</span>
            <span className="text-slate-200">{fmtPrice(r)}</span>
            {r.change_pct != null ? (
              <span
                className={cn(
                  r.change_pct >= 0 ? 'text-[var(--up)]' : 'text-[var(--down)]',
                )}
              >
                {r.change_pct >= 0 ? '+' : ''}
                {r.change_pct.toFixed(2)}%
              </span>
            ) : null}
          </span>
        ))}
      </div>
    </div>
  )
}
