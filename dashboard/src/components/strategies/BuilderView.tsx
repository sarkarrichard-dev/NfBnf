import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { cn } from '../../lib/cn'
import { fx } from '../../lib/theme'
import { STRATEGIES, byId, renderReads, type ParamDef, type ParamGroup } from '../../lib/strategies'
import { Button } from '../ui/Button'

type Values = Record<string, number | boolean | string>

const BUILDABLE = STRATEGIES.filter((s) => s.builder)

const STEPS: { id: ParamGroup; label: string; blurb: string }[] = [
  { id: 'market', label: 'Market', blurb: 'What it watches — instrument frame and the reference lines.' },
  { id: 'signal', label: 'Signal', blurb: 'What triggers an entry.' },
  { id: 'risk', label: 'Risk & size', blurb: 'When it gets out. Stops are % of P&L on margin, not price.' },
]

function Field({ p, value, onChange }: { p: ParamDef; value: number | boolean | string; onChange: (v: number | boolean | string) => void }) {
  if (p.type === 'bool') {
    return (
      <label className="flex items-center justify-between gap-3 py-1.5">
        <span className="text-xs text-slate-300">{p.label}</span>
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
      </label>
    )
  }
  if (p.type === 'select') {
    return (
      <label className="flex items-center justify-between gap-3 py-1.5">
        <span className="text-xs text-slate-300">{p.label}</span>
        <select
          value={String(value)}
          onChange={(e) => onChange(e.target.value)}
          className="rounded-lg border border-[var(--hair)] bg-black/30 px-2 py-1 font-mono text-xs text-slate-100"
        >
          {(p.options ?? []).map((o) => (
            <option key={o} value={o}>{o}</option>
          ))}
        </select>
      </label>
    )
  }
  const num = Number(value)
  return (
    <div className="py-1.5">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-slate-300">{p.label}</span>
        <input
          type="number"
          value={num}
          min={p.min}
          max={p.max}
          step={p.step ?? (p.type === 'int' ? 1 : 0.1)}
          onChange={(e) => onChange(p.type === 'int' ? Math.round(+e.target.value) : +e.target.value)}
          className="w-20 rounded-lg border border-[var(--hair)] bg-black/30 px-2 py-1 text-right font-mono text-xs tabular-nums text-slate-100"
        />
      </div>
      {p.min != null && p.max != null ? (
        <input
          type="range"
          value={num}
          min={p.min}
          max={p.max}
          step={p.step ?? (p.type === 'int' ? 1 : 0.1)}
          onChange={(e) => onChange(p.type === 'int' ? Math.round(+e.target.value) : +e.target.value)}
          className="mt-1 w-full accent-[var(--acc)]"
        />
      ) : null}
      {p.hint ? <p className="mt-0.5 text-[10.5px] text-slate-600">{p.hint}</p> : null}
    </div>
  )
}

