import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePollMs } from '../hooks/usePageVisible'
import {
  mergeLogRows,
  mergeTradesLiveMtm,
  openLegRows,
  sortLogRowsStable,
} from '../lib/pnl'
import type {
  AnalyticsResponse,
  JournalResponse,
  LiveMtmResponse,
} from '../types/analytics'

const ANALYTICS_MS = 20_000
const JOURNAL_MS = 10_000
const MTM_MS = 1_500

export function useDashboardData() {
  const analyticsPoll = usePollMs(ANALYTICS_MS)
  const journalPoll = usePollMs(JOURNAL_MS)

  const analyticsQuery = useQuery({
    queryKey: ['analytics'],
    queryFn: () => api<AnalyticsResponse>('/api/analytics?enrich_mtm=false'),
    refetchInterval: analyticsPoll,
  })

  const journalQuery = useQuery({
    queryKey: ['journal'],
    queryFn: () => api<JournalResponse>('/api/trades/recent?limit=80'),
    refetchInterval: journalPoll,
  })

  const hasOpen = useMemo(() => {
    const rows = journalQuery.data?.rows ?? analyticsQuery.data?.trades ?? []
    return rows.some((t) => t.is_open)
  }, [journalQuery.data?.rows, analyticsQuery.data?.trades])

  const mtmPoll = usePollMs(MTM_MS, hasOpen)

  const mtmQuery = useQuery({
    queryKey: ['live-mtm'],
    queryFn: () => api<LiveMtmResponse>('/api/trades/live-mtm'),
    refetchInterval: mtmPoll,
    enabled: hasOpen,
  })

  const trades = useMemo(() => {
    const base = journalQuery.data?.rows?.length
      ? journalQuery.data.rows
      : analyticsQuery.data?.trades ?? []
    if (mtmQuery.data?.trades?.length) {
      return mergeTradesLiveMtm(base, mtmQuery.data.trades)
    }
    return base
  }, [analyticsQuery.data?.trades, journalQuery.data?.rows, mtmQuery.data?.trades])

  const logRows = useMemo(() => {
    let rows = analyticsQuery.data?.log_rows ?? []
    if (journalQuery.data?.log_rows?.length) {
      rows = mergeLogRows(rows, journalQuery.data.log_rows)
    }
    if (mtmQuery.data?.log_rows?.length) {
      rows = mergeLogRows(rows, mtmQuery.data.log_rows)
    }
    return sortLogRowsStable(rows)
  }, [
    analyticsQuery.data?.log_rows,
    journalQuery.data?.log_rows,
    mtmQuery.data?.log_rows,
  ])

  const openMtmRupees = useMemo(() => {
    if (mtmQuery.data?.open_mtm_rupees != null) {
      return mtmQuery.data.open_mtm_rupees
    }
    return trades
      .filter((t) => t.is_open)
      .reduce((s, t) => s + (Number(t.mtm_pnl) || 0), 0)
  }, [mtmQuery.data?.open_mtm_rupees, trades])

  const openCount = useMemo(
    () => openLegRows(logRows, trades, 'today').length,
    [logRows, trades],
  )

  return {
    analytics: analyticsQuery.data,
    trades,
    logRows,
    openMtmRupees,
    openCount,
    mtmUpdatedAt: mtmQuery.data?.updated_at_ist,
    journalUpdatedAt: journalQuery.data?.updated_at_ist,
    statsUpdatedAt: analyticsQuery.data?.generated_at_ist,
    isLoading: analyticsQuery.isLoading && !analyticsQuery.data,
    isError: analyticsQuery.isError,
    error: analyticsQuery.error,
    hasOpen,
    isFetching: analyticsQuery.isFetching || journalQuery.isFetching || mtmQuery.isFetching,
  }
}
