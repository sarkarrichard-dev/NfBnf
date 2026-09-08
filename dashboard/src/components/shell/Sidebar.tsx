import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'

export type NavItem = {
  id: string
  label: string
  icon?: ReactNode
  badge?: string | number | null
}
export type NavGroup = { label?: string; items: NavItem[] }

/** Per-section sidebar — grouped nav with tiny mono dividers, an accent-soft
 *  active state. On mobile it slides in over a backdrop. */
export function Sidebar({
  groups,
  active,
  onSelect,
  open,
  onClose,
}: {
  groups: NavGroup[]
  active: string
  onSelect: (id: string) => void
  open: boolean
  onClose: () => void
}) {
  return (
    <>
      {open ? (
        <button
          aria-label="Close menu"
          onClick={onClose}
          className="fixed inset-0 z-30 bg-black/50 md:hidden"
        />
      ) : null}
      <aside
        className={cn(
          'w-[13.75rem] shrink-0 border-r border-[var(--hair)] px-3 py-5',
          'md:sticky md:top-[3.3rem] md:block md:h-[calc(100vh-3.3rem)] md:overflow-y-auto',
          open
            ? 'fixed inset-y-0 left-0 z-40 overflow-y-auto bg-[var(--ground)]'
            : 'hidden',
        )}
      >
        {groups.map((g, gi) => (
          <div key={gi} className="mb-5">
            {g.label ? (
              <p className="mb-1.5 px-2 font-mono text-[10px] font-medium uppercase tracking-[0.13em] text-slate-600">
                {g.label}
              </p>
            ) : null}
            <nav className="flex flex-col gap-0.5">
              {g.items.map((it) => {
                const on = it.id === active
                return (
                  <button
                    key={it.id}
                    type="button"
                    onClick={() => onSelect(it.id)}
                    aria-current={on ? 'page' : undefined}
                    className={cn(
                      'flex items-center gap-2.5 rounded-lg px-2 py-[7px] text-[13.5px] font-medium',
                      'transition-colors duration-75',
                      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--acc)]/70',
                      on
                        ? 'bg-[var(--acc-soft)] text-[var(--acc)]'
                        : 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-100',
                    )}
                  >
                    {it.icon ? (
                      <span className="grid size-4 shrink-0 place-items-center opacity-90">
                        {it.icon}
                      </span>
                    ) : null}
                    <span className="flex-1 truncate text-left">{it.label}</span>
                    {it.badge != null && it.badge !== '' ? (
                      <span className="rounded-full bg-white/10 px-1.5 py-px font-mono text-[10px] tabular-nums text-slate-300">
                        {it.badge}
                      </span>
                    ) : null}
                  </button>
                )
              })}
            </nav>
          </div>
        ))}
      </aside>
    </>
  )
}
