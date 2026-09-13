import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'

/** A deliberate "nothing here yet" box — dashed hairline border, muted
 *  caption — instead of a bare line of text floating in open space. Used
 *  everywhere a list or chart has no data for the current filter. */
export function EmptyState({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        'flex min-h-24 items-center justify-center rounded-lg border border-dashed border-[var(--hair)] bg-white/[0.01] px-4 py-6 text-center',
        className,
      )}
    >
      <p className="text-sm text-slate-500">{children}</p>
    </div>
  )
}
