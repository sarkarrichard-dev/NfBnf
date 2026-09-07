import { cn } from '../lib/cn'
import { istTodayDate } from '../lib/ist'
import type { DateRange, PeriodKey } from '../types/analytics'

const PRESETS: { id: PeriodKey; label: string }[] = [
  { id: 'today', label: 'Today' },
  { id: 'week', label: 'Week' },
  { id: 'month', label: 'Month' },
  { id: 'all', label: 'All' },
]

type Props = {
  period: PeriodKey
  onPeriodChange: (p: PeriodKey) => void
  range: DateRange
  onRangeChange: (r: DateRange) => void
}

/** Today | Week | Month | All  +  a start/end date range. Shared by the index
 *  and crypto tabs. Editing a date switches to the 'custom' period. */
export function PeriodBar({ period, onPeriodChange, range, onRangeChange }: Props) {
  const today = istTodayDate()
  const dateCls =
    'rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-xs text-slate-200 ' +
    'outline-none [color-scheme:dark] focus:border-[var(--acc)]'

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <div
        className="flex items-center gap-0.5 rounded-lg border border-white/[0.06] bg-white/[0.02] p-0.5"
        role="tablist"
        aria-label="Period"
      >
        {PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            role="tab"
            aria-selected={period === p.id}
            onClick={() => onPeriodChange(p.id)}
            className={cn(
              'rounded-md px-3 py-1 text-xs font-medium transition',
              period === p.id ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200',
            )}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div
        className={cn(
          'flex items-center gap-1.5 rounded-lg border p-1 transition-colors',
          period === 'custom' ? 'border-[var(--acc)]/50 bg-[var(--acc)]/[0.06]' : 'border-white/[0.06]',
        )}
      >
        <input
          type="date"
          aria-label="Start date"
          max={range.to || today}
          value={range.from}
          onChange={(e) => {
            onRangeChange({ from: e.target.value, to: range.to || e.target.value })
            if (e.target.value) onPeriodChange('custom')
          }}
          className={dateCls}
        />
        <span className="text-xs text-slate-500">→</span>
        <input
          type="date"
          aria-label="End date"
          min={range.from || undefined}
          max={today}
          value={range.to}
          onChange={(e) => {
            onRangeChange({ from: range.from || e.target.value, to: e.target.value })
            if (e.target.value) onPeriodChange('custom')
          }}
          className={dateCls}
        />
      </div>
    </div>
  )
}
