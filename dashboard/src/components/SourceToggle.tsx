import { useCallback, useState } from 'react'
import { cn } from '../lib/cn'

/** All | Index | Crypto | Futures — which trades the Reports and Trade-history
 *  pages show. Persisted per browser so the choice survives a reload. */
export type TradeSource = 'all' | 'index' | 'crypto' | 'futures'

const KEY = 'qh.tradeSource'
const OPTS: { id: TradeSource; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'index', label: 'Index' },
  { id: 'crypto', label: 'Crypto' },
  { id: 'futures', label: 'Futures' },
]

export function useTradeSource(): [TradeSource, (s: TradeSource) => void] {
  const [src, setSrc] = useState<TradeSource>(() => {
    try {
      const v = localStorage.getItem(KEY)
      if (v === 'all' || v === 'index' || v === 'crypto' || v === 'futures') return v
    } catch {
      /* ignore */
    }
    return 'all'
  })
  const set = useCallback((s: TradeSource) => {
    setSrc(s)
    try {
      localStorage.setItem(KEY, s)
    } catch {
      /* ignore */
    }
  }, [])
  return [src, set]
}

export function SourceToggle({
  value,
  onChange,
}: {
  value: TradeSource
  onChange: (s: TradeSource) => void
}) {
  return (
    <div
      className="flex items-center gap-0.5 rounded-lg border border-white/[0.06] bg-white/[0.02] p-0.5"
      role="tablist"
      aria-label="Trade source"
    >
      {OPTS.map((o) => (
        <button
          key={o.id}
          type="button"
          role="tab"
          aria-selected={value === o.id}
          onClick={() => onChange(o.id)}
          className={cn(
            'rounded-md px-3 py-1 text-xs font-medium transition',
            value === o.id ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}
