import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { usePollMs } from './usePageVisible'

type Was = { was: number | null; now: number | null }

// Only the fields the panel reads. The stored result also carries internal keys
// (rules, baselines, per-row distance/baseline) that the UI deliberately ignores.
export type ExitRecheckRow = {
  segment: string
  venue: 'india' | 'crypto' | 'commodities'
  lane: 'buy' | 'sell' | 'all'
  instrument: string
  label: string
  distance_label: string
  currency: 'INR' | 'USD'
  trades: number
  trading_days: number
  state: 'watching' | 'observing' | 'ready'
  frozen: boolean
  win_rate: number | null
  trail_hit_rate: number | null
  net: number
  verdict:
    | 'not_enough_data'
    | 'working'
    | 'no_replay_data'
    | 'replay_unreliable'
    | 'no_better_distance'
    | 'suggestion'
  message: string
  suggestion: Record<string, unknown> | null
  drift: boolean
  drift_detail: { trail_hit_rate: Was; win_rate: Was } | null
}

export type ExitRecheck = {
  ran_at: string | null
  trigger: 'daily' | 'button' | null
  segments: ExitRecheckRow[]
  errors: { venue: string; error: string }[]
}

export function useExitRecheck(enabled: boolean) {
  const poll = usePollMs(60_000, enabled)
  return useQuery({
    queryKey: ['exit-recheck'],
    queryFn: () => api<ExitRecheck>('/api/exit-recheck'),
    refetchInterval: poll,
    enabled,
    placeholderData: keepPreviousData,
  })
}

export function useRunExitRecheck() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api<ExitRecheck>('/api/exit-recheck/run', { method: 'POST' }),
    onSuccess: () => {
      toast.success('Stop check done')
      void qc.invalidateQueries({ queryKey: ['exit-recheck'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })
}
