import type { ReactNode } from 'react'
import { cn } from '../lib/cn'

type Props = {
  title: string
  summary?: string
  defaultOpen?: boolean
  children: ReactNode
  actions?: ReactNode
}

export function CollapsibleSection({
  title,
  summary,
  defaultOpen = false,
  children,
  actions,
}: Props) {
  return (
    <details
      open={defaultOpen}
      className="rounded-xl border border-slate-800 bg-slate-900/60 group"
    >
      <summary
        className={cn(
          'cursor-pointer list-none px-4 py-3 text-sm font-medium text-slate-200',
          '[&::-webkit-details-marker]:hidden',
        )}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span>{title}</span>
          {summary ? <span className="text-xs font-normal text-slate-500">{summary}</span> : null}
        </div>
      </summary>
      <div className="border-t border-slate-800 px-4 py-4">
        {actions ? <div className="mb-3 flex flex-wrap gap-2">{actions}</div> : null}
        {children}
      </div>
    </details>
  )
}
