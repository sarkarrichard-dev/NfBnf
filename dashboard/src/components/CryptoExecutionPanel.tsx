import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { cn } from '../lib/cn'
import { fx } from '../lib/theme'
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
}

/**
 * Crypto paper -> live switch. Its own two locks and its own phrase
 * ("ARM CRYPTO LIVE"), completely separate from the index arm. Delta rejects
 * orders from a non-whitelisted IP, so the egress IP is shown here.
 */
export function CryptoExecutionPanel() {
  const qc = useQueryClient()
  const status = useQuery({
    queryKey: ['crypto', 'status'],
    queryFn: () => api<Status>('/api/crypto/status'),
    refetchInterval: 30_000,
  })
  const s = status.data
  const isLive = (s?.trading_mode ?? 'PAPER').toUpperCase() === 'LIVE'
  const armed = !!s?.live_armed
  const [phrase, setPhrase] = useState('')
  const [arming, setArming] = useState(false)

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

  const ks = s?.kill_switch
  const eg = s?.egress
  const busy = setMode.isPending || armLive.isPending

  return (
    <div
      className={cn(
        fx.panel,
        'space-y-3 p-4',
        s?.live_orders_enabled
          ? 'border-[var(--armed)]/70 bg-[var(--armed)]/10'
          : isLive
            ? 'border-[var(--warn)]/60 bg-[var(--warn)]/10'
            : '',
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold text-cyan-50/90">Mode</span>
        <div className="inline-flex overflow-hidden rounded-lg border border-[var(--hair)] text-xs">
          <button
            type="button"
            disabled={busy || !isLive}
            onClick={() => setMode.mutate('PAPER')}
            className={cn('px-3 py-1', !isLive ? 'bg-white/10 text-slate-100' : 'text-slate-400')}
          >
            Paper
          </button>
          <button
            type="button"
            disabled={busy || isLive || !s?.credentials_ready}
            onClick={() => setMode.mutate('LIVE')}
            className={cn(
              'px-3 py-1',
              isLive ? 'bg-[var(--armed)] text-white' : 'text-slate-400',
            )}
          >
            Live
          </button>
        </div>
        {!s?.credentials_ready ? (
          <span className="text-[11px] text-slate-500">add Delta keys first</span>
        ) : null}
      </div>

      {isLive ? (
        <>
          <p className="text-xs text-slate-300">
            {s?.live_orders_enabled
              ? '⚠ ARMED — strategy entries place real orders on Delta with real money.'
              : 'Live mode on, disarmed. No real orders until you arm.'}
          </p>

          {armed ? (
            <Button variant="danger" pending={armLive.isPending} onClick={() => armLive.mutate({ disarm: true })}>
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
              <Button variant="ghost" onClick={() => { setArming(false); setPhrase('') }}>
                Cancel
              </Button>
            </div>
          ) : (
            <Button variant="primary" onClick={() => setArming(true)}>
              Arm live orders
            </Button>
          )}

          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
            <dt className="text-slate-500">Whitelist on your Delta key</dt>
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
                ? `${eg.delta_sees_ip} — not whitelisted; add this`
                : eg?.forcing_ipv4
                  ? 'IPv4 pinned — whitelist the IPv4 above'
                  : 'ok'}
            </dd>
            <dt className="text-slate-500">Kill switch</dt>
            <dd className={cn('font-mono', ks?.tripped ? 'text-[var(--down)]' : 'text-slate-300')}>
              {ks?.tripped
                ? `TRIPPED — ${ks.reason}`
                : `${ks?.today_live_trades ?? 0} live trades · net $${(ks?.today_live_net_usd ?? 0).toFixed(2)} · limit $${ks?.max_daily_loss_usd ?? 50} / ${ks?.max_consec_losses ?? 3} losses`}
            </dd>
          </dl>
        </>
      ) : null}
    </div>
  )
}
