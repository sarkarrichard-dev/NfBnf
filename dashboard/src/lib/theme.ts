/** Shared UI surface classes — flat hairline cards on a near-black ground, one
 *  hawk-gold accent, mono numbers. Colours come from the CSS custom properties
 *  in index.css so a palette change is one file. */
export const fx = {
  /** Section container — flat, one hairline, sharp instrument-panel corners. */
  panel: 'rounded-md border border-[var(--hair)] bg-[var(--panel)]',
  /** Stat tile — a lighter fill inside a panel. */
  card:
    'rounded-md border border-[var(--hair-soft)] bg-white/[0.02] px-3 py-2.5',
  cardLabel:
    'font-mono text-[10.5px] font-medium uppercase tracking-[0.09em] text-slate-400',
  cardValue: 'mt-1 font-mono text-sm font-semibold tabular-nums leading-tight text-slate-50',
  /** Active section / view tab — accent text over an accent underline. */
  tabActive: 'border-[var(--acc)] text-[var(--acc)]',
  tabIdle:
    'border-transparent text-slate-400 hover:border-[var(--hair)] hover:text-slate-100',
} as const
