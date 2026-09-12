import { futuresToLogRows, futuresToTradeRows, type FuturesJournalRow } from '../lib/futuresRows'
import { useJournal } from './useJournal'

/** Closed futures paper trades (index + stock), mapped into the index row shapes. */
export function useFuturesJournal(enabled: boolean) {
  return useJournal<FuturesJournalRow>(
    'futures-journal',
    '/api/futures/journal?limit=1000',
    20_000,
    enabled,
    futuresToLogRows,
    futuresToTradeRows,
  )
}
