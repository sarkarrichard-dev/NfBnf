import { memo, useState } from 'react'
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
  liveSummary?: { open?: number; realized_pnl?: number; open_mtm?: number }
  paperSummary?: { open?: number; realized_pnl?: number; open_mtm?: number }
  gates?: Gate[]
  liveArmed?: boolean
}

const ARM_PHRASE = 'ARM LIVE ORDERS'

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

  // Patch the cached /api/status straight from each mutation's response instead
  // of invalidating it. Invalidation forced a full ~250ms status refetch on the
  // event loop before the toggle moved — that was the "delay switching modes".
  const patchStatus = (partial: Record<string, unknown>) =>
    qc.setQueryData(['status'], (old: Record<string, unknown> | undefined) =>
      old ? { ...old, ...partial } : old,
    )

  const isLive = (tradingMode ?? 'PAPER').toUpperCase() === 'LIVE'

  const summary = isLive ? liveSummary : paperSummary
  const summaryLabel = isLive ? 'Live journal' : 'Paper journal'

  const setMode = useMutation({
    mutationFn: (mode: string) =>
      api<{
        trading_mode?: string
        live_orders_enabled?: boolean
        trading_gates?: unknown
        kill_switch?: unknown
      }>('/api/trading/mode', { method: 'POST', body: JSON.stringify({ mode }) }),
    onSuccess: (res) =>
      patchStatus({
        trading_mode: res.trading_mode,
        live_allowed: res.live_orders_enabled ?? false,
        trading_gates: res.trading_gates,
        kill_switch: res.kill_switch,
      }),
    onError: (e: Error) => toast.error(e.message),
  })

  const armLive = useMutation({
    mutationFn: (disarm: boolean) =>
      api<{ live_orders_enabled?: boolean; trading_mode?: string; trading_gates?: unknown }>(
        '/api/trading/arm-live',
        {
          method: 'POST',
          body: JSON.stringify(disarm ? { disarm: true } : { confirm: ARM_PHRASE }),
        },
      ),
    onSuccess: (res) => {
      patchStatus({
        live_allowed: res.live_orders_enabled ?? false,
        trading_mode: res.trading_mode,
        trading_gates: res.trading_gates,
      })
      toast[res.live_orders_enabled ? 'warning' : 'success'](
        res.live_orders_enabled ? 'LIVE ORDERS ARMED' : 'Live orders disarmed',
      )
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const adjustLots = useMutation({
    mutationFn: (lots: number) =>
      api<{ policy?: unknown }>('/api/settings/lots', {
        method: 'POST',
        body: JSON.stringify({ lots }),
      }),
    onSuccess: (res) => patchStatus({ policy: res.policy }),
    onError: (e: Error) => toast.error(e.message),
  })

  // 'go-live' = switch PAPER→LIVE and arm in one confirm. 'arm' = already in
  // Live, just arm. null = closed.
  const [modal, setModal] = useState<'go-live' | 'arm' | null>(null)
  const busy = setMode.isPending || armLive.isPending

  const confirmModal = async () => {
    if (modal === 'go-live') await setMode.mutateAsync('LIVE')
    await armLive.mutateAsync(false)
    setModal(null)
  }

  const onToggle = () => {
    if (busy) return
    if (isLive) setMode.mutate('PAPER') // safe direction — no confirm, server disarms
    else setModal('go-live')
  }

  const bumpLots = (delta: number) => {
    const next = Math.min(10, Math.max(1, lotsPerTrade + delta))
    if (next === lotsPerTrade || adjustLots.isPending) return
    adjustLots.mutate(next)
  }

  return (
    <section className={cn(fx.panel, 'p-4')}>
      <h2 className="mb-3 text-sm font-semibold tracking-wide text-slate-200">Execution</h2>

      <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
        {/* Paper / Live toggle */}
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
            Trading mode
          </p>
          <button
            type="button"
            role="switch"
            aria-checked={isLive}
            aria-label="Toggle between Paper and Live trading"
            disabled={busy}
            onClick={onToggle}
            className={cn(
              'relative flex h-9 w-[164px] items-center rounded-full border p-1 text-xs font-semibold transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--acc)]/70 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0a0d14]',
              'disabled:cursor-not-allowed disabled:opacity-70',
              isLive
                ? liveArmed
                  ? 'border-[var(--armed)] bg-[var(--armed)]/20'
                  : 'border-[var(--warn)]/70 bg-[var(--warn)]/15'
                : 'border-[var(--acc)]/50 bg-[var(--acc)]/10',
            )}
          >
            <span
              className={cn(
                'absolute z-10 flex h-7 w-[76px] items-center justify-center rounded-full shadow transition-transform duration-200 ease-out motion-reduce:transition-none',
                isLive
                  ? liveArmed
                    ? 'translate-x-[80px] bg-[var(--armed)] text-white'
                    : 'translate-x-[80px] bg-[var(--warn)] text-black'
                  : 'translate-x-0 bg-[var(--acc)] text-[var(--acc-ink)]',
              )}
            >
              {isLive ? 'LIVE' : 'PAPER'}
            </span>
            <span className="z-0 flex-1 text-center text-slate-500">Paper</span>
            <span className="z-0 flex-1 text-center text-slate-500">Live</span>
          </button>
          {isLive ? (
            <p
              className={cn(
                'mt-1.5 text-xs',
                liveArmed ? 'font-semibold text-[var(--armed)]' : 'text-[var(--warn)]',
              )}
            >
              {liveArmed ? '● Real orders will be sent to Dhan' : '○ Live mode — orders disarmed'}
            </p>
          ) : null}
        </div>

        {/* Lots stepper */}
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
            Lots per trade
          </p>
          <div
            className={cn(
              'flex items-center gap-2 rounded-lg p-0.5 transition-shadow',
              adjustLots.isPending && 'shadow-[0_0_0_1px_var(--acc)]',
            )}
          >
            <Button
              aria-label="Decrease lots per trade"
              disabled={lotsPerTrade <= 1 || adjustLots.isPending}
              onClick={() => bumpLots(-1)}
            >
              −
            </Button>
            <strong
              aria-live="polite"
              className={cn(
                'min-w-[2rem] text-center text-lg tabular-nums text-slate-50',
                adjustLots.isPending && 'animate-pulse text-[var(--acc)]',
              )}
            >
              {lotsPerTrade}
            </strong>
            <Button
              aria-label="Increase lots per trade"
              disabled={lotsPerTrade >= 10 || adjustLots.isPending}
              onClick={() => bumpLots(1)}
            >
              +
            </Button>
          </div>
          {orderQuantities ? (
            <p className="mt-1 text-xs tabular-nums text-slate-500">
              {Object.entries(orderQuantities)
                .map(([k, v]) => `${k} ${v} qty`)
                .join(' · ')}
            </p>
          ) : null}
        </div>

        {/* Journal summary */}
        <div className="flex flex-wrap gap-4 text-sm">
          <div>
            <span className="block text-xs text-slate-400">{summaryLabel} open</span>
            <strong className="text-slate-50">{summary?.open ?? 0}</strong>
          </div>
          <div>
            <span className="block text-xs text-slate-400">{summaryLabel} realized</span>
            <strong className="text-slate-50">{money(summary?.realized_pnl)}</strong>
          </div>
          <div>
            <span className="block text-xs text-slate-400">{summaryLabel} MTM</span>
            <strong className="text-slate-50">{money(summary?.open_mtm)}</strong>
          </div>
        </div>
      </div>

      {/* Live arm / disarm strip */}
      {isLive ? (
        <div
          className={cn(
            'mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3',
            liveArmed
              ? 'border-[var(--armed)]/60 bg-[var(--armed)]/10'
              : 'border-[var(--warn)]/50 bg-[var(--warn)]/10',
          )}
        >
          <p className="text-sm text-slate-100">
            {liveArmed
              ? '⚠ Live orders are ARMED — scanner entries go to Dhan with real money.'
              : 'Live mode is on but disarmed. No real orders will be sent until you arm them.'}
          </p>
          {liveArmed ? (
            <Button variant="danger" onClick={() => armLive.mutate(true)} pending={armLive.isPending}>
              Disarm
            </Button>
          ) : (
            <Button variant="primary" onClick={() => setModal('arm')} pending={armLive.isPending}>
              Arm live orders
            </Button>
          )}
        </div>
      ) : null}

      {gates.length > 0 ? (
        <ul className="mt-4 space-y-1 text-xs">
          {gates.map((g, i) => (
            <li
              key={i}
              className={cn(
                'rounded border px-2 py-1',
                g.ok
                  ? 'border-emerald-500/25 text-emerald-300/90'
                  : 'border-red-500/25 text-red-300/90',
              )}
            >
              {g.title}: {g.detail}
            </li>
          ))}
        </ul>
      ) : null}

      {modal ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-labelledby="exec-modal-title"
          onClick={() => !busy && setModal(null)}
        >
          <div
            className={cn(fx.panel, 'w-full max-w-sm p-5')}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="exec-modal-title" className="text-base font-semibold text-[var(--armed)]">
              {modal === 'go-live' ? 'Switch to Live trading?' : 'Arm live orders?'}
            </h3>
            <p className="mt-2 text-sm text-slate-300">
              This arms real broker orders. Scanner entries will be sent to Dhan with real
              money until you disarm or switch back to Paper.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="ghost" disabled={busy} onClick={() => setModal(null)}>
                Cancel
              </Button>
              <Button variant="danger" pending={busy} onClick={() => void confirmModal()}>
                Confirm
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  )
})
