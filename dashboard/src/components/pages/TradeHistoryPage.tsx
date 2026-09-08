import { useState } from 'react'
import { cn } from '../../lib/cn'
import { fx } from '../../lib/theme'
import { PeriodBar } from '../PeriodBar'
import { TradeLogTable } from '../TradeLogTable'
import type { DateRange, LogRow, PeriodKey, TradeRow } from '../../types/analytics'

/** Trade history — a dedicated page for the closed round-trip log, with the
 *  shared period control and a one-click CSV export. */
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

  const exportUrl = (() => {
    const p = new URLSearchParams({ period })
    if (period === 'custom' && range.from && range.to) {
      p.set('from', range.from)
      p.set('to', range.to)
    }
    return `/api/reports/export?${p.toString()}`
  })()

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PeriodBar
          period={period}
          onPeriodChange={setPeriod}
          range={range}
          onRangeChange={setRange}
        />
        <a
          href={exportUrl}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-lg border border-[var(--hair)] bg-white/[0.04]',
            'px-3 py-1.5 text-xs font-semibold text-slate-200 hover:bg-white/[0.08]',
          )}
        >
          <svg viewBox="0 0 24 24" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 3v12m0 0 4-4m-4 4-4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
          </svg>
          Export CSV
        </a>
      </div>

      <section className={cn(fx.panel, 'p-4')}>
        <TradeLogTable
          logRows={logRows}
          trades={trades}
          period={period}
          range={range}
          mtmUpdatedAt={mtmUpdatedAt}
        />
      </section>
    </div>
  )
}
