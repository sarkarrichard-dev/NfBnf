import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { usePollMs } from '../hooks/usePageVisible'
import { Button } from './ui/Button'

type BrainStatus = {
  enabled?: boolean
  trained_at_ist?: string | null
  rows?: number
  live_rows?: number
  seeded_with_backtest?: boolean
  gate_armed?: boolean
  min_win_prob_gate?: number
  oos_delta_rupees?: number | null
  oos_static_rupees?: number | null
  oos_gated_rupees?: number | null
  kept_fraction?: number | null
  top_features?: [string, number][]
  model_present?: boolean
}

type Commentary = {
  text?: string
  source?: string
  generated_at_ist?: string
}

const rupees = (v?: number | null) =>
  v == null ? '—' : `${v < 0 ? '-' : ''}₹${Math.abs(Math.round(v)).toLocaleString('en-IN')}`

export function BrainPanel() {
  const qc = useQueryClient()
  const poll = usePollMs(60_000)

  const { data } = useQuery({
    queryKey: ['brain'],
    queryFn: () => api<BrainStatus>('/api/brain/status'),
    refetchInterval: poll,
  })

  const commentary = useQuery({
    queryKey: ['brain-commentary'],
    queryFn: () => api<Commentary>('/api/brain/commentary?kind=pre_open'),
    refetchInterval: usePollMs(300_000),
  })

  const train = useMutation({
    mutationFn: () => api('/api/brain/train', { method: 'POST', body: JSON.stringify({}) }),
    onSuccess: (r: unknown) => {
      const res = r as { trained?: boolean; reason?: string; gate_armed?: boolean }
      if (res?.trained) toast.success(res.gate_armed ? 'Trained — gate armed' : 'Trained — gate stayed off')
      else toast.message(res?.reason ?? 'Not trained')
      void qc.invalidateQueries({ queryKey: ['brain'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const refreshBrief = useMutation({
    mutationFn: () => api('/api/brain/commentary?kind=pre_open&refresh=true'),
    onSuccess: () => {
      toast.success('Brief refreshed')
      void qc.invalidateQueries({ queryKey: ['brain-commentary'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const armed = data?.gate_armed === true
  const delta = data?.oos_delta_rupees

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
            armed ? 'bg-emerald-500/15 text-emerald-300' : 'bg-slate-700/50 text-slate-300'
          }`}
        >
          {armed ? `Gate armed @ ${((data?.min_win_prob_gate ?? 0) * 100).toFixed(0)}%` : 'Gate not armed'}
        </span>
        {data?.seeded_with_backtest ? (
          <span className="rounded-full bg-amber-500/15 px-2.5 py-1 text-xs text-amber-300">
            seeded with backtest rows
          </span>
        ) : null}
        <Button
          onClick={() => train.mutate()}
          pending={train.isPending}
        >
          {train.isPending ? 'Training…' : 'Retrain brain'}
        </Button>
        <Button
          onClick={() => refreshBrief.mutate()}
          pending={refreshBrief.isPending}
        >
          Refresh brief
        </Button>
      </div>

      {!armed ? (
        <p className="rounded-lg border border-slate-800 bg-slate-900/40 p-3 text-sm text-slate-400">
          The gate arms only when walk-forward validation shows it beats trading every setup,
          measured in rupees. Right now it does not{delta != null ? ` (${rupees(delta)} out-of-sample)` : ''},
          so every setup passes and only the regime filter is active.
        </p>
      ) : null}

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ['Training rows', `${data?.rows ?? 0} (${data?.live_rows ?? 0} live)`],
          ['OOS without gate', rupees(data?.oos_static_rupees)],
          ['OOS with gate', rupees(data?.oos_gated_rupees)],
          ['Gate edge', rupees(delta)],
          ['Trades kept', data?.kept_fraction != null ? `${(data.kept_fraction * 100).toFixed(0)}%` : '—'],
          ['Model file', data?.model_present ? 'Present' : 'Missing'],
          ['Last trained', data?.trained_at_ist ? data.trained_at_ist.slice(0, 16).replace('T', ' ') : '—'],
          ['Gate enabled', data?.enabled ? 'Yes' : 'No'],
        ].map(([label, val]) => (
          <article key={String(label)} className="rounded-lg border border-slate-800 p-3">
            <span className="text-xs text-slate-500">{label}</span>
            <p className="font-semibold text-slate-200">{String(val)}</p>
          </article>
        ))}
      </div>

      {data?.top_features?.length ? (
        <div>
          <h4 className="mb-2 text-xs uppercase tracking-wide text-slate-500">
            Strongest signals (model weights)
          </h4>
          <div className="flex flex-wrap gap-2">
            {data.top_features.map(([name, w]) => (
              <span
                key={name}
                className={`rounded border px-2 py-1 font-mono text-xs ${
                  w >= 0
                    ? 'border-emerald-800 text-emerald-300'
                    : 'border-rose-800 text-rose-300'
                }`}
                title={w >= 0 ? 'higher value → more likely a win' : 'higher value → more likely a loss'}
              >
                {name} {w >= 0 ? '+' : ''}
                {w.toFixed(3)}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {commentary.data?.text ? (
        <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
          <div className="mb-1 flex items-center gap-2">
            <h4 className="text-xs uppercase tracking-wide text-slate-500">AI brief</h4>
            <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">
              {commentary.data.source === 'claude' ? 'Claude' : 'local'} · advisory only
            </span>
          </div>
          <p className="whitespace-pre-wrap text-sm text-slate-300">{commentary.data.text}</p>
        </div>
      ) : null}
    </div>
  )
}
