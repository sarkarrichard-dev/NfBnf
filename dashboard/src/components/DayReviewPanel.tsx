import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { cn } from '../lib/cn'
import { money, pnlClass } from '../lib/pnl'
import { fx } from '../lib/theme'
import { usePollMs } from '../hooks/usePageVisible'
import { Button } from './ui/Button'

type Trade = {
  id?: string
  opened_ist?: string
  closed_ist?: string | null
  instrument?: string
  structure?: string
  lots?: string
  entry_premium?: number | null
  exit_premium?: number | null
  pnl_rupees?: number | null
  is_open?: boolean
  signal_reason?: string | null
  exit_reason?: string | null
}

type Summary = {
  date?: string
  closed?: number
  open?: number
  wins?: number
  losses?: number
  win_rate?: number | null
  net_rupees?: number
  how_trades_ended?: Record<string, number>
}

type Review = {
  narrative?: string
  went_right?: string[]
  went_wrong?: string[]
  watch?: string[]
  source?: string
}

type DayReview = {
  generated_at_ist?: string
  summary?: Summary
  trades?: Trade[]
  review?: Review
}

const hhmm = (s?: string | null) => (s ? s.replace(/^.*?(\d{1,2}:\d{2}).*$/, '$1') : '—')

export function DayReviewPanel() {
  const qc = useQueryClient()

  const { data } = useQuery({
    queryKey: ['day-review'],
    queryFn: () => api<DayReview>('/api/day-review'),
    refetchInterval: usePollMs(60_000),
  })

  const regen = useMutation({
    mutationFn: () => api<DayReview>('/api/day-review?refresh=true'),
    onSuccess: () => {
      toast.success('Day review refreshed')
      void qc.invalidateQueries({ queryKey: ['day-review'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const s = data?.summary
  const r = data?.review
  const trades = data?.trades ?? []
  const net = s?.net_rupees ?? 0

  return (
    <div className="space-y-4">
      {/* ---- Section 1: summary + AI review ---- */}
      <section className={cn(fx.panel, 'p-4')}>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold tracking-wide text-slate-200">
            Day review{s?.date ? ` · ${s.date}` : ''}
          </h2>
          <Button onClick={() => regen.mutate()} pending={regen.isPending}>
            {regen.isPending ? 'Thinking…' : 'Regenerate'}
          </Button>
        </div>

        <div className="mb-4 flex flex-wrap gap-x-6 gap-y-2 text-sm">
          <div>
            <span className="block text-xs text-slate-400">Net P&amp;L</span>
            <strong className={cn('text-lg tabular-nums', pnlClass(net))}>{money(net)}</strong>
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
            <strong className="text-slate-100">
              {s?.closed ?? 0} closed{s?.open ? ` · ${s.open} open` : ''}
            </strong>
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

        {(r?.went_right?.length || r?.went_wrong?.length) ? (
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/[0.04] p-3">
              <h4 className="mb-1.5 text-xs uppercase tracking-wide text-emerald-300/80">
                What went right
              </h4>
              <ul className="space-y-1 text-sm text-slate-300">
                {(r?.went_right ?? []).map((x, i) => (
                  <li key={i} className="flex gap-1.5">
                    <span className="text-emerald-400">+</span>
                    {x}
                  </li>
                ))}
                {!r?.went_right?.length ? <li className="text-slate-500">—</li> : null}
              </ul>
            </div>
            <div className="rounded-lg border border-rose-500/20 bg-rose-500/[0.04] p-3">
              <h4 className="mb-1.5 text-xs uppercase tracking-wide text-rose-300/80">
                What went wrong
              </h4>
              <ul className="space-y-1 text-sm text-slate-300">
                {(r?.went_wrong ?? []).map((x, i) => (
                  <li key={i} className="flex gap-1.5">
                    <span className="text-rose-400">−</span>
                    {x}
                  </li>
                ))}
                {!r?.went_wrong?.length ? <li className="text-slate-500">—</li> : null}
              </ul>
            </div>
          </div>
        ) : null}

        {r?.watch?.length ? (
          <div className="mt-3 rounded-lg border border-amber-500/20 bg-amber-500/[0.04] p-3">
            <h4 className="mb-1.5 text-xs uppercase tracking-wide text-amber-300/80">
              Watch tomorrow
            </h4>
            <ul className="space-y-1 text-sm text-slate-300">
              {r.watch.map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      {/* ---- Section 2: per-trade why-in / why-out ---- */}
      <section className={cn(fx.panel, 'p-4')}>
        <h2 className="mb-1 text-sm font-semibold tracking-wide text-slate-200">
          Trade rationale — why each trade was taken and exited
        </h2>
        <p className="mb-3 text-xs text-slate-500">
          Today only. Entry reason is the signal that fired; exit reason is what closed it.
        </p>

        {!trades.length ? (
          <p className="text-sm text-slate-500">No trades yet today.</p>
        ) : (
          <div className="space-y-2">
            {trades.map((t) => (
              <article
                key={t.id}
                className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <div className="text-sm">
                    <strong className="text-slate-100">{t.instrument}</strong>{' '}
                    <span className="text-slate-400">{t.structure}</span>
                    {t.lots ? <span className="text-slate-500"> · {t.lots}</span> : null}
                  </div>
                  <div className="flex items-center gap-3 text-xs tabular-nums">
                    <span className="text-slate-500">
                      {hhmm(t.opened_ist)} → {t.is_open ? 'open' : hhmm(t.closed_ist)}
                    </span>
                    {t.entry_premium != null ? (
                      <span className="text-slate-400">
                        ₹{t.entry_premium}
                        {t.exit_premium != null ? ` → ₹${t.exit_premium}` : ''}
                      </span>
                    ) : null}
                    {t.pnl_rupees != null ? (
                      <strong className={pnlClass(t.pnl_rupees)}>{money(t.pnl_rupees)}</strong>
                    ) : (
                      <span className="text-slate-500">open</span>
                    )}
                  </div>
                </div>
                <dl className="mt-2 space-y-1 text-xs">
                  <div className="flex gap-2">
                    <dt className="shrink-0 text-emerald-300/70">Why in</dt>
                    <dd className="text-slate-300">{t.signal_reason || '—'}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="shrink-0 text-rose-300/70">Why out</dt>
                    <dd className="text-slate-300">
                      {t.is_open ? 'still open' : t.exit_reason || '—'}
                    </dd>
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
