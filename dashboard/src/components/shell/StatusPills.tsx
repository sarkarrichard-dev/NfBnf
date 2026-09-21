import { memo, useEffect, useState } from 'react'
import { cn } from '../../lib/cn'

type Market = { message?: string; is_open?: boolean; phase?: string }

function istClock(): string {
  return new Date().toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  })
}

function Pill({
  children,
  tone = 'idle',
  title,
}: {
  children: React.ReactNode
  tone?: 'idle' | 'good' | 'warn' | 'accent'
  title?: string
}) {
  return (
    <span
      title={title}
      className={cn(
        'hidden items-center rounded-full border px-2.5 py-1 text-[11px] font-medium sm:inline-flex',
        tone === 'good' &&
          'border-[var(--up)]/30 bg-[var(--up)]/10 text-[var(--up)]',
        tone === 'warn' &&
          'border-[var(--warn)]/30 bg-[var(--warn)]/10 text-[var(--warn)]',
        tone === 'accent' &&
          'border-[var(--acc)]/40 bg-[var(--acc-soft)] text-[var(--acc)]',
        tone === 'idle' && 'border-[var(--hair)] bg-white/[0.03] text-slate-400',
      )}
    >
      {children}
    </span>
  )
}

/** Live status shown top-right in the shell: IST clock, market phase, trading
 *  mode, broker health. */
export const StatusPills = memo(function StatusPills({
  tradingMode,
  market,
  dhanReady,
}: {
  tradingMode?: string
  market?: Market
  dhanReady?: boolean
}) {
  const [clock, setClock] = useState(istClock)
  useEffect(() => {
    const id = setInterval(() => setClock(istClock()), 20_000)
    return () => clearInterval(id)
  }, [])

  const open = market?.is_open
  return (
    <>
      <Pill title="Indian Standard Time">
        <span className="font-mono tabular-nums">{clock} IST</span>
      </Pill>
      <Pill tone={open ? 'good' : 'idle'}>{open ? 'Market open' : 'Market closed'}</Pill>
      <Pill tone="accent" title="Trading mode">
        {(tradingMode || '—').toUpperCase()}
      </Pill>
      <Pill tone={dhanReady ? 'good' : 'warn'} title="Dhan broker connection">
        Dhan {dhanReady ? 'OK' : '—'}
      </Pill>
    </>
  )
})
