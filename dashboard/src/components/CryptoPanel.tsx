import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { cn } from '../lib/cn'
import { fx } from '../lib/theme'
import { inr, inr0, num, ok, pnlCls, usd, usd0 } from '../lib/cryptoFmt'
import { Button } from './ui/Button'
import { CollapsibleSection } from './CollapsibleSection'
import { Sparkline } from './Sparkline'
import { CryptoSetupPanel } from './CryptoSetupPanel'
import { CryptoExecutionPanel } from './CryptoExecutionPanel'

type Status = {
  paper_enabled: boolean
  lanes: { ny_n_break: boolean; ichimoku: boolean }
  sizing: { lots: number; deploy_cap_usd: number; leverage: number; max_concurrent: number }
  trailing: {
    stop_pnl_pct: number
    ratchet_step_pnl_pct: number
    tp_trigger_pnl_pct: number
    peak_trail_pnl_pct: number
  }
  session_ist: { start: string; end: string }
  ichimoku_tf: string
  symbols: string[]
  available_symbols: string[]
  half_spread_bps: Record<string, { measured: number | null; fallback: number | null }>
  ml: {
    enabled: boolean
    rows: number
    live_rows: number
    gate_armed: boolean
    min_win_prob_gate: number
    oos_delta_usd: number | null
    model_present: boolean
    min_rows: number
    min_live_rows: number
  }
}
type LotRow = {
  symbol: string
  coin_per_lot: number | null
  notional_per_lot_usd: number | null
  margin_per_lot_usd: number | null
  margin_per_lot_inr: number | null
  source?: string
  note?: string
}
type Lots = { lots: number; leverage: number; deploy_cap_usd: number; table: LotRow[] }
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
  const lotsQ = useQuery({
    queryKey: ['crypto', 'lots'],
    queryFn: () => api<Lots>('/api/crypto/lots'),
    refetchInterval: 60_000,
  })

  const s = status.data
  const [cap, setCap] = useState('')

  const cfg = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api('/api/crypto/config', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => {
      toast.success('Saved — restart the server to apply')
      setCap('')
      void qc.invalidateQueries({ queryKey: ['crypto'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const trades = journal.data?.trades ?? []
  const equity = equityCurve(trades)
  // union: every perp Delta lists, plus any already-picked symbol whose
  // contract cache hasn't refreshed yet
  const allSymbols = [...new Set([...(s?.available_symbols ?? []), ...(s?.symbols ?? [])])].sort()
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
          <SymbolSelect
            all={allSymbols}
            picked={s?.symbols ?? []}
            pending={cfg.isPending}
            onApply={(next) => cfg.mutate({ symbols: next })}
          />
          {(s?.symbols?.length ?? 0) > 4 ? (
            <p className="text-[11px] text-[var(--warn)]/80">
              {s?.symbols.length} symbols × ~5 Delta calls per 60s scan — within Delta's quota,
              but trims the scan headroom.
            </p>
          ) : null}
        </div>

        {/* exit rule — read-only */}
        {s?.trailing ? (
          <p className="text-[11px] text-slate-500">
            Exit: stop −{s.trailing.stop_pnl_pct}% P&L, ratchets +
            {s.trailing.ratchet_step_pnl_pct}% for every +{s.trailing.ratchet_step_pnl_pct}% gained ·
            trailing profit from +{s.trailing.tp_trigger_pnl_pct}%, then floor tracks peak −
            {s.trailing.peak_trail_pnl_pct}% · window {s?.session_ist.start}–{s?.session_ist.end} IST
          </p>
        ) : null}

        {/* minimum capital per instrument */}
        <div className="space-y-1.5">
          <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
            Minimum capital per instrument · {s?.sizing.lots ?? 1} lot
            {(s?.sizing.lots ?? 1) === 1 ? '' : 's'} @ 100x
          </span>
          <LotTable rows={lotsQ.data?.table ?? []} lots={lotsQ.data?.lots ?? s?.sizing.lots ?? 1} />
        </div>

        {/* optional margin cap */}
        <label className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
          Margin cap $ / trade (blank = off, currently{' '}
          {s?.sizing.deploy_cap_usd ? `$${num(s.sizing.deploy_cap_usd, 0)}` : 'off'})
          <input
            className={inputCls}
            inputMode="decimal"
            placeholder="none"
            value={cap}
            onChange={(e) => setCap(e.target.value)}
          />
          <Button
            variant="secondary"
            pending={cfg.isPending}
            disabled={!cap}
            onClick={() => cfg.mutate({ deploy_cap_usd: Number(cap) })}
          >
            Set
          </Button>
        </label>

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

      {s?.ml ? <LearningRow ml={s.ml} /> : null}

      <CollapsibleSection title="Delta connection" summary="keys · wallet · live quotes">
        <CryptoSetupPanel />
      </CollapsibleSection>
    </div>
  )
}

function Chip({ label, on, onClick }: { label: string; on: boolean; onClick: () => void }) {
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
      {label}: {on ? 'on' : 'off'}
    </button>
  )
}

/** Dropdown checkbox multi-select for the tradable perps. Local draft until Apply. */
function SymbolSelect({
  all,
  picked,
  pending,
  onApply,
}: {
  all: string[]
  picked: string[]
  pending: boolean
  onApply: (next: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<string[]>(picked)
  // resync the draft whenever the popover opens or the saved set changes
  const key = picked.join(',')
  const [seen, setSeen] = useState(key)
  if (seen !== key) {
    setSeen(key)
    setDraft(picked)
  }
  const dirty = [...draft].sort().join(',') !== [...picked].sort().join(',')
  const toggle = (sym: string) =>
    setDraft((d) => (d.includes(sym) ? d.filter((x) => x !== sym) : [...d, sym]))

  return (
    <details
      open={open}
      onToggle={(e) => {
        setOpen(e.currentTarget.open)
        if (e.currentTarget.open) setDraft(picked)
      }}
      className="relative inline-block"
    >
      <summary className="flex w-72 max-w-full cursor-pointer list-none items-center justify-between gap-2 rounded-lg border border-[var(--hair)] bg-black/30 px-3 py-1.5 text-xs text-slate-100 [&::-webkit-details-marker]:hidden">
        <span className="truncate">
          {picked.length} symbol{picked.length === 1 ? '' : 's'}: {picked.join(', ') || '—'}
        </span>
        <span className="text-slate-500">▾</span>
      </summary>
      <div className="absolute z-20 mt-1 w-72 max-w-full rounded-lg border border-[var(--hair)] bg-[var(--panel)] p-2 shadow-lg">
        <div className="max-h-56 space-y-0.5 overflow-auto">
          {all.map((sym) => (
            <label
              key={sym}
              className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs text-slate-200 hover:bg-white/[0.04]"
            >
              <input
                type="checkbox"
                className="accent-[var(--acc)]"
                checked={draft.includes(sym)}
                onChange={() => toggle(sym)}
              />
              {sym}
            </label>
          ))}
        </div>
        <div className="mt-2 flex items-center justify-between gap-2 border-t border-[var(--hair)] pt-2">
          <span className="text-[11px] text-slate-500">{draft.length} selected</span>
          <Button
            variant="secondary"
            pending={pending}
            disabled={!dirty || !draft.length}
            onClick={() => {
              onApply(draft)
              setOpen(false)
            }}
          >
            {draft.length ? 'Apply' : 'Pick at least one'}
          </Button>
        </div>
      </div>
    </details>
  )
}

function LearningRow({ ml }: { ml: NonNullable<Status['ml']> }) {
  const state = ml.gate_armed
    ? `gate armed @ ${(ml.min_win_prob_gate * 100).toFixed(0)}%`
    : ml.model_present
      ? 'model trained, gate not armed'
      : `collecting data — ${ml.rows}/${ml.min_rows} trades (${ml.live_rows}/${ml.min_live_rows} live)`
  return (
    <div className={cn(fx.card, 'flex flex-wrap items-center gap-x-4 gap-y-1 text-xs')}>
      <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
        Crypto learning
      </span>
      <span className="text-slate-300">{state}</span>
      <span className="text-slate-600">
        {ml.rows} trades · {ml.live_rows} live
        {ok(ml.oos_delta_usd) ? ` · OOS Δ ${usd(ml.oos_delta_usd)}` : ''}
        {ml.enabled ? '' : ' · gate off'}
      </span>
      <span className="text-slate-600">separate from the index model</span>
    </div>
  )
}

function LotTable({ rows, lots }: { rows: LotRow[]; lots: number }) {
  if (!rows.length) return null
  const usdInr = (() => {
    const r = rows.find((x) => ok(x.margin_per_lot_usd) && ok(x.margin_per_lot_inr) && x.margin_per_lot_usd)
    return r ? (r.margin_per_lot_inr as number) / (r.margin_per_lot_usd as number) : 0
  })()
  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--hair)] bg-black/25">
      <table className="min-w-full text-xs">
        <thead className="text-cyan-200/50">
          <tr className="border-b border-slate-800 [&>th]:px-3 [&>th]:py-2 [&>th]:text-left [&>th]:font-medium">
            <th>Symbol</th>
            <th>1 lot =</th>
            <th>Notional / lot</th>
            <th>Min capital ({lots} lot{lots === 1 ? '' : 's'})</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {rows.map((r) => {
            const minUsd = ok(r.margin_per_lot_usd) ? r.margin_per_lot_usd * lots : null
            return (
              <tr
                key={r.symbol}
                className="border-b border-slate-800/60 text-slate-200 [&>td]:px-3 [&>td]:py-2"
              >
                <td>{r.symbol}</td>
                <td className="tabular-nums text-slate-400">
                  {ok(r.coin_per_lot) ? `${r.coin_per_lot} ${r.symbol.replace(/USD.?$/, '')}` : '—'}
                </td>
                <td className="tabular-nums text-slate-400">{usd0(r.notional_per_lot_usd)}</td>
                <td className="tabular-nums">
                  {ok(minUsd) ? (
                    <>
                      {usd0(minUsd)}{' '}
                      <span className="text-slate-600">
                        {inr0(usdInr ? minUsd * usdInr : null)}
                      </span>
                      {r.source === 'delta' ? (
                        <span className="text-slate-600"> · Delta</span>
                      ) : null}
                    </>
                  ) : (
                    <span className="text-[var(--warn)]/80">— · {r.note || 'no mark'}</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
