import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { usePollMs } from '../hooks/usePageVisible'
import { pctRate } from '../lib/pnl'

export function LearningPanel() {
  const qc = useQueryClient()
  const learningPoll = usePollMs(60_000)

  const { data } = useQuery({
    queryKey: ['learning'],
    queryFn: () => api<Record<string, unknown>>('/api/learning'),
    refetchInterval: learningPoll,
  })

  const optimize = useMutation({
    mutationFn: () => api('/api/learning/optimize', { method: 'POST' }),
    onSuccess: () => {
      toast.success('Optimized')
      void qc.invalidateQueries({ queryKey: ['learning'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const retrain = useMutation({
    mutationFn: () => api('/api/learning/retrain-ml', { method: 'POST' }),
    onSuccess: () => {
      toast.success('ML retrained')
      void qc.invalidateQueries({ queryKey: ['learning'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const hfSync = useMutation({
    mutationFn: () => api('/api/learning/hf-sync', { method: 'POST' }),
    onSuccess: () => {
      toast.success('HF synced')
      void qc.invalidateQueries({ queryKey: ['learning'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const learned = (data?.learned || {}) as Record<string, unknown>
  const ml = (learned.ml || {}) as Record<string, unknown>
  const hf = (learned.hf || {}) as Record<string, unknown>

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => optimize.mutate()}
          className="rounded border border-slate-700 px-3 py-1.5 text-xs text-slate-300"
        >
          Optimize from OI
        </button>
        <button
          type="button"
          onClick={() => retrain.mutate()}
          className="rounded border border-slate-700 px-3 py-1.5 text-xs text-slate-300"
        >
          Retrain ML
        </button>
        <button
          type="button"
          onClick={() => hfSync.mutate()}
          className="rounded border border-slate-700 px-3 py-1.5 text-xs text-slate-300"
        >
          Sync HF dataset
        </button>
      </div>

      <p className="text-sm text-slate-400">
        {String(learned.explanation || 'No learning data yet.')}
      </p>

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ['Effective min conf.', pctRate(learned.effective_min_confidence as number)],
          ['Recent win rate', pctRate(learned.trade_win_rate as number)],
          ['ML model', ml.ready ? `v${ml.version}` : String(ml.status || 'Not trained')],
          ['ML accuracy', ml.holdout_accuracy != null ? pctRate(ml.holdout_accuracy as number) : '—'],
          ['HF status', hf.ready ? 'Ready' : 'Off'],
          ['HF rows', String(hf.dataset_rows ?? '—')],
        ].map(([label, val]) => (
          <article key={String(label)} className="rounded-lg border border-slate-800 p-3">
            <span className="text-xs text-slate-500">{label}</span>
            <p className="font-semibold text-slate-200">{String(val)}</p>
          </article>
        ))}
      </div>
    </div>
  )
}
