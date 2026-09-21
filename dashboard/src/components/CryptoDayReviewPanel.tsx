import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { cn } from '../lib/cn'
import { fx } from '../lib/theme'
import { inr, pnlCls, usd } from '../lib/cryptoFmt'
import { Button } from './ui/Button'

type AssetGroup = {
  trades: number
  wins: number
  losses: number
  win_rate?: number
  net_usd: number
  net_inr: number
  strategies: Record<string, number>
  exits: Record<string, number>
  best_usd?: number | null
  worst_usd?: number | null
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
  by_asset?: Record<string, AssetGroup>
}
type GroupNote = { group: string; read?: string; improve?: string }
type Review = {
  narrative?: string
  by_group?: GroupNote[]
  watch?: string[]
  source?: string
}
type DayReview = { generated_at_ist?: string; summary?: Summary; review?: Review }

const GROUP_LABEL: Record<string, string> = {
  BTC: 'Bitcoin',
  ETH: 'Ethereum',
  PAX: 'Gold (PAXG)',
  OTHER: 'Other (SOL, DOGE…)',
}

/** Crypto day review — grouped by asset (BTC / ETH / PAX / OTHER), not per trade.
 *  Each group shows the running tally plus the AI's read and what to improve. */
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
  const groups = Object.entries(s?.by_asset ?? {})
  const noteFor = (g: string) => (r?.by_group ?? []).find((x) => x.group?.toUpperCase() === g)

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
            <strong className={cn('font-mono text-lg tabular-nums', pnlCls(s?.net_usd ?? 0))}>
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
          {s?.fee_bled_trades ? (
            <div>
              <span className="block text-xs text-slate-400">Fee-bled</span>
              <strong className="text-[var(--warn)]">{s.fee_bled_trades}</strong>
            </div>
          ) : null}
        </div>

        {r?.narrative ? (
          <div className="rounded-md border border-white/[0.06] bg-white/[0.02] p-3">
            <div className="mb-1 flex items-center gap-2">
              <h4 className="text-xs uppercase tracking-wide text-slate-500">How today went</h4>
              <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">
                {r.source === 'ai' ? 'AI' : 'local'} · advisory only
              </span>
            </div>
            <p className="whitespace-pre-wrap text-sm text-slate-300">{r.narrative}</p>
          </div>
        ) : null}

        {r?.watch?.length ? (
          <div className="mt-3 rounded-md border border-[var(--warn)]/20 bg-[var(--warn)]/[0.04] p-3">
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
          By asset — the running tally and what to fix
        </h2>
        <p className="mb-3 text-xs text-slate-500">Today only. Updates as trades close.</p>
        {!groups.length ? (
          <p className="text-sm text-slate-500">No trades yet today.</p>
        ) : (
          <div className="space-y-2.5">
            {groups.map(([name, g]) => {
              const note = noteFor(name)
              return (
                <article
                  key={name}
                  className="rounded-md border border-white/[0.06] bg-white/[0.02] p-3"
                >
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <strong className="text-sm text-slate-100">
                      {GROUP_LABEL[name] ?? name}
                    </strong>
                    <div className="flex items-center gap-3 font-mono text-xs tabular-nums">
                      <span className="text-slate-400">
                        {g.trades} {g.trades === 1 ? 'trade' : 'trades'} · {g.wins}W / {g.losses}L
                      </span>
                      <strong className={pnlCls(g.net_usd)}>
                        {usd(g.net_usd)}{' '}
                        <span className="text-slate-600">{inr(g.net_inr)}</span>
                      </strong>
                    </div>
                  </div>

                  <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
                    <span>
                      Strategies:{' '}
                      {Object.entries(g.strategies)
                        .map(([k, v]) => `${k} ×${v}`)
                        .join(', ') || '—'}
                    </span>
                    <span>
                      Exits:{' '}
                      {Object.entries(g.exits)
                        .map(([k, v]) => `${k} ${v}`)
                        .join(' · ') || '—'}
                    </span>
                  </div>

                  {note?.read ? (
                    <p className="mt-2 text-xs text-slate-300">{note.read}</p>
                  ) : null}
                  {note?.improve ? (
                    <p className="mt-1 flex gap-1.5 text-xs text-[var(--warn)]/90">
                      <span className="shrink-0 font-semibold">Improve</span>
                      {note.improve}
                    </p>
                  ) : null}
                </article>
              )
            })}
          </div>
        )}
      </section>
    </div>
  )
}
