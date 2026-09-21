import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { Button } from './ui/Button'

export function BacktestPanel() {
  const [instrument, setInstrument] = useState('NIFTY')
  const [days, setDays] = useState(5)
  const [output, setOutput] = useState('No backtest run yet.')
  const [summary, setSummary] = useState('—')

  const cache = useQuery({
    queryKey: ['candle-cache'],
    queryFn: () => api<Record<string, unknown>>('/api/research/candle-cache'),
    staleTime: 30_000,
  })

  const sync = useMutation({
    mutationFn: () => api('/api/research/sync-candle-cache', { method: 'POST' }),
    onSuccess: () => {
      toast.success('Cache synced')
      void cache.refetch()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const run = useMutation({
    mutationFn: (): Promise<Record<string, unknown>> =>
      api<Record<string, unknown>>('/api/research/backtest-dhan', {
        method: 'POST',
        body: JSON.stringify({
          instrument,
          days,
          pnl_mode: 'option_proxy',
          use_cache: true,
        }),
      }),
    onSuccess: (r: Record<string, unknown>) => {
      setSummary(String(r.summary || 'Done'))
      setOutput(JSON.stringify(r, null, 2))
    },
    onError: (e: Error) => setOutput(e.message),
  })

  const cacheText = cache.data
    ? Object.entries((cache.data.instruments as Record<string, Record<string, unknown>>) || {})
        .map(([k, v]) =>
          v?.cached_days
            ? `${k}: ${v.cached_days}d @ ${v.interval_minutes}m`
            : `${k}: no cache`,
        )
        .join(' · ')
    : '—'

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">{cacheText}</p>
      <div className="flex flex-wrap gap-3 text-sm">
        <label className="flex flex-col gap-1 text-xs text-slate-500">
          Index
          <select
            value={instrument}
            onChange={(e) => setInstrument(e.target.value)}
            className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200"
          >
            <option value="NIFTY">NIFTY</option>
            <option value="BANKNIFTY">BANKNIFTY</option>
            <option value="SENSEX">SENSEX</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-500">
          Days
          <input
            type="number"
            min={1}
            max={90}
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="w-20 rounded border border-slate-700 bg-slate-950 px-2 py-1"
          />
        </label>
        <div className="flex items-end gap-2">
          <Button
            onClick={() => sync.mutate()}
            pending={sync.isPending}
          >
            Sync cache
          </Button>
          <Button
            onClick={() => run.mutate()}
            pending={run.isPending}
          >
            Run backtest
          </Button>
        </div>
      </div>
      <p className="text-sm text-slate-400">{summary}</p>
      <pre className="max-h-64 overflow-auto rounded-md border border-slate-800 bg-slate-950 p-3 text-[11px] text-slate-500">
        {output}
      </pre>
    </div>
  )
}
