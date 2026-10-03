import { useQuery } from '@tanstack/react-query'
import { memo, useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { cn } from '../../lib/cn'

type Market = { message?: string; is_open?: boolean; phase?: string }
type TickFeed = {
  enabled?: boolean
  connected?: boolean
  stalled?: boolean
  seconds_since_last_tick?: number | null
}

type PillSpec = { tone: 'idle' | 'good' | 'warn' | 'accent'; label: string; title: string }

/** D-10: a small read of the existing GET /api/tick-feed — the one-glance summary
 *  on every page. The full view (price age, option-chain age, spread age, Dhan
 *  live feed) is DataHealthPanel at the top of the Index Options page (Phase 4);
 *  this pill points to it and does not duplicate it. */
function tickFeedPill(feed: TickFeed | undefined, marketOpen?: boolean): PillSpec {
  if (!feed) return { tone: 'idle', label: 'Ticks —', title: 'Tick feed status unavailable — details: Data health, top of the Index Options page' }
  if (!feed.enabled) {
    return {
      tone: 'idle',
      label: 'Ticks off',
      title: 'Live tick feed is switched off — stops are checked every 20 seconds from Dhan prices — details: Data health, top of the Index Options page',
    }
  }
  const down = !feed.connected || !!feed.stalled
  if (!down) {
    return { tone: 'good', label: 'Ticks live', title: 'Stops react to every live tick — details: Data health, top of the Index Options page' }
  }
  if (marketOpen) {
    return {
      tone: 'warn',
      label: 'Ticks: fallback',
      title: 'Tick feed is down — stops are checked every 20 seconds from Dhan prices until it reconnects — details: Data health, top of the Index Options page',
    }
  }
  return { tone: 'idle', label: 'Ticks idle', title: 'Market closed — details: Data health, top of the Index Options page' }
}

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

  const { data: tickFeed } = useQuery({
    queryKey: ['tick-feed'],
    queryFn: () => api<TickFeed>('/api/tick-feed'),
    refetchInterval: 20_000,
    staleTime: 10_000,
  })

  const open = market?.is_open
  const tf = tickFeedPill(tickFeed, open)
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
      <Pill tone={tf.tone} title={tf.title}>
        {tf.label}
      </Pill>
    </>
  )
})
