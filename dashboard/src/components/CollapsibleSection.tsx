import { useState, type ReactNode } from 'react'
import { cn } from '../lib/cn'
import { fx } from '../lib/theme'

type Props = {
  title: string
  summary?: string
  defaultOpen?: boolean
  children: ReactNode
  actions?: ReactNode
}

/** Collapsible panel — children mount only when open (saves API polls). */
export function CollapsibleSection({
  title,
  summary,
  defaultOpen = false,
  children,
  actions,
}: Props) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <details
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
      className={cn(fx.panel, 'group')}
    >
      <summary
        className={cn(
          'cursor-pointer list-none px-4 py-3 text-sm font-medium text-cyan-50/90',
          '[&::-webkit-details-marker]:hidden',
        )}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span>{title}</span>
          {summary ? (
            <span className="text-xs font-normal text-cyan-200/40">{summary}</span>
          ) : null}
        </div>
      </summary>
      {open ? (
        <div className="border-t border-cyan-500/10 px-4 py-4">
          {actions ? <div className="mb-3 flex flex-wrap gap-2">{actions}</div> : null}
          {children}
        </div>
      ) : null}
    </details>
  )
}
