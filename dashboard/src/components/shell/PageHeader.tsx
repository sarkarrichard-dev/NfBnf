import type { ReactNode } from 'react'

/** The header every page opens with — mono eyebrow → H1 → one-line status →
 *  optional actions on the right. Modelled on the Cryptomaty page pattern. */
export function PageHeader({
  eyebrow,
  title,
  status,
  actions,
}: {
  eyebrow?: ReactNode
  title: ReactNode
  status?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
      <div className="min-w-0">
        {eyebrow ? (
          <p className="font-mono text-[11px] font-semibold uppercase tracking-[0.13em] text-[var(--acc)]">
            {eyebrow}
          </p>
        ) : null}
        <h1 className="mt-1 text-[1.65rem] font-extrabold leading-none tracking-[-0.022em] text-slate-50">
          {title}
        </h1>
        {status ? <p className="mt-1.5 text-[13px] text-slate-500">{status}</p> : null}
      </div>
      {actions ? (
        <div className="flex flex-wrap items-center gap-2">{actions}</div>
      ) : null}
    </div>
  )
}
