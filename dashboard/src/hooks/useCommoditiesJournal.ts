import { useMemo } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePollMs } from './usePageVisible'
import { futuresToLogRows, futuresToTradeRows, type FuturesJournalRow } from '../lib/futuresRows'

type CommodityJournalRow = FuturesJournalRow & { label?: string; lots?: number }

/** Closed MCX commodity paper trades, mapped into the index row shapes so the
 *  Reports / Trade-history pages can show them under the Commodities source. */
export function useCommoditiesJournal(enabled: boolean) {
  const poll = usePollMs(20_000, enabled)
  const q = useQuery({
    queryKey: ['commodities-journal'],
    queryFn: () => api<{ trades: CommodityJournalRow[] }>('/api/commodities/journal?limit=1000'),
    refetchInterval: poll,
    enabled,
    placeholderData: keepPreviousData,
  })

  const raw = q.data?.trades
  return useMemo(() => {
    const rows: FuturesJournalRow[] = (raw ?? []).map((r) => ({
      ...r,
      instrument: r.label || r.instrument,
      lot_size: r.lots ?? r.lot_size,
    }))
    return {
      trades: rows.length ? futuresToTradeRows(rows) : [],
      logRows: rows.length ? futuresToLogRows(rows) : [],
    }
  }, [raw])
}
