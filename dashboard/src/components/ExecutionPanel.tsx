import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { cn } from '../lib/cn'
import { money } from '../lib/pnl'
import { fx } from '../lib/theme'

type Gate = { title?: string; detail?: string; ok?: boolean }

type Props = {
  tradingMode?: string
  lotsPerTrade?: number
  orderQuantities?: Record<string, number>
  liveSummary?: {
    open?: number
    realized_pnl?: number
    open_mtm?: number
  }
  paperSummary?: {
    open?: number
    realized_pnl?: number
    open_mtm?: number
  }
  gates?: Gate[]
}

export function ExecutionPanel({
  tradingMode,
  lotsPerTrade = 1,
  orderQuantities,
  liveSummary,
  paperSummary,
  gates = [],
}: Props) {
  const qc = useQueryClient()
  const isPaper = (tradingMode || 'PAPER').toUpperCase() !== 'LIVE'
  const journalSummary = isPaper ? paperSummary : liveSummary
  const journalLabel = isPaper ? 'Paper journal' : 'Live journal'

  const setMode = useMutation({
    mutationFn: (mode: string) =>
      api('/api/trading/mode', {
        method: 'POST',
        body: JSON.stringify({ mode }),
      }),
    onSuccess: () => {
      toast.success('Trading mode updated')
      void qc.invalidateQueries({ queryKey: ['status'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const adjustLots = useMutation({
    mutationFn: (delta: number) =>
      api('/api/settings/lots', {
        method: 'POST',
        body: JSON.stringify({ delta }),
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['status'] }),
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <section className={cn(fx.panel, 'p-4')}>
      <h2 className="mb-3 text-sm font-medium text-cyan-100/80">Execution</h2>
      <div className="flex flex-wrap gap-6">
        <div>
          <p className="mb-2 text-xs text-cyan-200/45">Trading mode</p>
          <div className="inline-flex rounded-lg border border-cyan-500/20 bg-black/20 p-0.5">
            {(['PAPER', 'LIVE'] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                disabled={setMode.isPending}
                onClick={() => setMode.mutate(mode)}
                className={cn(
                  'rounded-md px-4 py-1.5 text-sm transition',
                  tradingMode === mode
                    ? 'bg-cyan-400 font-medium text-slate-950 shadow-[0_0_12px_rgba(34,211,238,0.4)]'
                    : 'text-slate-400 hover:text-cyan-100',
                )}
              >
                {mode === 'PAPER' ? 'Paper' : 'Live'}
              </button>
            ))}
          </div>
        </div>
        <div>
          <p className="mb-2 text-xs text-cyan-200/45">Lots per trade</p>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="rounded border border-cyan-500/25 px-2 py-1 text-cyan-100/80 hover:border-cyan-400/40"
              onClick={() => adjustLots.mutate(-1)}
            >
              −
            </button>
            <strong className="min-w-[2rem] text-center text-lg">{lotsPerTrade}</strong>
            <button
              type="button"
              className="rounded border border-cyan-500/25 px-2 py-1 text-cyan-100/80 hover:border-cyan-400/40"
              onClick={() => adjustLots.mutate(1)}
            >
              +
            </button>
          </div>
          {orderQuantities ? (
            <p className="mt-1 text-xs text-cyan-200/40">
              NIFTY {orderQuantities.NIFTY ?? '—'} qty · BANKNIFTY{' '}
              {orderQuantities.BANKNIFTY ?? '—'} qty
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-4 text-sm">
          <div>
            <span className="block text-xs text-cyan-200/45">{journalLabel} open</span>
            <strong>{journalSummary?.open ?? 0}</strong>
          </div>
          <div>
            <span className="block text-xs text-cyan-200/45">{journalLabel} realized</span>
            <strong>{money(journalSummary?.realized_pnl)}</strong>
          </div>
          <div>
            <span className="block text-xs text-cyan-200/45">{journalLabel} MTM</span>
            <strong>{money(journalSummary?.open_mtm)}</strong>
          </div>
        </div>
      </div>
      {gates.length > 0 ? (
        <ul className="mt-4 space-y-1 text-xs">
          {gates.map((g, i) => (
            <li
              key={i}
              className={cn(
                'rounded border px-2 py-1',
                g.ok
                  ? 'border-emerald-500/20 text-emerald-300/90'
                  : 'border-red-500/20 text-red-300/90',
              )}
            >
              {g.title}: {g.detail}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}
