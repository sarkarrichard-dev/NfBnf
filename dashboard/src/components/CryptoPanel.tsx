import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { cn } from '../lib/cn'
import { Button } from './ui/Button'
import { CryptoSetupPanel } from './CryptoSetupPanel'

type Status = {
  paper_enabled: boolean
  lanes: { ny_n_break: boolean; ichimoku: boolean }
  sizing: { deploy_usd: number; leverage: number; max_concurrent: number }
  session_ist: { start: string; end: string }
  ichimoku_tf: string
  half_spread_bps: Record<string, { measured: number | null; fallback: number | null }>
}
type DaySum = { trades: number; wins: number; losses: number; net_usd: number; net_inr: number }
type Day = { ny_session_date: string; utc_date: string; ny_n_break: DaySum; ichimoku: DaySum }
type PaperPos = {
  key: string
  asset: string
  strategy: string
  side: string
  size: number
  entry_price: number
  leverage: number
  margin_total_usd: number
  mark: number
  unrealized_usd: number
  unrealized_inr: number
  unrealized_pct: number
}
type Positions = {
  paper: PaperPos[]
  live: unknown[]
  open_unrealized_usd: number
  open_unrealized_inr: number
}
type Trade = {
  day: string
  strategy: string
  asset: string
  side: string
  size: number
  entry_price: number
  exit_price: number
  pnl_usd: number
  pnl_inr: number
  exit_reason?: string
}

const ok = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const num = (v: number | null | undefined, d = 2) =>
  ok(v) ? v.toLocaleString(undefined, { maximumFractionDigits: d }) : '—'
const money = (v: number | null | undefined) =>
  ok(v) ? `${v >= 0 ? '+' : '−'}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}` : '—'
