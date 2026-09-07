import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { cn } from '../lib/cn'
import { fx } from '../lib/theme'
import { inr, pnlCls, usd } from '../lib/cryptoFmt'
import { Button } from './ui/Button'

type Trade = {
  asset?: string
  strategy?: string
  side?: string
  opened_ist?: string
  closed_ist?: string | null
  entry?: number
  exit?: number
  pnl_usd?: number | null
  pnl_inr?: number | null
  peak_pnl_pct?: number | null
  entry_reason?: string | null
  exit_reason?: string | null
}
type Summary = {
  date?: string
  closed?: number
  wins?: number
  losses?: number
  win_rate?: number | null
  net_usd?: number
  net_inr?: number
  how_trades_ended?: Record<string, number>
  fee_bled_trades?: number
}
type Review = {
  narrative?: string
  went_right?: string[]
  went_wrong?: string[]
  watch?: string[]
  source?: string
}
type DayReview = { generated_at_ist?: string; summary?: Summary; trades?: Trade[]; review?: Review }

const n2 = (v?: number | null) => (typeof v === 'number' && Number.isFinite(v) ? v : '—')

/** Crypto twin of DayReviewPanel — same shell, USD/₹, crypto trade shape. */
export function CryptoDayReviewPanel() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['crypto', 'day-review'],
    queryFn: () => api<DayReview>('/api/crypto/day-review'),
    refetchInterval: 60_000,
  })
  const regen = useMutation({
    mutationFn: () => api<DayReview>('/api/crypto/day-review?refresh=true'),
    onSuccess: () => {
      toast.success('Crypto review refreshed')
      void qc.invalidateQueries({ queryKey: ['crypto', 'day-review'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const s = data?.summary
  const r = data?.review
  const trades = data?.trades ?? []

  return (
    <div className="space-y-4">
      <section className={cn(fx.panel, 'p-4')}>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold tracking-wide text-slate-200">
            Crypto day review{s?.date ? ` · ${s.date}` : ''}
          </h2>
          <Button onClick={() => regen.mutate()} pending={regen.isPending}>
            {regen.isPending ? 'Thinking…' : 'Regenerate'}
          </Button>
        </div>

        <div className="mb-4 flex flex-wrap gap-x-6 gap-y-2 text-sm">
          <div>
            <span className="block text-xs text-slate-400">Net P&amp;L</span>
            <strong className={cn('text-lg tabular-nums', pnlCls(s?.net_usd ?? 0))}>
              {usd(s?.net_usd ?? 0)}{' '}
              <span className="text-xs text-slate-500">{inr(s?.net_inr ?? 0)}</span>
            </strong>
          </div>
          <div>
            <span className="block text-xs text-slate-400">Record</span>
            <strong className="text-slate-100">
              {s?.wins ?? 0}W / {s?.losses ?? 0}L
              {s?.win_rate != null ? ` · ${(s.win_rate * 100).toFixed(0)}%` : ''}
            </strong>
          </div>
          <div>
            <span className="block text-xs text-slate-400">Trades</span>
            <strong className="text-slate-100">{s?.closed ?? 0} closed</strong>
          </div>
          {s?.how_trades_ended && Object.keys(s.how_trades_ended).length ? (
            <div>
              <span className="block text-xs text-slate-400">How they ended</span>
              <strong className="text-slate-100">
                {Object.entries(s.how_trades_ended)
                  .map(([k, v]) => `${k} ${v}`)
                  .join(' · ')}
              </strong>
            </div>
          ) : null}
          {s?.fee_bled_trades ? (
            <div>
              <span className="block text-xs text-slate-400">Fee-bled</span>
              <strong className="text-[var(--warn)]">{s.fee_bled_trades}</strong>
            </div>
          ) : null}
        </div>

        {r?.narrative ? (
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3">
            <div className="mb-1 flex items-center gap-2">
              <h4 className="text-xs uppercase tracking-wide text-slate-500">How today went</h4>
              <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">
                {r.source === 'ai' ? 'AI' : 'local'} · advisory only
              </span>
            </div>
            <p className="whitespace-pre-wrap text-sm text-slate-300">{r.narrative}</p>
          </div>
        ) : null}

        {r?.went_right?.length || r?.went_wrong?.length ? (
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg border border-[var(--up)]/20 bg-[var(--up)]/[0.04] p-3">
              <h4 className="mb-1.5 text-xs uppercase tracking-wide text-[var(--up)]/80">
                What went right
              </h4>
              <ul className="space-y-1 text-sm text-slate-300">
                {(r?.went_right ?? []).map((x, i) => (
                  <li key={i} className="flex gap-1.5">
                    <span className="text-[var(--up)]">+</span>
                    {x}
                  </li>
                ))}
                {!r?.went_right?.length ? <li className="text-slate-500">—</li> : null}
              </ul>
            </div>
            <div className="rounded-lg border border-[var(--down)]/20 bg-[var(--down)]/[0.04] p-3">
              <h4 className="mb-1.5 text-xs uppercase tracking-wide text-[var(--down)]/80">
                What went wrong
              </h4>
              <ul className="space-y-1 text-sm text-slate-300">
                {(r?.went_wrong ?? []).map((x, i) => (
                  <li key={i} className="flex gap-1.5">
                    <span className="text-[var(--down)]">−</span>
                    {x}
                  </li>
                ))}
                {!r?.went_wrong?.length ? <li className="text-slate-500">—</li> : null}
              </ul>
            </div>
          </div>
        ) : null}

        {r?.watch?.length ? (
          <div className="mt-3 rounded-lg border border-[var(--warn)]/20 bg-[var(--warn)]/[0.04] p-3">
            <h4 className="mb-1.5 text-xs uppercase tracking-wide text-[var(--warn)]/80">
              Watch next
            </h4>
            <ul className="space-y-1 text-sm text-slate-300">
              {r.watch.map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      <section className={cn(fx.panel, 'p-4')}>
        <h2 className="mb-1 text-sm font-semibold tracking-wide text-slate-200">
          Trade rationale — why each trade was taken and exited
        </h2>
        <p className="mb-3 text-xs text-slate-500">Today only.</p>
        {!trades.length ? (
          <p className="text-sm text-slate-500">No trades yet today.</p>
        ) : (
          <div className="space-y-2">
            {trades.map((t, i) => (
              <article
                key={i}
                className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <div className="text-sm">
                    <strong className="text-slate-100">{t.asset}</strong>{' '}
                    <span className="text-slate-400">{t.strategy}</span>{' '}
                    <span
                      className={t.side === 'long' ? 'text-[var(--up)]' : 'text-[var(--down)]'}
                    >
                      {t.side}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 text-xs tabular-nums">
                    <span className="text-slate-500">
                      {t.opened_ist} → {t.closed_ist}
                    </span>
                    <span className="text-slate-400">
                      ${n2(t.entry)} → ${n2(t.exit)}
                    </span>
                    <strong className={pnlCls(t.pnl_usd)}>
                      {usd(t.pnl_usd)} <span className="text-slate-600">{inr(t.pnl_inr)}</span>
                    </strong>
                  </div>
                </div>
                <dl className="mt-2 space-y-1 text-xs">
                  <div className="flex gap-2">
                    <dt className="shrink-0 text-[var(--up)]/70">Why in</dt>
                    <dd className="text-slate-300">{t.entry_reason || '—'}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="shrink-0 text-[var(--down)]/70">Why out</dt>
                    <dd className="text-slate-300">{t.exit_reason || '—'}</dd>
                  </div>
                </dl>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
