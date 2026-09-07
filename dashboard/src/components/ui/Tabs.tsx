import { cn } from '../../lib/cn'

export type TabDef = { id: string; label: string; badge?: string | number | null }

/**
 * Top-level view switcher.
 *
 * A trading dashboard is watched live but configured rarely, so stacking every
 * panel on one screen buries the things that change by the minute under the
 * things that change monthly. The selection persists in localStorage so a
 * refresh mid-session returns to the view you were watching.
 */
export function Tabs({
  tabs,
  value,
  onChange,
}: {
  tabs: TabDef[]
  value: string
  onChange: (id: string) => void
}) {
  return (
    <div
      role="tablist"
      aria-label="Dashboard sections"
      className="mb-5 flex flex-wrap items-center gap-1 border-b border-slate-800 pb-px"
    >
      {tabs.map((t) => {
        const active = t.id === value
        return (
          <button
            key={t.id}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(t.id)}
            className={cn(
              'relative -mb-px inline-flex select-none items-center gap-2 rounded-t-md border-b-2 px-3.5 py-2',
              'text-sm font-medium transition-[color,border-color,background-color] duration-100',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/70',
              'active:bg-white/[0.04] motion-reduce:transition-none',
              active
                ? 'border-cyan-400 text-slate-100'
                : 'border-transparent text-slate-500 hover:border-slate-700 hover:text-slate-300',
            )}
          >
            {t.label}
            {t.badge != null && t.badge !== '' ? (
              <span
                className={cn(
                  'rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums',
                  active ? 'bg-cyan-500/20 text-cyan-200' : 'bg-slate-800 text-slate-400',
                )}
              >
                {t.badge}
              </span>
            ) : null}
          </button>
        )
      })}
    </div>
  )
}
