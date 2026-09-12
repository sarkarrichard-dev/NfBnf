import { futuresToLogRows, futuresToTradeRows, type FuturesJournalRow } from '../lib/futuresRows'
import { useJournal } from './useJournal'

type CommodityJournalRow = FuturesJournalRow & { label?: string; lots?: number }

const remap = (r: CommodityJournalRow): FuturesJournalRow => ({
  ...r,
  instrument: r.label || r.instrument,
  lot_size: r.lots ?? r.lot_size,
})

/** Closed MCX commodity paper trades, mapped into the index row shapes so the
 *  Reports / Trade-history pages can show them under the Commodities source. */
export function useCommoditiesJournal(enabled: boolean) {
  return useJournal<CommodityJournalRow>(
    'commodities-journal',
    '/api/commodities/journal?limit=1000',
    20_000,
    enabled,
    (rows) => futuresToLogRows(rows.map(remap)),
    (rows) => futuresToTradeRows(rows.map(remap)),
  )
}
