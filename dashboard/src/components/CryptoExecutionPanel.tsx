import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { cn } from '../lib/cn'
import { fx } from '../lib/theme'
import { usd0 } from '../lib/cryptoFmt'
import { Button } from './ui/Button'

type KillSwitch = {
  tripped: boolean
  reason: string
  today_live_net_usd: number
  today_live_trades: number
  max_daily_loss_usd: number
  max_consec_losses: number
}
type Egress = {
  ipv4: string | null
  ipv6: string | null
  forcing_ipv4: boolean
  delta_sees_ip: string | null
  whitelist_ok: boolean
}
type Status = {
  credentials_ready: boolean
  trading_mode: string
  live_armed: boolean
  live_orders_enabled: boolean
  arm_phrase: string
  egress: Egress
  kill_switch: KillSwitch
  sizing: { margin_per_position_usd: number; leverage: number }
}
type LotRow = { symbol: string; margin_per_lot_usd: number | null; lots: number | null; deployed_usd: number | null }
type Lots = { margin_per_position_usd: number; leverage: number; table: LotRow[] }

/**
 * Crypto Execution — mirrors the index ExecutionPanel: a sliding PAPER/LIVE
 * pill, a $ per position input, and the arm strip. Its own two locks and its
 * own phrase ("ARM CRYPTO LIVE"), separate from the index arm.
 *
 * Sizing is dollar-first (Richard, 2026-09-14): the operator sets one $ margin
 * budget per position, and each symbol auto-sizes to however many contracts
 * that budget covers at its own price and the configured leverage — a fixed
 * lot count meant wildly different money per symbol (a BTC lot vs. a SOL lot).
 */
