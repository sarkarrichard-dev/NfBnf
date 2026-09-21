import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { usePollMs } from '../hooks/usePageVisible'
import { pctRate } from '../lib/pnl'
import { Button } from './ui/Button'
import { StatTile } from './ui/StatTile'

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
        <Button
          onClick={() => optimize.mutate()}
        >
          Optimize from OI
        </Button>
        <Button
          onClick={() => retrain.mutate()}
        >
          Retrain ML
        </Button>
        <Button
          onClick={() => hfSync.mutate()}
        >
          Sync HF dataset
        </Button>
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
          <StatTile key={String(label)} label={String(label)} value={String(val)} />
        ))}
      </div>
    </div>
  )
}
