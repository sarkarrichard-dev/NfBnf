/** Shared UI surface classes — dark cards with a faint top highlight, hairline
 *  borders, one cyan accent. Colours come from the CSS custom properties in
 *  index.css so a palette change is one file. */
export const fx = {
  panel:
    'rounded-xl border border-[var(--hair)] bg-[var(--panel)] ' +
    'shadow-[inset_0_1px_0_var(--panel-hi),0_2px_8px_-2px_rgba(0,0,0,0.6)]',
  card:
    'rounded-lg border border-[var(--hair)] bg-white/[0.03] px-3 py-2.5 ' +
    'shadow-[inset_0_1px_0_var(--panel-hi)]',
  cardLabel: 'text-[11px] font-medium uppercase tracking-wide text-slate-400',
  cardValue: 'mt-1 text-sm font-semibold tabular-nums leading-tight text-slate-50',
  tabActive:
    'border-[var(--acc)] bg-[var(--acc)] text-[var(--acc-ink)] ' +
    'shadow-[0_0_16px_-2px_var(--acc)]',
  tabIdle:
    'border-[var(--hair)] text-slate-400 hover:border-white/25 hover:text-slate-100',
} as const