export function CryptoExecutionPanel() {
  const qc = useQueryClient()
  const status = useQuery({
    queryKey: ['crypto', 'status'],
    queryFn: () => api<Status>('/api/crypto/status'),
    refetchInterval: 30_000,
  })
  const lotsQ = useQuery({
    queryKey: ['crypto', 'lots'],
    queryFn: () => api<Lots>('/api/crypto/lots'),
    refetchInterval: 60_000,
  })
  const s = status.data
  const isLive = (s?.trading_mode ?? 'PAPER').toUpperCase() === 'LIVE'
  const armed = !!s?.live_armed
  const leverage = s?.sizing.leverage ?? lotsQ.data?.leverage ?? 20
  const margin = s?.sizing.margin_per_position_usd ?? 50
  const [phrase, setPhrase] = useState('')
  const [arming, setArming] = useState(false)
  const [marginDraft, setMarginDraft] = useState('')  // '' = show the live `margin`

  const setMode = useMutation({
    mutationFn: (mode: string) =>
      api('/api/crypto/mode', { method: 'POST', body: JSON.stringify({ mode }) }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['crypto'] })
      setArming(false)
      setPhrase('')
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const armLive = useMutation({
    mutationFn: (body: { confirm?: string; disarm?: boolean }) =>
      api<{ live_orders_enabled: boolean }>('/api/crypto/arm-live', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: (r) => {
      toast[r.live_orders_enabled ? 'warning' : 'success'](
        r.live_orders_enabled ? 'CRYPTO LIVE ORDERS ARMED' : 'Crypto live orders disarmed',
      )
      setArming(false)
      setPhrase('')
      void qc.invalidateQueries({ queryKey: ['crypto'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const adjustMargin = useMutation({
    mutationFn: (usd: number) =>
      api('/api/crypto/config', {
        method: 'POST',
        body: JSON.stringify({ margin_per_position_usd: usd }),
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['crypto'] }),
    onError: (e: Error) => toast.error(e.message),
  })

  const ks = s?.kill_switch
  const eg = s?.egress
  const busy = setMode.isPending || armLive.isPending
  const commitMargin = () => {
    const n = Math.round(Number(marginDraft))
    setMarginDraft('')
    if (Number.isFinite(n) && n >= 1 && n !== margin) adjustMargin.mutate(n)
  }

  // actual $ this budget deploys per symbol, summed — what "$X per position"
  // really commits across the whole active symbol list
  const totalDeployed = (lotsQ.data?.table ?? []).reduce((a, r) => a + (r.deployed_usd ?? 0), 0)

  return (
    <section
      className={cn(
        fx.panel,
        'p-4',
        s?.live_orders_enabled
          ? 'border-[var(--armed)]/70 bg-[var(--armed)]/[0.06]'
          : isLive
            ? 'border-[var(--warn)]/60 bg-[var(--warn)]/[0.06]'
            : '',
      )}
    >
      <h2 className="mb-3 text-sm font-semibold tracking-wide text-slate-200">Execution</h2>

      <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
        {/* Paper / Live pill */}
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
            Trading mode
          </p>
          <button
            type="button"
            role="switch"
            aria-checked={isLive}
            aria-label="Toggle between Paper and Live crypto trading"
            disabled={busy || (!isLive && !s?.credentials_ready)}
            onClick={() => {
              if (busy) return
              if (isLive) setMode.mutate('PAPER')
              else setMode.mutate('LIVE')
            }}
            className={cn(
              'relative flex h-9 w-[164px] items-center rounded-full border p-1 text-xs font-semibold transition-colors',
              'disabled:cursor-not-allowed disabled:opacity-70',
              isLive
                ? armed
                  ? 'border-[var(--armed)] bg-[var(--armed)]/20'
                  : 'border-[var(--warn)]/70 bg-[var(--warn)]/15'
                : 'border-[var(--acc)]/50 bg-[var(--acc)]/10',
            )}
          >
            <span
              className={cn(
                'absolute z-10 flex h-7 w-[76px] items-center justify-center rounded-full shadow transition-transform duration-200 ease-out motion-reduce:transition-none',
                isLive
                  ? armed
                    ? 'translate-x-[80px] bg-[var(--armed)] text-white'
                    : 'translate-x-[80px] bg-[var(--warn)] text-black'
                  : 'translate-x-0 bg-[var(--acc)] text-[var(--acc-ink)]',
              )}
            >
              {isLive ? 'LIVE' : 'PAPER'}
            </span>
            <span className="z-0 flex-1 text-center text-slate-400">Paper</span>
            <span className="z-0 flex-1 text-center text-slate-400">Live</span>
          </button>
          {isLive ? (
            <p
              className={cn(
                'mt-1.5 text-xs',
                armed ? 'font-semibold text-[var(--armed)]' : 'text-[var(--warn)]',
              )}
            >
              {armed ? '● Real orders will be sent to Delta' : '○ Live mode — orders disarmed'}
            </p>
          ) : !s?.credentials_ready ? (
            <p className="mt-1.5 text-xs text-slate-500">Add Delta keys to enable Live</p>
          ) : null}
        </div>

        {/* $ per position — auto-sizes to however many contracts that covers per symbol */}
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
            $ per position
          </p>
          <div
            className={cn(
              'flex items-center gap-1 rounded-lg p-0.5 transition-shadow',
              adjustMargin.isPending && 'shadow-[0_0_0_1px_var(--acc)]',
            )}
          >
            <span className="text-lg font-semibold text-slate-500">$</span>
            <input
              type="number"
              min={1}
              step={1}
              inputMode="decimal"
              aria-label="Dollar margin per position"
              value={marginDraft === '' ? margin : marginDraft}
              onChange={(e) => setMarginDraft(e.target.value)}
              onFocus={() => setMarginDraft(String(margin))}
              onBlur={commitMargin}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur()
              }}
              className={cn(
                'w-20 rounded-md border border-[var(--hair)] bg-black/30 px-1 py-0.5 text-center text-lg font-semibold tabular-nums text-slate-50 outline-none focus:border-[var(--acc)]',
                adjustMargin.isPending && 'animate-pulse text-[var(--acc)]',
              )}
            />
          </div>
          <p className="mt-1 text-xs tabular-nums text-slate-500">
            {totalDeployed > 0
              ? `≈ ${usd0(totalDeployed)} deployed @ ${leverage}x across ${(lotsQ.data?.table ?? []).filter((r) => r.lots).length} symbols`
              : `per symbol, auto-sized @ ${leverage}x`}
          </p>
        </div>
      </div>

      {/* Live arm / disarm strip */}
      {isLive ? (
        <div
          className={cn(
            'mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3',
            armed
              ? 'border-[var(--armed)]/60 bg-[var(--armed)]/10'
              : 'border-[var(--warn)]/50 bg-[var(--warn)]/10',
          )}
        >
          <p className="text-sm text-slate-100">
            {armed
              ? '⚠ Live orders ARMED — strategy entries go to Delta with real money.'
              : 'Live mode is on but disarmed. No real orders until you arm.'}
          </p>
          {armed ? (
            <Button
              variant="danger"
              pending={armLive.isPending}
              onClick={() => armLive.mutate({ disarm: true })}
            >
              Disarm
            </Button>
          ) : arming ? (
            <div className="flex flex-wrap items-center gap-2">
              <input
                autoFocus
                className="w-52 rounded-lg border border-[var(--hair)] bg-black/30 px-2 py-1 font-mono text-xs text-slate-100 outline-none focus:border-[var(--armed)]"
                placeholder={s?.arm_phrase}
                value={phrase}
                onChange={(e) => setPhrase(e.target.value)}
              />
              <Button
                variant="danger"
                pending={armLive.isPending}
                disabled={phrase.trim().toUpperCase() !== (s?.arm_phrase ?? '').toUpperCase()}
                onClick={() => armLive.mutate({ confirm: phrase })}
              >
                Arm
              </Button>
              <Button
                variant="ghost"
                onClick={() => {
                  setArming(false)
                  setPhrase('')
                }}
              >
                Cancel
              </Button>
            </div>
          ) : (
            <Button variant="primary" onClick={() => setArming(true)}>
              Arm live orders
            </Button>
          )}
        </div>
      ) : null}

      {isLive ? (
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
          <dt className="text-slate-500">Allowed on your Delta key</dt>
          <dd className="font-mono text-slate-300">
            IPv4 {eg?.ipv4 ?? '—'}
            {eg?.ipv6 ? <span className="text-slate-600"> · IPv6 {eg.ipv6}</span> : null}
          </dd>
          <dt className="text-slate-500">Delta sees</dt>
          <dd
            className={cn(
              'font-mono',
              eg?.delta_sees_ip ? 'text-[var(--down)]' : 'text-[var(--up)]',
            )}
          >
            {eg?.delta_sees_ip
              ? `${eg.delta_sees_ip} — not on the allow-list; add this`
              : eg?.forcing_ipv4
                ? 'IPv4 pinned — add the IPv4 above to the allow-list'
                : 'ok'}
          </dd>
          <dt className="text-slate-500">Kill switch</dt>
          <dd className={cn('font-mono', ks?.tripped ? 'text-[var(--down)]' : 'text-slate-300')}>
            {ks?.tripped
              ? `TRIPPED — ${ks.reason}`
              : `${ks?.today_live_trades ?? 0} live trades · net $${(ks?.today_live_net_usd ?? 0).toFixed(2)} · limit $${ks?.max_daily_loss_usd ?? 50} / ${ks?.max_consec_losses ?? 3} losses`}
          </dd>
        </dl>
      ) : null}
    </section>
  )
}
