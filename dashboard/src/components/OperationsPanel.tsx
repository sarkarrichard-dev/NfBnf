import { memo } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePollMs } from '../hooks/usePageVisible'
import { cn } from '../lib/cn'
import { fx } from '../lib/theme'
import { Button } from './ui/Button'

type OpsItem = { code?: string; title?: string; detail?: string }

type OpsStatus = {
  updated_at_ist?: string
  trading_mode?: string
  can_enter_trades?: boolean
  can_send_live_orders?: boolean
  blockers?: OpsItem[]
  warnings?: OpsItem[]
  ok?: OpsItem[]
  scanner?: {
    running?: boolean
    cycles?: number
    executions?: number
    last_cycle_at_ist?: string
    last_error?: string
    open_trades_paper?: number
    open_trades_live?: number
  }
  events?: Array<Record<string, unknown>>
  log_tail?: string[]
  log_file?: string
}

type ScanPreview = {
  instrument?: string
  action?: string
  confidence?: number
  plan_allowed?: boolean
  plan_reason?: string
  reason?: string
  error?: string
}

function formatEvent(e: Record<string, unknown>): string {
  const parts = [e.at_ist, e.event, e.instrument, e.action, e.reason, e.trade_id, e.status]
  return parts.filter(Boolean).join(' · ')
}

export const OperationsPanel = memo(function OperationsPanel() {
  const qc = useQueryClient()
  const poll = usePollMs(2_000)

  const ops = useQuery({
    queryKey: ['ops-status'],
    queryFn: () => api<OpsStatus>('/api/ops/status'),
    refetchInterval: poll,
  })

  const probe = useMutation({
    mutationFn: (instrument: string) =>
      api<ScanPreview>(`/api/ops/scan-preview?instrument=${encodeURIComponent(instrument)}`),
  })

  const data = ops.data
  const blockers = data?.blockers ?? []
  const canEnter = data?.can_enter_trades
  const mode = (data?.trading_mode || 'PAPER').toUpperCase()
  const scanner = data?.scanner

  return (
    <section className={cn(fx.panel, 'mb-4 p-4')}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-cyan-50">Operations</h2>
          <p className="mt-0.5 text-xs text-cyan-200/45">
            Why trades are or aren&apos;t firing · log: {data?.log_file || 'memory/server.log'}
            {data?.updated_at_ist ? ` · ${data.updated_at_ist}` : ''}
          </p>
        </div>
        <div
          className={cn(
            'rounded-lg border px-3 py-1.5 text-sm font-semibold',
            canEnter
              ? 'border-emerald-400/40 bg-emerald-400/10 text-emerald-200'
              : 'border-red-400/40 bg-red-400/10 text-red-200',
          )}
        >
          {canEnter ? 'Ready to enter trades' : 'Not entering trades'}
        </div>
      </div>

      {blockers.length > 0 ? (
        <ul className="mb-3 space-y-1">
          {blockers.map((b) => (
            <li
              key={b.code || b.title}
              className="rounded border border-red-500/30 bg-red-950/25 px-3 py-2 text-xs text-red-100"
            >
              <strong>{b.title}</strong> — {b.detail}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mb-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {(data?.ok ?? []).map((item) => (
          <div
            key={item.code || item.title}
            className="rounded-lg border border-emerald-500/20 bg-emerald-950/20 px-2.5 py-2 text-xs"
          >
            <p className="font-medium text-emerald-200">{item.title}</p>
            <p className="mt-0.5 text-emerald-100/70">{item.detail}</p>
          </div>
        ))}
        {(data?.warnings ?? []).map((item) => (
          <div
            key={item.code || item.title}
            className="rounded-lg border border-amber-500/25 bg-amber-950/20 px-2.5 py-2 text-xs"
          >
            <p className="font-medium text-amber-200">{item.title}</p>
            <p className="mt-0.5 text-amber-100/80">{item.detail}</p>
          </div>
        ))}
      </div>

      <p className="mb-2 text-xs text-cyan-200/50">
        Mode <strong className="text-cyan-100">{mode}</strong>
        {mode === 'PAPER' ? ' — journal only, no Dhan orders' : ''}
        {mode === 'LIVE' && !data?.can_send_live_orders ? ' — live orders blocked (see above)' : ''}
        {' · '}
        Scanner {scanner?.running ? 'on' : 'off'}
        {scanner?.cycles != null ? ` · ${scanner.cycles} cycles` : ''}
        {scanner?.executions != null ? ` · ${scanner.executions} algo entries` : ''}
        {scanner?.last_cycle_at_ist ? ` · last cycle ${scanner.last_cycle_at_ist}` : ''}
      </p>

      <div className="mb-3 flex flex-wrap gap-2">
        {['NIFTY', 'BANKNIFTY', 'SENSEX'].map((inst) => (
          <Button
            key={inst}
            pending={probe.isPending}
            onClick={() => probe.mutate(inst)}
          >
            Test signal · {inst}
          </Button>
        ))}
        <Button
          onClick={() => {
            for (const key of [
              'ops-status',
              'auto-status',
              'status',
              'analytics',
              'journal',
              'live-mtm',
            ]) {
              void qc.invalidateQueries({ queryKey: [key] })
            }
          }}
        >
          Refresh
        </Button>
      </div>

      {probe.data ? (
        <pre className="mb-3 max-h-32 overflow-auto rounded border border-cyan-500/20 bg-black/30 p-2 text-[11px] text-cyan-100/80">
          {JSON.stringify(probe.data, null, 2)}
        </pre>
      ) : null}

      <details open className="mb-2 rounded-lg border border-slate-700/80">
        <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-slate-300">
          Scanner events ({data?.events?.length ?? 0})
        </summary>
        <pre className="max-h-40 overflow-auto px-3 pb-3 text-[11px] text-slate-400">
          {(data?.events ?? []).map(formatEvent).join('\n') || 'No events yet — start scanner.'}
        </pre>
      </details>

      <details className="rounded-lg border border-slate-700/80">
        <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-slate-300">
          Server log tail ({data?.log_tail?.length ?? 0} lines)
        </summary>
        <pre className="max-h-48 overflow-auto px-3 pb-3 text-[11px] text-slate-500">
          {(data?.log_tail ?? []).join('\n') || 'Log empty — restart server with Start Index Options AI.cmd'}
        </pre>
      </details>
    </section>
  )
})
