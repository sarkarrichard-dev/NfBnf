// Run: npx tsx src/lib/cryptoRows.selfcheck.ts
import {
  cryptoToLogRows,
  cryptoToTradeRows,
  dailySeriesFromTrades,
  logRowsToCsv,
  mergeDailySeries,
  type CryptoJournalRow,
} from './cryptoRows'

function eq(a: unknown, b: unknown, msg: string) {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    throw new Error(`${msg}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`)
  }
}
function ok(c: boolean, msg: string) {
  if (!c) throw new Error(msg)
}

const rows: CryptoJournalRow[] = [
  {
    exit_id: 'ny_n_break:ETHUSD:1',
    strategy: 'ny_n_break',
    asset: 'ETHUSD',
    mode: 'paper',
    side: 'long',
    size: 30,
    entry_price: 2492.2,
    exit_price: 2489.3,
    opened_at: '2026-09-08T15:41:30.683384+00:00',
    exit_time: '2026-09-08 18:00:00+00:00',
    closed_at: '2026-09-08T18:05:35.753797+00:00',
    pnl_inr: -150.22,
    pnl_usd: -1.7673,
    exit_reason: 'trailing profit +10%',
  },
  {
    exit_id: 'ichimoku:BTCUSD:2',
    strategy: 'ichimoku',
    asset: 'BTCUSD',
    mode: 'paper',
    side: 'short',
    size: 30,
    entry_price: 61000,
    exit_price: 60500,
    opened_at: '2026-09-07T09:00:00+00:00',
    closed_at: '2026-09-07T10:00:00+00:00',
    pnl_inr: 420,
  },
]

const tr = cryptoToTradeRows(rows)
eq(tr.length, 2, 'trade rows count')
eq(tr[0].instrument, 'ETH', 'USD suffix stripped')
eq(tr[0].pnl, -150.22, 'pnl in INR')
eq(tr[0].is_open, false, 'crypto rows always closed')
ok(tr[0].id.startsWith('crypto:'), 'id namespaced')
eq(tr[0].entry_session_ok, true, 'session ok so it is not filtered out')

const lr = cryptoToLogRows(rows)
eq(lr[0].side, 'Buy', 'long leg → Buy')
eq(lr[1].side, 'Sell', 'short leg → Sell')
eq(lr[0].quote_ccy, 'USD', 'crypto prices are USD-quoted')
eq(lr[0].leg_count, 1, 'perp trade is a single leg')
eq(lr[0].avg_entry, 2492.2, 'entry price carried through')

// daily series buckets by UTC day of created_at, newest first
const ds = dailySeriesFromTrades(tr)
eq(ds, [
  { period: '2026-09-08', pnl_rupees: -150.22 },
  { period: '2026-09-07', pnl_rupees: 420 },
], 'daily series newest-first by UTC day')

const merged = mergeDailySeries(
  [{ period: '2026-09-08', pnl_rupees: 1000 }],
  ds,
)
eq(merged[0], { period: '2026-09-08', pnl_rupees: 849.78 }, 'same-day index+crypto summed')

// open trades / non-numeric pnl are skipped
eq(dailySeriesFromTrades([{ id: 'x', created_at: '2026-09-08T00:00:00+00:00', pnl: null }]), [], 'open trade skipped')

const csv = logRowsToCsv(lr)
ok(csv.split('\n').length === 3, 'header + 2 rows')
ok(csv.startsWith('Open,Close,Mode,Instrument,Side,Qty,Entry,Exit,PnL,Status'), 'csv header')

console.log('cryptoRows self-check ok')