export function BuilderView({ seedId }: { seedId: string }) {
  const [baseId, setBaseId] = useState(byId(seedId)?.builder ? seedId : BUILDABLE[0].id)
  const base = byId(baseId) ?? BUILDABLE[0]

  const [values, setValues] = useState<Values>(() =>
    Object.fromEntries(base.params.map((p) => [p.key, p.default])),
  )

  // reset the form when the base template changes
  const [lastBase, setLastBase] = useState(baseId)
  if (lastBase !== baseId) {
    setLastBase(baseId)
    setValues(Object.fromEntries(base.params.map((p) => [p.key, p.default])))
  }

  const set = (k: string, v: number | boolean | string) => setValues((prev) => ({ ...prev, [k]: v }))

  const issues = useMemo(() => {
    const out: string[] = []
    for (const p of base.params) {
      const v = Number(values[p.key])
      if (p.type !== 'bool' && p.type !== 'select') {
        if (p.min != null && v < p.min) out.push(`${p.label} below ${p.min}`)
        if (p.max != null && v > p.max) out.push(`${p.label} above ${p.max}`)
      }
    }
    if (base.id === 'candle_renko' && Number(values.st_period) < Number(values.atr_len) / 3)
      out.push('Supertrend period looks short vs ATR — expect chop')
    if (Number(values.ratchet_step_pnl_pct) > Number(values.stop_pnl_pct))
      out.push('Ratchet step wider than the initial stop — it will never ratchet')
    return out
  }, [base, values])

  const changed = base.params.filter((p) => values[p.key] !== p.default)

  const config = {
    base: base.id,
    params: Object.fromEntries(changed.map((p) => [p.key, values[p.key]])),
  }

  return (
    <div className="space-y-4">
      <section className={cn(fx.panel, 'p-4 space-y-3')}>
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-xs text-slate-400">Base template</label>
          <select
            value={baseId}
            onChange={(e) => setBaseId(e.target.value)}
            className="rounded-lg border border-[var(--hair)] bg-black/30 px-2 py-1 text-xs text-slate-100"
          >
            {BUILDABLE.map((s) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
          <span className="font-mono text-[10.5px] uppercase tracking-wide text-slate-500">
            {base.instrument} · {base.timeframe}
          </span>
        </div>

        <div className="rounded-lg border border-[var(--hair-soft)] bg-white/[0.015] p-3">
          <p className={fx.cardLabel}>Reads</p>
          <p className="mt-1 text-xs leading-relaxed text-slate-200">{renderReads(base.reads, values)}</p>
        </div>
      </section>

      <div className="grid gap-4 lg:grid-cols-3">
        {STEPS.map((step) => {
          const rows = base.params.filter((p) => p.group === step.id)
          return (
            <section key={step.id} className={cn(fx.panel, 'p-4')}>
              <h3 className="text-sm font-bold text-slate-100">{step.label}</h3>
              <p className="mt-0.5 mb-2 text-[10.5px] text-slate-600">{step.blurb}</p>
              {rows.length ? (
                <div className="divide-y divide-[var(--hair-soft)]">
                  {rows.map((p) => (
                    <Field key={p.key} p={p} value={values[p.key]} onChange={(v) => set(p.key, v)} />
                  ))}
                </div>
              ) : (
                <p className="text-[11px] text-slate-600">Nothing to configure for this template.</p>
              )}
            </section>
          )
        })}
      </div>

      <section
        className={cn(
          'rounded-xl border p-3 text-xs',
          issues.length
            ? 'border-[var(--warn)]/40 bg-[var(--warn)]/10 text-[var(--warn)]'
            : 'border-[var(--up)]/30 bg-[var(--up)]/10 text-[var(--up)]',
        )}
      >
        {issues.length ? (
          <ul className="space-y-0.5">
            {issues.map((i) => (
              <li key={i}>• {i}</li>
            ))}
          </ul>
        ) : (
          <span>Valid — {changed.length} parameter{changed.length === 1 ? '' : 's'} changed from the {base.name} defaults.</span>
        )}
      </section>

      <section className={cn(fx.panel, 'p-4 space-y-3')}>
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-bold text-slate-100">Config preview</h3>
          <Button
            variant="primary"
            disabled={issues.length > 0}
            onClick={() => toast.info('Saving custom strategies lands in Phase 4 (per-user store). This is the config that would deploy.')}
          >
            Deploy
          </Button>
        </div>
        <pre className="overflow-x-auto rounded-lg border border-[var(--hair-soft)] bg-black/30 p-3 font-mono text-[11px] leading-relaxed text-slate-300">
{JSON.stringify(config, null, 2)}
        </pre>
        <p className="text-[11px] text-slate-600">
          Read-only for now — deploy writes to <span className="text-slate-400">memory/crypto_strategy_params.json</span> and
          the paper scan once the per-user credential store ships.
        </p>
      </section>
    </div>
  )
}
