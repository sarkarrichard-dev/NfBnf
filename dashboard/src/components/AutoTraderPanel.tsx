import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { usePollMs } from '../hooks/usePageVisible'
import { cn } from '../lib/cn'

type HeatCell = {
  instrument?: string
  action?: string
  heat?: number
  confidence?: number
  cpr_regime?: string
  cpr_width_class?: string
  cpr_position?: string
  ema_bias?: string
  structure?: string
  pcr?: number
  oi_bias?: string
  plan_allowed?: boolean
  plan_reason?: string
  error?: string
}

type Heatmap = {
  cells?: HeatCell[]
  summary?: Record<string, number>
  error?: string
}

type AutoStatus = {
  running?: boolean
  cycles?: number
  executions?: number
  open_trades?: number
  auth_blocked?: boolean
  last_error?: string
  kill_switch?: { active?: boolean }
  indices_skipped?: string[]
  position_mode?: string
  open_trades_paper?: number
  open_trades_live?: number
  events?: Array<Record<string, unknown>>
  market?: { is_open?: boolean; phase?: string; message?: string }
  index_scan_concurrency?: number
  health?: {
    last_cycle_ms?: number
    tripped?: string[]
    stages?: Array<{
      name: string
      ok: number
      failed: number
      last_ms: number
      avg_ms: number
      tripped: boolean
      last_error?: string | null
      cooldown_remaining_s?: number
    }>
  }
  last_reconcile?: {
    ok?: boolean
    issues?: Array<{ kind: string; security_id: number; expected_qty: number; broker_qty: number }>
    skipped?: string
  } | null
}

function actionClass(action?: string): string {
  if (action === 'BUY_CALL') return 'border-emerald-500/30 bg-emerald-500/5'
  if (action === 'BUY_PUT') return 'border-red-500/30 bg-red-500/5'
  if (action === 'ERROR' || action === 'SKIP') return 'border-amber-500/30'
  return 'border-slate-700'
}

