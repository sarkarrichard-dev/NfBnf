import { cryptoToLogRows, cryptoToTradeRows, type CryptoJournalRow } from '../lib/cryptoRows'
import { useJournal } from './useJournal'

/** Closed Delta-perp trades, mapped into the index dashboard's row shapes. */
export function useCryptoJournal(enabled: boolean) {
  return useJournal<CryptoJournalRow>(
    'crypto-journal',
    '/api/crypto/journal?limit=500',
    15_000,
    enabled,
    cryptoToLogRows,
    cryptoToTradeRows,
  )
}
