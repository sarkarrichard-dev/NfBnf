/** Shared UI surface classes — flat hairline cards on a near-black ground, one
 *  blue accent, mono numbers. Modelled on the web.cryptomaty.com app UI.
 *  Colours come from the CSS custom properties in index.css so a palette change
 *  is one file. */
export const fx = {
  /** Section container — flat, one hairline, 12px radius, no shadow or glow. */
  panel: 'rounded-xl border border-[var(--hair)] bg-[var(--panel)]',
  /** Stat tile — a lighter fill inside a panel. */
  card:
    'rounded-xl border border-[var(--hair-soft)] bg-white/[0.02] px-3 py-2.5',
  cardLabel:
    'font-mono text-[10.5px] font-medium uppercase tracking-[0.09em] text-slate-400',
  cardValue: 'mt-1 font-mono text-sm font-semibold tabular-nums leading-tight text-slate-50',
  /** Active section / view tab — accent text over an accent underline. */
  tabActive: 'border-[var(--acc)] text-[var(--acc)]',
  tabIdle:
    'border-transparent text-slate-400 hover:border-[var(--hair)] hover:text-slate-100',
} as const
