import { memo, useEffect } from 'react'
import { cn } from '../lib/cn'
import { formatPrice, legDisplayName, legPctChange, legPnlValue, money, pnlClass, signedQty } from '../lib/pnl'
import { getSeries, pushPoint } from '../hooks/useSeries'
import { Sparkline } from './Sparkline'
import type { LogRow } from '../types/analytics'

function PositionRowInner({
  row,
  showClose,
  closing,
  onClose,
}: {
  row: LogRow
  showClose?: boolean
  closing?: boolean
  onClose?: () => void
}) {
  const isBuy = row.side !== 'Sell'
  const qty = signedQty(row)
  const pnl = legPnlValue(row)
  const pct = legPctChange(row)

  const seriesKey = `pos:${row.trade_id}:${row.leg_index ?? 0}`
  useEffect(() => {
    pushPoint(seriesKey, pnl)
  }, [seriesKey, pnl])
  const trend = getSeries(seriesKey)

  return (
    <tr className="border-b border-slate-800/70 bg-[var(--up)]/[0.03] text-slate-200">
      <td className="px-3 py-2">
        <span
          className={cn(
            'inline-flex h-6 w-6 items-center justify-center rounded text-[11px] font-bold',
            isBuy
              ? 'border border-[var(--up)]/35 bg-[var(--up)]/15 text-[var(--up)]'
              : 'border border-[var(--down)]/35 bg-[var(--down)]/15 text-[var(--down)]',
          )}
          title={isBuy ? 'Buy' : 'Sell'}
        >
          {isBuy ? 'B' : 'S'}
        </span>
      </td>
      <td className="max-w-xs px-3 py-2 text-slate-100">
        <span>{legDisplayName(row)}</span>
        {trend.length > 1 ? (
          <Sparkline points={trend} width={64} height={12} className="mt-0.5 block" />
        ) : null}
      </td>
      <td className="px-3 py-2">
        <span className="rounded border border-slate-700 px-1.5 py-0.5 text-[11px] text-slate-300">
          {row.mode || '—'}
        </span>
      </td>
      <td
        className={cn(
          'px-3 py-2 text-right tabular-nums font-semibold',
          qty >= 0 ? 'text-[var(--up)]' : 'text-[var(--down)]',
        )}
      >
        {qty >= 0 ? '+' : ''}
        {qty}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">{formatPrice(row.avg_entry)}</td>
      <td className="px-3 py-2 text-right tabular-nums">{formatPrice(row.mark_price)}</td>
      <td className={cn('px-3 py-2 text-right tabular-nums font-semibold', pnlClass(pnl))}>
        {pnl != null ? money(pnl) : '—'}
      </td>
      <td className={cn('px-3 py-2 text-right tabular-nums', pnlClass(pnl))}>
        {pct != null ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : '—'}
      </td>
      <td className="px-3 py-2">
        {showClose && onClose ? (
          <button
            type="button"
            disabled={closing}
            onClick={onClose}
            className="rounded-md border border-[var(--down)]/40 px-2 py-1 font-sans text-[11px] font-semibold text-[var(--down)] transition hover:bg-[var(--down)]/10 disabled:opacity-40"
          >
            {closing ? 'Closing…' : 'Close'}
          </button>
        ) : null}
      </td>
    </tr>
  )
}

export const PositionRow = memo(
  PositionRowInner,
  (a, b) =>
    a.row.trade_id === b.row.trade_id &&
    a.row.leg_index === b.row.leg_index &&
    a.row.leg_mtm === b.row.leg_mtm &&
    a.showClose === b.showClose &&
    a.closing === b.closing &&
    a.row.mark_price === b.row.mark_price &&
    a.row.display_pnl === b.row.display_pnl,
)
