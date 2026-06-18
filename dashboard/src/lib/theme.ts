/** Shared futuristic UI surface classes */
export const fx = {
  panel:
    'rounded-xl border border-cyan-500/15 bg-slate-950/55 shadow-[0_0_40px_-16px_rgba(34,211,238,0.35),inset_0_1px_0_0_rgba(255,255,255,0.06)] backdrop-blur-md',
  card:
    'rounded-lg border border-cyan-500/10 bg-gradient-to-br from-slate-900/70 to-slate-950/90 px-2.5 py-2 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.05)]',
  cardLabel: 'text-[11px] font-medium text-cyan-200/50',
  cardValue: 'mt-0.5 text-sm font-semibold tabular-nums leading-tight text-slate-100',
  tabActive:
    'border-cyan-400/50 bg-cyan-400/10 text-cyan-100 shadow-[0_0_12px_-4px_rgba(34,211,238,0.5)]',
  tabIdle:
    'border-slate-700/60 text-slate-400 hover:border-cyan-500/30 hover:text-cyan-100/80',
} as const