export function AutoTraderPanel() {
  const qc = useQueryClient()
  const autoPoll = usePollMs(3_000)

  const auto = useQuery({
    queryKey: ['auto-status'],
    queryFn: () => api<AutoStatus>('/api/auto/status'),
    refetchInterval: autoPoll,
  })

  const running = !!auto.data?.running
  const heatmapPoll = usePollMs(running ? 4_000 : 30_000)

  const heatmap = useQuery({
    queryKey: ['heatmap'],
    queryFn: () => api<Heatmap>('/api/heatmap'),
    refetchInterval: heatmapPoll,
  })

  const start = useMutation({
    mutationFn: () => api('/api/auto/start', { method: 'POST', body: '{}' }),
    onSuccess: () => {
      toast.success('Scanner started')
      void qc.invalidateQueries({ queryKey: ['auto-status'] })
      void qc.invalidateQueries({ queryKey: ['heatmap'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const stop = useMutation({
    mutationFn: () => api('/api/auto/stop', { method: 'POST' }),
    onSuccess: () => {
      toast.info('Scanner stopped')
      void qc.invalidateQueries({ queryKey: ['auto-status'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const status = auto.data
  const mode = (status?.position_mode || 'PAPER').toUpperCase()
  const openJournal =
    mode === 'LIVE' ? status?.open_trades_live : status?.open_trades_paper
  const parts = [
    running ? 'Scanner running' : 'Scanner stopped',
    `${mode} mode`,
    status?.market?.is_open ? 'Market open' : status?.market?.message || 'Market closed',
    status?.cycles != null ? `${status.cycles} cycles` : null,
    status?.executions != null ? `${status.executions} algo entries` : null,
    openJournal != null ? `${openJournal} open in journal` : null,
    status?.auth_blocked ? 'Dhan token expired' : null,
    status?.last_error ? `⚠ ${status.last_error}` : null,
    status?.health?.last_cycle_ms ? `cycle ${Math.round(status.health.last_cycle_ms)}ms` : null,
  ].filter(Boolean)

  const stages = status?.health?.stages ?? []
  const tripped = status?.health?.tripped ?? []
  const drift = status?.last_reconcile?.issues ?? []

  const cells = heatmap.data?.cells || []
  const summary = heatmap.data?.summary || {}

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={running || start.isPending}
          onClick={() => start.mutate()}
          className="rounded-md bg-cyan-500 px-3 py-1.5 text-sm font-medium text-slate-950 disabled:opacity-50"
        >
          Start
        </button>
        <button
          type="button"
          disabled={!running || stop.isPending}
          onClick={() => stop.mutate()}
          className="rounded-md border border-slate-700 px-3 py-1.5 text-sm text-slate-300 disabled:opacity-50"
        >
          Stop
        </button>
        <button
          type="button"
          onClick={() => void heatmap.refetch()}
          className="rounded-md border border-slate-700 px-3 py-1.5 text-sm text-slate-300"
        >
          Refresh heatmap
        </button>
      </div>

      <p
        className={cn(
          'text-sm',
          status?.last_error || status?.auth_blocked ? 'text-red-300' : 'text-slate-400',
        )}
      >
        {parts.join(' · ')}
      </p>

      {tripped.length ? (
        <p className="rounded-lg border border-amber-500/30 bg-amber-950/20 px-3 py-2 text-sm text-amber-200">
          Circuit breaker open on {tripped.join(', ')} — skipped until cooldown clears.
        </p>
      ) : null}

      {drift.length ? (
        <p className="rounded-lg border border-amber-500/30 bg-amber-950/20 px-3 py-2 text-sm text-amber-200">
          Broker/journal drift: {drift.map((d) => `${d.kind} on ${d.security_id} (${d.expected_qty}→${d.broker_qty})`).join('; ')}
        </p>
      ) : null}

      {stages.length ? (
        <div className="flex flex-wrap gap-1.5">
          {stages.map((st) => (
            <span
              key={st.name}
              title={st.last_error ?? `${st.ok} ok / ${st.failed} failed · avg ${Math.round(st.avg_ms)}ms`}
              className={cn(
                'rounded border px-1.5 py-0.5 font-mono text-[10px]',
                st.tripped
                  ? 'border-amber-700 text-amber-300'
                  : st.failed > 0
                    ? 'border-rose-800 text-rose-300'
                    : 'border-slate-700 text-slate-400',
              )}
            >
              {st.name} {Math.round(st.last_ms)}ms
            </span>
          ))}
        </div>
      ) : null}

      {heatmap.data?.error ? (
        <p className="text-sm text-red-300">{heatmap.data.error}</p>
      ) : !cells.length ? (
        <p className="text-sm text-slate-500">No heatmap data — start scanner or wait for market.</p>
      ) : (
        <>
          <p className="text-xs text-slate-500">
            {summary.executable ?? 0} executable · {summary.credit_signals ?? 0} credit ·{' '}
            {summary.bullish_signals ?? 0} buy call · {summary.bearish_signals ?? 0} buy put
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            {cells.map((c) => (
              <article
                key={c.instrument || c.action}
                className={cn(
                  'rounded-lg border p-3 text-xs',
                  actionClass(c.action),
                  c.plan_allowed ? 'opacity-100' : 'opacity-75',
                )}
              >
                <header className="font-semibold text-slate-200">{c.instrument}</header>
                <p className="mt-1 text-sm font-medium text-slate-100">{c.action || '—'}</p>
                <p className="mt-1 text-slate-500">
                  {[c.cpr_regime || c.cpr_width_class, c.cpr_position, c.ema_bias, c.structure]
                    .filter(Boolean)
                    .join(' · ')}
                </p>
                {c.confidence != null ? (
                  <p className="text-slate-400">{(c.confidence * 100).toFixed(0)}% conf</p>
                ) : null}
                <p className="mt-1 text-slate-500">
                  {c.plan_allowed ? 'Plan OK' : c.plan_reason || c.error || '—'}
                </p>
              </article>
            ))}
          </div>
        </>
      )}

    </div>
  )
}
