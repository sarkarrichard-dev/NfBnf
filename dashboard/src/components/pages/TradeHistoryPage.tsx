import { useMemo, useState } from 'react'
import { cn } from '../../lib/cn'
import { fx } from '../../lib/theme'
import { PeriodBar } from '../PeriodBar'
import { SourceToggle, useTradeSource } from '../SourceToggle'
import { TradeLogTable } from '../TradeLogTable'
import { useCryptoJournal } from '../../hooks/useCryptoJournal'
import { logRowsToCsv } from '../../lib/cryptoRows'
import { logRowsForPeriod } from '../../lib/pnl'
import type { DateRange, LogRow, PeriodKey, TradeRow } from '../../types/analytics'

/** Trade history — a dedicated page for the closed round-trip log, with the
 *  shared period control, an Index/Crypto/All source toggle, and CSV export. */
export function TradeHistoryPage({
  logRows,
  trades,
  mtmUpdatedAt,
}: {
  logRows: LogRow[]
  trades: TradeRow[]
  mtmUpdatedAt?: string
}) {
  const [period, setPeriod] = useState<PeriodKey>('month')
  const [range, setRange] = useState<DateRange>({ from: '', to: '' })
  const [source, setSource] = useTradeSource()
  const crypto = useCryptoJournal(source !== 'index')

  const allLogRows = useMemo<LogRow[]>(() => {
    if (source === 'index') return logRows
    if (source === 'crypto') return crypto.logRows
    return [...logRows, ...crypto.logRows]
  }, [source, logRows, crypto.logRows])

  const allTrades = useMemo<TradeRow[]>(() => {
    if (source === 'index') return trades
    if (source === 'crypto') return crypto.trades
    return [...trades, ...crypto.trades]
  }, [source, trades, crypto.trades])

  const serverExportUrl = (() => {
    const p = new URLSearchParams({ period })
    if (period === 'custom' && range.from && range.to) {
      p.set('from', range.from)
      p.set('to', range.to)
    }
    return `/api/reports/export?${p.toString()}`
  })()

  const clientCsvHref = useMemo(() => {
    if (source === 'index') return null
    const rows = logRowsForPeriod(allLogRows, allTrades, period, range)
    return `data:text/csv;charset=utf-8,${encodeURIComponent(logRowsToCsv(rows))}`
  }, [source, allLogRows, allTrades, period, range])

  const exportBtn =
    'inline-flex items-center gap-1.5 rounded-lg border border-[var(--hair)] bg-white/[0.04] ' +
    'px-3 py-1.5 text-xs font-semibold text-slate-200 hover:bg-white/[0.08]'
  const downloadIcon = (
    <svg viewBox="0 0 24 24" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3v12m0 0 4-4m-4 4-4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
    </svg>
  )

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <PeriodBar
            period={period}
            onPeriodChange={setPeriod}
            range={range}
            onRangeChange={setRange}
          />
          <SourceToggle value={source} onChange={setSource} />
        </div>
        {clientCsvHref ? (
          <a href={clientCsvHref} download={`trades-${source}-${period}.csv`} className={cn(exportBtn)}>
            {downloadIcon}
            Export CSV
          </a>
        ) : (
          <a href={serverExportUrl} className={cn(exportBtn)}>
            {downloadIcon}
            Export CSV
          </a>
        )}
      </div>

      <section className={cn(fx.panel, 'p-4')}>
        <TradeLogTable
          logRows={allLogRows}
          trades={allTrades}
          period={period}
          range={range}
          mtmUpdatedAt={mtmUpdatedAt}
        />
      </section>
    </div>
  )
}
