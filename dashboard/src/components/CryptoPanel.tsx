import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { cn } from '../lib/cn'
import { fx } from '../lib/theme'
import { inr, num, ok, pnlCls, usd } from '../lib/cryptoFmt'
import { Button } from './ui/Button'
import { CollapsibleSection } from './CollapsibleSection'
import { Sparkline } from './Sparkline'
import { CryptoSetupPanel } from './CryptoSetupPanel'
import { CryptoExecutionPanel } from './CryptoExecutionPanel'

type Status = {
  paper_enabled: boolean
  lanes: { ny_n_break: boolean; ichimoku: boolean }
  sizing: { deploy_usd: number; leverage: number; max_concurrent: number }
  session_ist: { start: string; end: string }
  ichimoku_tf: string
  symbols: string[]
  available_symbols: string[]
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

const blank: DaySum = { trades: 0, wins: 0, losses: 0, net_usd: 0, net_inr: 0 }

/** Cumulative realised USD P&L, oldest → newest, for the equity sparkline. */
function equityCurve(trades: Trade[]): number[] {
  let running = 0
  return trades.map((t) => (running += Number(t.pnl_usd) || 0))
}

function StatTile({
  label,
  value,
  sub,
  valueCls,
  points,
}: {
  label: string
  value: string
  sub?: string
  valueCls?: string
  points?: number[]
}) {
  return (
    <article className={fx.card}>
      <p className={fx.cardLabel}>{label}</p>
      <p className={cn(fx.cardValue, valueCls)}>{value}</p>
      {sub ? <p className="mt-0.5 text-[11px] text-slate-500 tabular-nums">{sub}</p> : null}
      {points && points.length > 1 ? (
        <Sparkline points={points} className="mt-1 w-full" height={16} />
      ) : null}
    </article>
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
  const equity = equityCurve(trades)
  const picked = new Set(s?.symbols ?? [])
  // union: every perp Delta lists, plus any already-picked symbol whose
  // contract cache hasn't refreshed yet
  const chipSymbols = [...new Set([...(s?.available_symbols ?? []), ...(s?.symbols ?? [])])].sort()
  const inputCls =
    'w-24 rounded-lg border border-[var(--hair)] bg-black/30 px-2 py-1 font-mono text-xs ' +
    'text-slate-100 outline-none focus:border-[var(--acc)]'

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">
        Delta Exchange India · perpetual futures. Paper by default; the Mode switch below arms
        real orders.
      </p>

      <CryptoExecutionPanel />

      {/* P&L summary */}
      <div className="grid gap-2 sm:grid-cols-3">
        <StatTile
          label="Open (unrealised)"
          value={usd(positions.data?.open_unrealized_usd ?? 0)}
          valueCls={pnlCls(positions.data?.open_unrealized_usd ?? 0)}
          sub={`${inr(positions.data?.open_unrealized_inr ?? 0)} · ${positions.data?.paper?.length ?? 0} position${
            (positions.data?.paper?.length ?? 0) === 1 ? '' : 's'
          }`}
        />
        <StatTile
          label={`6 PM · ${day.data?.ny_session_date ?? ''}`}
          value={usd((day.data?.ny_n_break ?? blank).net_usd)}
          valueCls={pnlCls((day.data?.ny_n_break ?? blank).net_usd)}
          sub={`${inr((day.data?.ny_n_break ?? blank).net_inr)} · ${(day.data?.ny_n_break ?? blank).trades} trades · ${
            (day.data?.ny_n_break ?? blank).wins
          }W/${(day.data?.ny_n_break ?? blank).losses}L`}
        />
        <StatTile
          label={`Ichimoku · ${day.data?.utc_date ?? ''}`}
          value={usd((day.data?.ichimoku ?? blank).net_usd)}
          valueCls={pnlCls((day.data?.ichimoku ?? blank).net_usd)}
          sub={`${inr((day.data?.ichimoku ?? blank).net_inr)} · ${(day.data?.ichimoku ?? blank).trades} trades · ${
            (day.data?.ichimoku ?? blank).wins
          }W/${(day.data?.ichimoku ?? blank).losses}L`}
          points={equity}
        />
      </div>

      {/* controls */}
      <div className={cn(fx.panel, 'space-y-3 p-4')}>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold text-cyan-50/90">Paper lane</span>
          <Button
            variant={s?.paper_enabled ? 'secondary' : 'primary'}
            onClick={() => cfg.mutate({ paper_enabled: !s?.paper_enabled })}
          >
            {s?.paper_enabled ? 'On — turn off' : 'Off — turn on'}
          </Button>
          <Chip
            label="6 PM (NY N-Break)"
            on={!!s?.lanes.ny_n_break}
            onClick={() => cfg.mutate({ ny_n_break_enabled: !s?.lanes.ny_n_break })}
          />
          <Chip
            label="Ichimoku"
            on={!!s?.lanes.ichimoku}
            onClick={() => cfg.mutate({ ichimoku_enabled: !s?.lanes.ichimoku })}
          />
        </div>

        {/* symbol picker */}
        <div className="space-y-1.5">
          <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
            Trade symbols
          </span>
          <div className="flex flex-wrap gap-2">
            {chipSymbols.map((sym) => {
              const on = picked.has(sym)
              return (
                <Chip
                  key={sym}
                  label={sym}
                  on={on}
                  showState={false}
                  onClick={() => {
                    const next = on ? [...picked].filter((x) => x !== sym) : [...picked, sym]
                    if (!next.length) return toast.error('Keep at least one symbol')
                    cfg.mutate({ symbols: next })
                  }}
                />
              )
            })}
          </div>
          {(s?.symbols?.length ?? 0) > 4 ? (
            <p className="text-[11px] text-[var(--warn)]/80">
              {s?.symbols.length} symbols × ~5 Delta calls per 60s scan — within Delta's quota,
              but trims the scan headroom.
            </p>
          ) : null}
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
              {sym}{' '}
              {v.measured != null
                ? `${v.measured.toFixed(2)} bps (measured)`
                : `${v.fallback ?? '?'} bps (est.)`}
            </span>
          ))}
        </p>
      </div>

      {/* open positions */}
      <section className={cn(fx.panel, 'p-4')}>
        <h3 className="mb-3 text-sm font-semibold text-cyan-50/95">Open (paper)</h3>
        {positions.data?.paper?.length ? (
          <div className="overflow-x-auto rounded-lg border border-[var(--hair)] bg-black/25">
            <table className="min-w-full text-xs">
              <thead className="text-cyan-200/50">
                <tr className="border-b border-slate-800 [&>th]:px-3 [&>th]:py-2 [&>th]:text-left [&>th]:font-medium">
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
                  <tr
                    key={p.key}
                    className="border-b border-slate-800/60 text-slate-200 [&>td]:px-3 [&>td]:py-2"
                  >
                    <td>{p.asset}</td>
                    <td className="text-slate-400">{p.strategy}</td>
                    <td className={p.side === 'long' ? 'text-[var(--up)]' : 'text-[var(--down)]'}>
                      {p.side}
                    </td>
                    <td className="tabular-nums">{p.size}</td>
                    <td className="text-slate-400 tabular-nums">
                      ${num(p.entry_price)} → ${p.mark ? num(p.mark) : '—'}
                    </td>
                    <td className={cn('tabular-nums', pnlCls(p.unrealized_usd))}>
                      {usd(p.unrealized_usd)}{' '}
                      <span className="text-slate-600">{inr(p.unrealized_inr)}</span>
                      {ok(p.unrealized_pct) && p.unrealized_pct !== 0 ? (
                        <span className="text-slate-600">
                          {' '}
                          ({p.unrealized_pct > 0 ? '+' : ''}
                          {p.unrealized_pct}%)
                        </span>
                      ) : null}
                    </td>
                    <td className="text-slate-400 tabular-nums">
                      ${num(p.margin_total_usd)} · {num(p.leverage, 0)}x
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-cyan-200/45">No open paper positions.</p>
        )}
      </section>

      {/* journal */}
      <section className={cn(fx.panel, 'p-4')}>
        <h3 className="mb-3 text-sm font-semibold text-cyan-50/95">Closed trades</h3>
        {trades.length ? (
          <div className="max-h-[28rem] overflow-auto rounded-lg border border-[var(--hair)] bg-black/25">
            <table className="min-w-full text-xs">
              <thead className="sticky top-0 z-10 bg-slate-950/95 text-cyan-200/50">
                <tr className="border-b border-slate-800 [&>th]:px-3 [&>th]:py-2 [&>th]:text-left [&>th]:font-medium">
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
                  <tr
                    key={i}
                    className="border-b border-slate-800/60 text-slate-200 [&>td]:px-3 [&>td]:py-2"
                  >
                    <td className="text-slate-500">{t.day}</td>
                    <td>{t.asset}</td>
                    <td className="text-slate-400">{t.strategy}</td>
                    <td className={t.side === 'long' ? 'text-[var(--up)]' : 'text-[var(--down)]'}>
                      {t.side}
                    </td>
                    <td className="text-slate-400 tabular-nums">
                      ${num(t.entry_price)} → ${num(t.exit_price)}
                    </td>
                    <td className={cn('tabular-nums', pnlCls(t.pnl_usd))}>
                      {usd(t.pnl_usd)} <span className="text-slate-600">{inr(t.pnl_inr)}</span>
                    </td>
                    <td className="text-slate-500">{t.exit_reason ?? ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-cyan-200/45">No closed trades yet.</p>
        )}
      </section>

      <CollapsibleSection title="Delta connection" summary="keys · wallet · live quotes">
        <CryptoSetupPanel />
      </CollapsibleSection>
    </div>
  )
}

function Chip({
  label,
  on,
  onClick,
  showState = true,
}: {
  label: string
  on: boolean
  onClick: () => void
  showState?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-full border px-3 py-1 text-xs font-medium transition',
        on
          ? 'border-[var(--acc)]/40 bg-[var(--acc)]/15 text-[var(--acc)]'
          : 'border-[var(--hair)] bg-white/[0.03] text-slate-400 hover:text-slate-200',
      )}
    >
      {label}
      {showState ? `: ${on ? 'on' : 'off'}` : ''}
    </button>
  )
}