const rupees = (v: number | null | undefined) =>
  ok(v) ? `${v >= 0 ? '+' : '−'}₹${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—'
const pnlCls = (v: number | null | undefined) =>
  ok(v) && v > 0 ? 'text-emerald-300' : ok(v) && v < 0 ? 'text-rose-300' : 'text-slate-400'

function DayCard({ title, s }: { title: string; s: DaySum }) {
  return (
    <div className="rounded-lg border border-slate-800 p-3">
      <p className="text-xs font-semibold text-slate-300">{title}</p>
      <p className={cn('mt-1 font-mono text-lg', pnlCls(s.net_usd))}>{money(s.net_usd)}</p>
      <p className="text-xs text-slate-500">
        {rupees(s.net_inr)} · {s.trades} trades · {s.wins}W/{s.losses}L
      </p>
    </div>
  )
}

export function CryptoPanel() {
  const qc = useQueryClient()
  const status = useQuery({ queryKey: ['crypto', 'status'], queryFn: () => api<Status>('/api/crypto/status') })
  const day = useQuery({
    queryKey: ['crypto', 'day'],
    queryFn: () => api<Day>('/api/crypto/day'),
    refetchInterval: 60_000,
  })
  const positions = useQuery({
    queryKey: ['crypto', 'positions'],
    queryFn: () => api<Positions>('/api/crypto/positions'),
    refetchInterval: 20_000,
  })
  const journal = useQuery({
    queryKey: ['crypto', 'journal'],
    queryFn: () => api<{ trades: Trade[] }>('/api/crypto/journal?limit=50'),
    refetchInterval: 60_000,
  })

  const s = status.data
  const [deploy, setDeploy] = useState('')
  const [lev, setLev] = useState('')

  const cfg = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api('/api/crypto/config', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => {
      toast.success('Saved — restart the server to apply')
      setDeploy('')
      setLev('')
      void qc.invalidateQueries({ queryKey: ['crypto'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const trades = journal.data?.trades ?? []
  const inputCls =
    'w-24 rounded-lg border border-slate-700 bg-slate-950/60 px-2 py-1 font-mono text-xs text-slate-100 outline-none focus:border-slate-500'

  return (
    <div className="space-y-6">
      <p className="text-xs text-slate-500">
        Delta Exchange · BTC / ETH perpetual futures · paper only. Live order placement is a
        separate, not-yet-built phase.
      </p>

      {/* P&L summary */}
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-slate-800 p-3">
          <p className="text-xs font-semibold text-slate-300">Open (unrealised)</p>
          <p className={cn('mt-1 font-mono text-lg', pnlCls(positions.data?.open_unrealized_usd ?? 0))}>
            {money(positions.data?.open_unrealized_usd ?? 0)}
          </p>
          <p className="text-xs text-slate-500">
            {rupees(positions.data?.open_unrealized_inr ?? 0)} · {positions.data?.paper?.length ?? 0} position
            {(positions.data?.paper?.length ?? 0) === 1 ? '' : 's'}
          </p>
        </div>
        <DayCard title={`6 PM · ${day.data?.ny_session_date ?? ''}`} s={day.data?.ny_n_break ?? blank} />
        <DayCard title={`Ichimoku · ${day.data?.utc_date ?? ''}`} s={day.data?.ichimoku ?? blank} />
      </div>

      {/* controls */}
      <div className="space-y-3 rounded-lg border border-slate-800 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold text-slate-300">Paper lane</span>
          <Button
            variant={s?.paper_enabled ? 'secondary' : 'primary'}
            onClick={() => cfg.mutate({ paper_enabled: !s?.paper_enabled })}
          >
            {s?.paper_enabled ? 'On — turn off' : 'Off — turn on'}
          </Button>
          <LaneToggle
            label="6 PM (NY N-Break)"
            on={!!s?.lanes.ny_n_break}
            onClick={() => cfg.mutate({ ny_n_break_enabled: !s?.lanes.ny_n_break })}
          />
          <LaneToggle
            label="Ichimoku"
            on={!!s?.lanes.ichimoku}
            onClick={() => cfg.mutate({ ichimoku_enabled: !s?.lanes.ichimoku })}
          />
        </div>
        <div className="flex flex-wrap items-center gap-4 text-xs text-slate-400">
          <label className="flex items-center gap-2">
            Deploy $ / trade (min 100)
            <input
              className={inputCls}
              inputMode="decimal"
              placeholder={num(s?.sizing.deploy_usd, 0)}
              value={deploy}
              onChange={(e) => setDeploy(e.target.value)}
            />
          </label>
          <label className="flex items-center gap-2">
            Leverage
            <input
              className={inputCls}
              inputMode="decimal"
              placeholder={num(s?.sizing.leverage, 0)}
              value={lev}
              onChange={(e) => setLev(e.target.value)}
            />
          </label>
          <Button
            variant="secondary"
            pending={cfg.isPending}
            disabled={!deploy && !lev}
            onClick={() =>
              cfg.mutate({
                ...(deploy ? { deploy_usd: Number(deploy) } : {}),
                ...(lev ? { leverage: Number(lev) } : {}),
              })
            }
          >
            Save
          </Button>
          <span className="text-slate-600">
            window {s?.session_ist.start}–{s?.session_ist.end} IST · Ichimoku {s?.ichimoku_tf}
          </span>
        </div>
        <p className="text-[11px] text-slate-600">
          spread cost:{' '}
          {Object.entries(s?.half_spread_bps ?? {}).map(([sym, v], i) => (
            <span key={sym}>
              {i > 0 ? ' · ' : ''}
              {sym} {v.measured != null ? `${v.measured.toFixed(2)} bps (measured)` : `${v.fallback ?? '?'} bps (est.)`}
            </span>
          ))}
        </p>
      </div>

      {/* open positions */}
      <div>
        <p className="mb-2 text-xs font-semibold text-slate-300">Open (paper)</p>
        {positions.data?.paper?.length ? (
          <div className="overflow-x-auto rounded-lg border border-slate-800">
            <table className="w-full text-xs">
              <thead className="text-slate-500">
                <tr className="[&>th]:px-3 [&>th]:py-2 [&>th]:text-left">
                  <th>Asset</th>
                  <th>Strategy</th>
                  <th>Side</th>
                  <th>Size</th>
                  <th>Entry → Mark</th>
                  <th>Unrealised P&L</th>
                  <th>Margin</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {positions.data.paper.map((p) => (
                  <tr key={p.key} className="border-t border-slate-800 [&>td]:px-3 [&>td]:py-2">
                    <td>{p.asset}</td>
                    <td className="text-slate-400">{p.strategy}</td>
                    <td className={p.side === 'long' ? 'text-emerald-300' : 'text-rose-300'}>
                      {p.side}
                    </td>
                    <td>{p.size}</td>
                    <td className="text-slate-400">
                      ${num(p.entry_price)} → ${p.mark ? num(p.mark) : '—'}
                    </td>
                    <td className={pnlCls(p.unrealized_usd)}>
                      {money(p.unrealized_usd)} <span className="text-slate-600">{rupees(p.unrealized_inr)}</span>
                      {ok(p.unrealized_pct) && p.unrealized_pct !== 0 ? (
                        <span className="text-slate-600"> ({p.unrealized_pct > 0 ? '+' : ''}{p.unrealized_pct}%)</span>
                      ) : null}
                    </td>
                    <td className="text-slate-400">
                      ${num(p.margin_total_usd)} · {num(p.leverage, 0)}x
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-xs text-slate-600">No open paper positions.</p>
        )}
      </div>

      {/* journal */}
      <div>
        <p className="mb-2 text-xs font-semibold text-slate-300">Closed trades</p>
        {trades.length ? (
          <div className="overflow-x-auto rounded-lg border border-slate-800">
            <table className="w-full text-xs">
              <thead className="text-slate-500">
                <tr className="[&>th]:px-3 [&>th]:py-2 [&>th]:text-left">
                  <th>Day</th>
                  <th>Asset</th>
                  <th>Strategy</th>
                  <th>Side</th>
                  <th>Entry → Exit</th>
                  <th>P&L</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {[...trades].reverse().map((t, i) => (
                  <tr key={i} className="border-t border-slate-800 [&>td]:px-3 [&>td]:py-2">
                    <td className="text-slate-500">{t.day}</td>
                    <td>{t.asset}</td>
                    <td className="text-slate-400">{t.strategy}</td>
                    <td className={t.side === 'long' ? 'text-emerald-300' : 'text-rose-300'}>
                      {t.side}
                    </td>
                    <td className="text-slate-400">
                      ${num(t.entry_price)} → ${num(t.exit_price)}
                    </td>
                    <td className={pnlCls(t.pnl_usd)}>
                      {money(t.pnl_usd)} <span className="text-slate-600">{rupees(t.pnl_inr)}</span>
                    </td>
                    <td className="text-slate-500">{t.exit_reason ?? ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-xs text-slate-600">No closed trades yet.</p>
        )}
      </div>

      <CryptoSetupPanel />
    </div>
  )
}

const blank: DaySum = { trades: 0, wins: 0, losses: 0, net_usd: 0, net_inr: 0 }

function LaneToggle({ label, on, onClick }: { label: string; on: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-full border px-3 py-1 text-xs font-medium transition',
        on
          ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-200'
          : 'border-slate-700 bg-slate-800/40 text-slate-400',
      )}
    >
      {label}: {on ? 'on' : 'off'}
    </button>
  )
}
