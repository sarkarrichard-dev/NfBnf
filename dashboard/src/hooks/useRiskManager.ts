import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePollMs } from './usePageVisible'

export type RiskDay = {
  state: 'NORMAL' | 'TIGHTENED' | 'STOPPED'
  limit_rupees: number
  lost_rupees: number
  used_pct: number
  india_live_rupees: number
  crypto_live_usd: number
  crypto_live_rupees: number
  message: string
}

export type SizeSuggestion = {
  venue: 'india' | 'crypto' | 'commodities'
  strategy: string
  instrument: string
  trades: number
  net: number
  currency: 'INR' | 'USD'
  suggestion: 'cut to smallest size' | 'eligible for more — your call' | 'keep' | 'collecting'
  why: string
}

export type RiskManager = { day: RiskDay; suggestions: SizeSuggestion[] }

export function useRiskManager(enabled: boolean) {
  const poll = usePollMs(60_000, enabled)
  return useQuery({
    queryKey: ['risk-manager'],
    queryFn: () => api<RiskManager>('/api/risk-manager'),
    refetchInterval: poll,
    enabled,
    placeholderData: keepPreviousData,
  })
}
