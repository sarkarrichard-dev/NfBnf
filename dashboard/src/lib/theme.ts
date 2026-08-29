/** Shared UI surface classes — flat dark cards, hairline borders, blue accent. */
export const fx = {
  panel:
    'rounded-xl border border-white/[0.06] bg-[#101216] shadow-[0_1px_2px_0_rgba(0,0,0,0.4)]',
  card:
    'rounded-lg border border-white/[0.05] bg-white/[0.02] px-3 py-2.5',
  cardLabel: 'text-[11px] font-medium uppercase tracking-wide text-slate-500',
  cardValue: 'mt-1 text-sm font-semibold tabular-nums leading-tight text-slate-100',
  tabActive: 'border-blue-500 bg-blue-600 text-white',
  tabIdle:
    'border-white/[0.06] text-slate-400 hover:border-white/20 hover:text-slate-200',
} as const
