import { memo, useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { cn } from '../lib/cn'
import { money } from '../lib/pnl'
import { fx } from '../lib/theme'
import { Button } from './ui/Button'

type Gate = { title?: string; detail?: string; ok?: boolean }

type Props = {
  tradingMode?: string
  lotsPerTrade?: number
  orderQuantities?: Record<string, number>
  liveSummary?: {
    open?: number
    realized_pnl?: number
    open_mtm?: number
  }
  paperSummary?: {
    open?: number
    realized_pnl?: number
    open_mtm?: number
  }
  gates?: Gate[]
  liveArmed?: boolean
}

export const ExecutionPanel = memo(function ExecutionPanel({
  tradingMode,
  lotsPerTrade = 1,
  orderQuantities,
  liveSummary,
  paperSummary,
  gates = [],
  liveArmed = false,
}: Props) {
  const qc = useQueryClient()

  // Optimistic: the switch itself is fast, but the button used to wait on the
  // next /api/status refetch before it reflected reality, which reads as a stuck
  // spinner. Show the new mode immediately and reconcile from the response.
  const [pendingMode, setPendingMode] = useState<string | null>(null)

  const effectiveMode = (pendingMode ?? tradingMode ?? 'PAPER').toUpperCase()
  const isPaper = effectiveMode !== 'LIVE'
  const journalSummary = isPaper ? paperSummary : liveSummary
  const journalLabel = isPaper ? 'Paper journal' : 'Live journal'

  const setMode = useMutation({
    mutationFn: (mode: string) =>
      api<{ trading_mode?: string }>('/api/trading/mode', {
        method: 'POST',
        body: JSON.stringify({ mode }),
      }),
    onSuccess: (res) => {
      setPendingMode(res?.trading_mode ?? null)
      toast.success(`Switched to ${res?.trading_mode ?? 'new mode'}`)
      void qc.invalidateQueries({ queryKey: ['status'] })
    },
    onError: (e: Error) => {
      setPendingMode(null)
      toast.error(e.message)
    },
  })

  // Optimistic: the server round trip is fast now, but a counter that waits on
  // the network still feels broken. Show the new value immediately and reconcile
  // when the request lands; on failure the pending delta is dropped.
  // Absolute target, never a delta: a relative change is a read-modify-write on
  // the server, so two fast clicks can lose one and leave the counter showing a
  // number the engine is not actually using.
  const adjustLots = useMutation({
    mutationFn: (lots: number) =>
      api<{ lots_per_trade?: number }>('/api/settings/lots', {
        method: 'POST',
        body: JSON.stringify({ lots }),
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['status'] }),
    onError: (e: Error) => toast.error(e.message),
  })

  const [confirmText, setConfirmText] = useState('')
  const armLive = useMutation({
    mutationFn: (disarm: boolean) =>
      api('/api/trading/arm-live', {
        method: 'POST',
        body: JSON.stringify(disarm ? { disarm: true } : { confirm: confirmText }),
      }),
    onSuccess: (r: unknown) => {
      const armed = (r as { live_orders_enabled?: boolean })?.live_orders_enabled
      setConfirmText('')
      toast[armed ? 'warning' : 'success'](armed ? 'LIVE ORDERS ARMED' : 'Live orders disarmed')
      void qc.invalidateQueries({ queryKey: ['status'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  useEffect(() => {
    if (pendingMode !== null && pendingMode === tradingMode) setPendingMode(null)
  }, [pendingMode, tradingMode])

  const shownLots = lotsPerTrade
  // No optimistic value here on purpose. This number decides position size, and
  // an optimistic counter can drift from the server (rapid clicks read a stale
  // render value). The round trip is ~70ms, so showing only the confirmed value
  // and disabling while in flight is both correct and fast enough.
  const bumpLots = (delta: number) => {
    const next = Math.min(10, Math.max(1, lotsPerTrade + delta))
    if (next === lotsPerTrade || adjustLots.isPending) return
    adjustLots.mutate(next)
  }

  return (
    <section className={cn(fx.panel, 'p-4')}>
      <h2 className="mb-3 text-sm font-medium text-cyan-100/80">Execution</h2>
      {!isPaper ? (
        <div className="mb-3 rounded-lg border border-rose-500/40 bg-rose-950/20 p-3">
          {liveArmed ? (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-semibold text-rose-200">
                ⚠ Live orders ARMED — entries will be sent to Dhan with real money.
              </p>
              <Button variant="danger" onClick={() => armLive.mutate(true)} pending={armLive.isPending}>
                Disarm
              </Button>
            </div>
          ) : (
            <div className="space-y-2">
              <p className="text-sm text-rose-200">
                Live mode selected, but orders are <strong>blocked</strong>. To send real orders,
                type <code className="rounded bg-black/40 px-1">ARM LIVE ORDERS</code> below.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <input
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  placeholder="ARM LIVE ORDERS"
                  aria-label="Live order confirmation phrase"
                  className="rounded-md border border-rose-800 bg-black/40 px-2 py-1 font-mono text-sm text-rose-100 placeholder:text-rose-300/30"
                />
                <Button
                  variant="danger"
                  disabled={confirmText.trim().toUpperCase() !== 'ARM LIVE ORDERS'}
                  pending={armLive.isPending}
                  onClick={() => armLive.mutate(false)}
                >
                  Arm live orders
                </Button>
              </div>
            </div>
          )}
        </div>
      ) : null}

      <div className="flex flex-wrap gap-6">
        <div>
          <p className="mb-2 text-xs text-cyan-200/45">Trading mode</p>
          <div className="inline-flex rounded-lg border border-cyan-500/20 bg-black/20 p-0.5">
            {(['PAPER', 'LIVE'] as const).map((mode) => (
              <Button
                key={mode}
                pending={setMode.isPending && setMode.variables === mode}
                disabled={setMode.isPending || effectiveMode === mode}
                onClick={() => setMode.mutate(mode)}
                className={cn(
                  'rounded-md px-4 py-1.5 text-sm transition',
                  effectiveMode === mode
                    ? 'bg-cyan-400 font-medium text-slate-950 shadow-[0_0_12px_rgba(34,211,238,0.4)]'
                    : 'text-slate-400 hover:text-cyan-100',
                )}
              >
                {mode === 'PAPER' ? 'Paper' : 'Live'}
              </Button>
            ))}
          </div>
        </div>
        <div>
          <p className="mb-2 text-xs text-cyan-200/45">Lots per trade</p>
          <div className="flex items-center gap-2">
            <Button
              aria-label="Decrease lots per trade"
              disabled={shownLots <= 1 || adjustLots.isPending}
              onClick={() => bumpLots(-1)}
            >
              −
            </Button>
            <strong
              aria-live="polite"
              className={cn(
                'min-w-[2rem] text-center text-lg tabular-nums transition-opacity duration-150',
                adjustLots.isPending && 'opacity-40',
              )}
            >
              {shownLots}
            </strong>
            <Button
              aria-label="Increase lots per trade"
              disabled={shownLots >= 10 || adjustLots.isPending}
              onClick={() => bumpLots(1)}
            >
              +
            </Button>
          </div>
          {orderQuantities ? (
            <p className="mt-1 text-xs tabular-nums text-cyan-200/40">
              {Object.entries(orderQuantities)
                .map(([k, v]) => `${k} ${v} qty`)
                .join(' · ')}
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-4 text-sm">
          <div>
            <span className="block text-xs text-cyan-200/45">{journalLabel} open</span>
            <strong>{journalSummary?.open ?? 0}</strong>
          </div>
          <div>
            <span className="block text-xs text-cyan-200/45">{journalLabel} realized</span>
            <strong>{money(journalSummary?.realized_pnl)}</strong>
          </div>
          <div>
            <span className="block text-xs text-cyan-200/45">{journalLabel} MTM</span>
            <strong>{money(journalSummary?.open_mtm)}</strong>
          </div>
        </div>
      </div>
      {gates.length > 0 ? (
        <ul className="mt-4 space-y-1 text-xs">
          {gates.map((g, i) => (
            <li
              key={i}
              className={cn(
                'rounded border px-2 py-1',
                g.ok
                  ? 'border-emerald-500/20 text-emerald-300/90'
                  : 'border-red-500/20 text-red-300/90',
              )}
            >
              {g.title}: {g.detail}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
})
