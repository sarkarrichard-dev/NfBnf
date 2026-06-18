import { useEffect, useState } from 'react'
import { cn } from '../lib/cn'
import { fx } from '../lib/theme'

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

function formatIstClock(): string {
  return new Date().toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
  })
}

export function Header({ tradingMode, market, dhanReady }: Props) {
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
    <header className={cn(fx.panel, 'mb-6 p-5')}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-transparent bg-gradient-to-r from-cyan-100 via-white to-violet-200 bg-clip-text">
            Index Options AI
          </h1>
          <p className="mt-1 text-sm text-cyan-200/45">
            CPR + EMA + OI · NIFTY &amp; Bank Nifty FNO
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div
            className="rounded-xl border border-cyan-500/20 bg-black/30 px-3 py-2 font-mono text-sm text-cyan-100/90 shadow-[0_0_20px_-8px_rgba(34,211,238,0.4)]"
          >
            <span className="text-cyan-400/50">IST </span>
            <time>{clock}</time>
          </div>
          <span
            className={cn(
              'rounded-full border px-3 py-1 text-xs font-medium',
              phaseOpen
                ? 'border-emerald-400/40 bg-emerald-400/10 text-emerald-300 shadow-[0_0_14px_-6px_rgba(52,211,153,0.5)]'
                : 'border-slate-600/50 bg-black/20 text-slate-400',
            )}
          >
            {phaseLabel}
          </span>
          <span
            className={cn(
              'rounded-full border px-3 py-1 text-xs font-semibold',
              fx.tabActive,
            )}
          >
            {tradingMode || '—'}
          </span>
          <span
            className={cn(
              'rounded-full border px-3 py-1 text-xs font-medium',
              dhanReady
                ? 'border-emerald-400/35 bg-emerald-400/10 text-emerald-300'
                : 'border-amber-400/35 bg-amber-400/10 text-amber-200',
            )}
            title="Dhan API connection"
          >
            Dhan {dhanReady ? 'OK' : '—'}
          </span>
        </div>
      </div>
    </header>
  )
}
