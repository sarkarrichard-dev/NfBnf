import { useEffect, useState } from 'react'
import { cn } from '../lib/cn'

type Market = {
  message?: string
  now_ist?: string
  is_open?: boolean
  phase?: string
}

type Props = {
  tradingMode?: string
  market?: Market
}

function formatIstClock(): string {
  return new Date().toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
  })
}

export function Header({ tradingMode, market }: Props) {
  const [clock, setClock] = useState(formatIstClock)

  useEffect(() => {
    const id = setInterval(() => setClock(formatIstClock()), 1000)
    return () => clearInterval(id)
  }, [])

  const phaseOpen = market?.is_open
  const phaseLabel =
    market?.message ||
    (phaseOpen ? 'Market open' : market?.phase === 'square_off' ? 'Square-off' : 'Market closed')

  return (
    <header className="mb-6 rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-100">Index Options AI</h1>
          <p className="mt-1 text-sm text-slate-400">
            CPR + EMA + OI · NIFTY &amp; Bank Nifty FNO
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm">
            <span className="text-slate-500">IST </span>
            <time>{clock}</time>
          </div>
          <span
            className={cn(
              'rounded-full border px-3 py-1 text-xs font-medium',
              phaseOpen
                ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                : 'border-slate-600 bg-slate-950 text-slate-400',
            )}
          >
            {phaseLabel}
          </span>
          <span className="rounded-full border border-cyan-500/30 bg-cyan-500/10 px-3 py-1 text-xs font-semibold text-cyan-200">
            {tradingMode || '—'}
          </span>
        </div>
      </div>
    </header>
  )
}
