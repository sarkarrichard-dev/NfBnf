import { memo, useEffect, useState } from 'react'
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
  dhanReady?: boolean
}

function istParts() {
  const s = new Date().toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
  })
  const hour24 = Number(
    new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Kolkata',
      hour: 'numeric',
      hour12: false,
    }).format(new Date()),
  )
  return { clock: s, hour24 }
}

function greeting(hour: number): string {
  if (hour < 12) return 'Good morning'
  if (hour < 17) return 'Good afternoon'
  return 'Good evening'
}

export const Header = memo(function Header({ tradingMode, market, dhanReady }: Props) {
  const [{ clock, hour24 }, setTime] = useState(istParts)

  useEffect(() => {
    const id = setInterval(() => setTime(istParts()), 1000)
    return () => clearInterval(id)
  }, [])

  const phaseOpen = market?.is_open
  const phaseLabel =
    market?.message ||
    (phaseOpen ? 'Market open' : market?.phase === 'square_off' ? 'Square-off' : 'Market closed')

  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <p className="text-sm text-slate-400">{greeting(hour24)}</p>
        <h1 className="mt-0.5 text-2xl font-semibold tracking-tight text-slate-50">
          Index Options AI
        </h1>
        <p className="mt-1 text-[13px] text-slate-500">
          {phaseLabel} · CPR + EMA + OI · NIFTY &amp; Bank Nifty FNO
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-lg border border-white/[0.06] bg-white/[0.03] px-3 py-1.5 font-mono text-xs text-slate-300">
          <span className="text-slate-500">IST </span>
          <time>{clock}</time>
        </span>
        <span
          className={cn(
            'rounded-lg border px-3 py-1.5 text-xs font-medium',
            phaseOpen
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
              : 'border-white/[0.06] bg-white/[0.03] text-slate-400',
          )}
        >
          {phaseOpen ? 'Market open' : 'Market closed'}
        </span>
        <span className="rounded-lg border border-blue-500/40 bg-blue-600/15 px-3 py-1.5 text-xs font-semibold text-blue-300">
          {tradingMode || '—'}
        </span>
        <span
          className={cn(
            'rounded-lg border px-3 py-1.5 text-xs font-medium',
            dhanReady
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
              : 'border-amber-500/30 bg-amber-500/10 text-amber-300',
          )}
          title="Dhan API connection"
        >
          Dhan {dhanReady ? 'OK' : '—'}
        </span>
      </div>
    </header>
  )
})
