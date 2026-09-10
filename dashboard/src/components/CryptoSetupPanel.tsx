import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { cn } from '../lib/cn'
import { fx } from '../lib/theme'
import { Button } from './ui/Button'

type Quote = { mark: number | null; bid: number | null; ask: number | null }
type Health = {
  connected: boolean
  credentials_ready: boolean
  base_url: string
  wallet_usd: number | null
  wallet_inr: number | null
  quotes: Record<string, Quote>
  errors: string[]
}
type Status = {
  credentials_ready: boolean
  api_key_preview: string
  base_url: string
  paper_enabled: boolean
  lanes: {
    ny_n_break: boolean
    ichimoku: boolean
  }
}

const inputCls =
  'w-full rounded-lg border border-[var(--hair)] bg-black/30 px-3 py-2 font-mono text-xs ' +
  'text-slate-100 outline-none focus:border-[var(--acc)]'

function px(v: number | null | undefined) {
  return v == null ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

/**
 * Delta Exchange (crypto) credentials + connectivity.
 *
 * The keys can place real orders once live execution ships, so — like the Dhan
 * login — this writes .env through a confirmed money-path endpoint, never the
 * generic feature-toggle panel.
 */
export function CryptoSetupPanel() {
  const qc = useQueryClient()
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [confirm, setConfirm] = useState(false)

  const status = useQuery({
    queryKey: ['crypto', 'status'],
    queryFn: () => api<Status>('/api/crypto/status'),
  })
  const health = useQuery({
    queryKey: ['crypto', 'health'],
    queryFn: () => api<Health>('/api/crypto/health'),
    refetchInterval: 60_000,
  })

  const save = useMutation({
    mutationFn: () =>
      api<{ connected: boolean; errors: string[] }>('/api/crypto/credentials', {
        method: 'POST',
        body: JSON.stringify({ api_key: apiKey, api_secret: apiSecret, confirm: true }),
      }),
    onSuccess: (r) => {
      if (r.connected) toast.success('Delta connected')
      else toast.warning(`Saved, but not connected: ${r.errors[0] ?? 'unknown error'}`)
      setApiKey('')
      setApiSecret('')
      setConfirm(false)
      void qc.invalidateQueries({ queryKey: ['crypto'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const h = health.data
  const s = status.data

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">
        Crypto trades on Delta Exchange India, separate from the index lanes. Perpetual futures
        only for now. Keys are written to .env by the app.
      </p>

      {/* connection + wallet */}
      <div className={fx.card}>
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-semibold text-cyan-50/90">Connection</span>
          <span
            className={cn(
              'rounded-full px-2 py-0.5 text-[10px] font-semibold',
              h?.connected
                ? 'bg-[var(--up)]/15 text-[var(--up)]'
                : s?.credentials_ready
                  ? 'bg-[var(--warn)]/15 text-[var(--warn)]'
                  : 'bg-white/10 text-slate-400',
            )}
          >
            {h?.connected ? 'connected' : s?.credentials_ready ? 'key set · not connected' : 'no key'}
          </span>
        </div>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          <dt className="text-slate-500">Host</dt>
          <dd className="font-mono text-slate-300">{(s?.base_url ?? '').replace('https://', '')}</dd>
          <dt className="text-slate-500">Key</dt>
          <dd className="font-mono text-slate-300">{s?.api_key_preview || '—'}</dd>
          <dt className="text-slate-500">Wallet (USD)</dt>
          <dd className="font-mono text-slate-300">{px(h?.wallet_usd)}</dd>
          <dt className="text-slate-500">Wallet (INR)</dt>
          <dd className="font-mono text-slate-300">{px(h?.wallet_inr)}</dd>
        </dl>
      </div>

      {/* live quotes */}
      <div className={fx.card}>
        <span className="mb-2 block text-xs font-semibold text-cyan-50/90">Live quotes</span>
        <div className="space-y-1">
          {Object.entries(h?.quotes ?? {}).map(([sym, q]) => (
            <div key={sym} className="flex items-center justify-between font-mono text-xs">
              <span className="text-slate-400">{sym}</span>
              <span className="text-slate-200">
                {px(q.mark)} <span className="text-slate-600">· {px(q.bid)}/{px(q.ask)}</span>
              </span>
            </div>
          ))}
          {!h?.quotes || Object.keys(h.quotes).length === 0 ? (
            <p className="text-xs text-slate-600">No quotes — check connectivity.</p>
          ) : null}
        </div>
      </div>

      {/* errors */}
      {h?.errors?.length ? (
        <ul className="space-y-1 rounded-lg border border-[var(--warn)]/30 bg-[var(--warn)]/10 p-3 text-xs text-[var(--warn)]">
          {h.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      ) : null}

      {/* credential form */}
      <div className={cn(fx.card, 'space-y-2')}>
        <span className="block text-xs font-semibold text-cyan-50/90">Set / replace keys</span>
        <input
          className={inputCls}
          placeholder="DELTA_API_KEY"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          autoComplete="off"
        />
        <input
          className={inputCls}
          type="password"
          placeholder="DELTA_API_SECRET"
          value={apiSecret}
          onChange={(e) => setApiSecret(e.target.value)}
          autoComplete="off"
        />
        <label className="flex items-center gap-2 text-xs text-slate-400">
          <input
            type="checkbox"
            checked={confirm}
            onChange={(e) => setConfirm(e.target.checked)}
          />
          I understand these keys can place real orders once live trading is enabled.
        </label>
        <div className="flex gap-2">
          <Button
            variant="primary"
            pending={save.isPending}
            disabled={!apiKey || !apiSecret || !confirm}
            onClick={() => save.mutate()}
          >
            Save keys
          </Button>
          <Button
            variant="secondary"
            pending={health.isFetching}
            onClick={() => void qc.invalidateQueries({ queryKey: ['crypto'] })}
          >
            Test connection
          </Button>
        </div>
      </div>
    </div>
  )
}
