/** The strategy catalog.
 *
 *  The set of strategies is fixed and small (three crypto engines + the two
 *  index lanes), so it lives here rather than behind an endpoint. Each entry
 *  mirrors the frozen config dataclass in `crypto/strategies/*.py` /
 *  `index_ai/` so the Builder can render a parameter form and the "READS"
 *  sentence without a round-trip. Live status is layered on at render time
 *  from `/api/{futures,crypto}/status` (index options report via Trade History).
 *
 *  When Phase 4 adds per-user custom strategies, this becomes the seed list
 *  and saved permutations come from the user store.
 */

export type ParamGroup = 'market' | 'signal' | 'risk'

export type ParamDef = {
  key: string
  label: string
  group: ParamGroup
  type: 'int' | 'float' | 'bool' | 'select'
  default: number | boolean | string
  min?: number
  max?: number
  step?: number
  options?: string[]
  hint?: string
}

export type StrategyDef = {
  id: string
  name: string
  kind: 'crypto' | 'index'
  /** which lane / status block reports it */
  statusKey: 'ny_n_break' | 'ichimoku' | 'ak_roxx_pro' | 'index_options' | 'futures'
  engine: string
  instrument: string
  timeframe: string
  blurb: string
  /** buy vs sell nature — the only mechanic shown when internals are redacted */
  side: 'buying' | 'selling' | 'mixed'
  /** one deliberately vague line shown in place of blurb/engine/params when
   *  HIDE_STRATEGY_INTERNALS is on (shared team view) */
  teaser: string
  /** plain-English template; {tokens} are param keys */
  reads: string
  /** backtest headline, already measured — see crypto/strategies/RESULTS.md */
  backtest?: { window: string; net: string; trades: number; note?: string }
  /** default-on in the paper lane? */
  paperDefault: boolean
  /** exposed in the Builder (index lanes tune via .env, not here) */
  builder: boolean
  params: ParamDef[]
}

const TRAIL: ParamDef[] = [
  { key: 'stop_pnl_pct', label: 'Initial stop', group: 'risk', type: 'float', default: 10, min: 2, max: 40, step: 1, hint: '% of P&L on margin — price-based stops are meaningless at 100×' },
  { key: 'ratchet_step_pnl_pct', label: 'Ratchet step', group: 'risk', type: 'float', default: 5, min: 1, max: 20, step: 1, hint: 'every N% of peak P&L lifts the stop by N%' },
  { key: 'tp_trigger_pnl_pct', label: 'Profit-floor trigger', group: 'risk', type: 'float', default: 25, min: 5, max: 100, step: 5 },
  { key: 'peak_trail_pnl_pct', label: 'Peak trail', group: 'risk', type: 'float', default: 2, min: 1, max: 10, step: 1 },
]

export const STRATEGIES: StrategyDef[] = [
  {
    id: 'ny_n_break',
    name: 'NY N-Break',
    kind: 'crypto',
    statusKey: 'ny_n_break',
    engine: 'EMA25 + VWAP bias · first-swing re-break',
    instrument: 'BTC / ETH perp',
    timeframe: '5m entry · 15m exit',
    side: 'buying',
    teaser: 'Intraday momentum entries on BTC/ETH perps with a trailing exit.',
    blurb:
      'The "6 PM" strategy, now around the clock. Bias needs price on one side of both EMA25 and VWAP; entry is a closing-basis re-break of the first swing after a retrace — the "N" shape. Exit watches the 15m structure or the P&L trail. Still trades the NY hours as before; the window gate is off by default (CRYPTO_NBREAK_ALLROUND).',
    reads:
      'In the NY session, when price closes above both EMA{ema_len} and VWAP, arm on the first swing high; enter long when it re-breaks. Up to {max_trades_per_session} trades/session. Trail the stop {stop_pnl_pct}% on margin.',
    backtest: { window: 'paper since 2026-09', net: 'paper lane', trades: 0, note: 'live paper — no backtest replay (session-scoped)' },
    paperDefault: true,
    builder: true,
    params: [
      { key: 'ema_len', label: 'EMA length', group: 'market', type: 'int', default: 25, min: 8, max: 100 },
      { key: 'swing_left', label: 'Swing left bars', group: 'signal', type: 'int', default: 5, min: 2, max: 15 },
      { key: 'swing_right', label: 'Swing right bars', group: 'signal', type: 'int', default: 2, min: 1, max: 10 },
      { key: 'exit_swing_left', label: 'Exit swing left', group: 'signal', type: 'int', default: 5, min: 2, max: 15 },
      { key: 'exit_swing_right', label: 'Exit swing right', group: 'signal', type: 'int', default: 2, min: 1, max: 10 },
      { key: 'max_trades_per_session', label: 'Max trades / session', group: 'risk', type: 'int', default: 3, min: 1, max: 10 },
      ...TRAIL,
    ],
  },
  {
    id: 'ichimoku',
    name: 'Ichimoku Cloud',
    kind: 'crypto',
    statusKey: 'ichimoku',
    engine: 'Tenkan/Kijun cross · cloud filter',
    instrument: 'BTC / ETH perp',
    timeframe: '15m',
    side: 'buying',
    teaser: 'Trend-following entries on BTC/ETH perps with a trailing exit.',
    blurb:
      'Classic Ichimoku trend-following. Long when the conversion line crosses above the base line with price above the cloud; mirrored for shorts. The shared P&L trailing engine handles the exit.',
    reads:
      'On the 15m frame, go long when Tenkan({conversion}) crosses above Kijun({base}) and price is above the {span_b}-period cloud. Trail the stop {stop_pnl_pct}% on margin.',
    backtest: { window: '2026 candle history', net: 'dormant', trades: 0, note: 'kept as an indicator lane; not enabled live' },
    paperDefault: false,
    builder: true,
    params: [
      { key: 'conversion', label: 'Conversion (Tenkan)', group: 'signal', type: 'int', default: 9, min: 5, max: 30 },
      { key: 'base', label: 'Base (Kijun)', group: 'signal', type: 'int', default: 26, min: 10, max: 60 },
      { key: 'span_b', label: 'Span B', group: 'signal', type: 'int', default: 52, min: 20, max: 120 },
      { key: 'displacement', label: 'Displacement', group: 'market', type: 'int', default: 26, min: 10, max: 60 },
      ...TRAIL,
    ],
  },
  {
    id: 'ak_roxx_pro',
    name: 'AK Roxx Pro',
    kind: 'crypto',
    statusKey: 'ak_roxx_pro',
    engine: '5m · SMA channel + EMA + PEMA ribbon + 1H CPR',
    instrument: 'BTC / ETH perp',
    timeframe: '5m',
    side: 'buying',
    teaser: 'Confluence trend entries on BTC/ETH perps; exits on the channel break.',
    blurb:
      'A faithful port of the "AK Roxx" TradingView indicator (Alpha 1), verified line-for-line against the portal\'s own signal engine. Enters on the first bar eight reads line up: both SMA(8) channel bands rising, close above the upper band and the prior close, EMA7 > EMA14 with both rising, close beyond the previous hour\'s CPR, and the 13/21/34 PEMA ribbon stacked and sloping. Exits — the whole exit — on the first bar that closes back below SMA(8) of the lows (a short exits above SMA(8) of the highs). No target, no fixed stop. An optional gate requires the portal\'s "Alpha 2" (15-bar break + Choppiness + Supertrend) to agree.',
    reads:
      'Long when SMA({upper_len}) highs and SMA({lower_len}) lows are both rising, close is above the upper band and the prior close, EMA{ema_short} > EMA{ema_long} (both rising), the {pema_fast}/{pema_mid}/{pema_slow} PEMA ribbon is stacked & sloping up, and close is above the 1H CPR. Exit the first bar close falls back below SMA({lower_len}) of the lows.',
    backtest: { window: 'pending re-backtest', net: 'paper lane', trades: 0, note: 'faithful port verified 2026-09-11; earlier backtests were on wrong exit logic — void' },
    paperDefault: true,
    builder: true,
    params: [
      { key: 'upper_len', label: 'Channel — SMA of highs', group: 'market', type: 'int', default: 8, min: 4, max: 30 },
      { key: 'lower_len', label: 'Channel — SMA of lows', group: 'market', type: 'int', default: 8, min: 4, max: 30 },
      { key: 'ema_short', label: 'Fast EMA', group: 'signal', type: 'int', default: 7, min: 3, max: 20 },
      { key: 'ema_long', label: 'Slow EMA', group: 'signal', type: 'int', default: 14, min: 8, max: 40 },
      { key: 'pema_fast', label: 'PEMA fast', group: 'signal', type: 'int', default: 13, min: 5, max: 30 },
      { key: 'pema_mid', label: 'PEMA mid', group: 'signal', type: 'int', default: 21, min: 10, max: 50 },
      { key: 'pema_slow', label: 'PEMA slow', group: 'signal', type: 'int', default: 34, min: 20, max: 90 },
      { key: 'slope_lookback', label: 'Slope lookback (bars)', group: 'signal', type: 'int', default: 1, min: 1, max: 5 },
      { key: 'require_beyond_cpr', label: 'Require beyond 1H CPR', group: 'signal', type: 'bool', default: true },
      { key: 'require_alpha2_agree', label: 'Require Alpha 2 to agree', group: 'signal', type: 'bool', default: false },
      ...TRAIL,
    ],
  },
  {
    id: 'index_options',
    name: 'Index Options',
    kind: 'index',
    statusKey: 'index_options',
    engine: 'CPR + EMA + Supertrend + OI · premium-trail exits',
    instrument: 'NIFTY · BANKNIFTY · SENSEX',
    timeframe: 'buy fast · sell 5m setup + 15m trend',
    side: 'mixed',
    teaser: 'Directional index-options entries on NIFTY / BANKNIFTY / SENSEX — both buying and selling — routed through the live executor. P&L in Trade History.',
    blurb:
      'The live index-options engine — naked single-leg buying (60% confidence, OI + liquidity gated) and directional credit selling (5m setup, 15m trend/S&R, premium-trail owns the exit). Chop brakes and the per-index viability cost floor gate the sell lane; BANKNIFTY 4-leg structures stay blocked. Runs through the executor → Trade History.',
    reads: 'Tuned through Settings → Strategy tuning (.env), not the Builder. P&L is in Trade History / Reports (source: index).',
    backtest: { window: 'full history', net: 'net-negative after costs', trades: 0, note: 'friction is the binding constraint — see strategy-findings' },
    paperDefault: true,
    builder: false,
    params: [],
  },
  {
    id: 'futures',
    name: 'Index Futures',
    kind: 'index',
    statusKey: 'futures',
    engine: 'CPR + EMA + Supertrend',
    instrument: 'NIFTY · BANKNIFTY · SENSEX',
    timeframe: '5m',
    side: 'buying',
    teaser: 'Directional index-futures entries on NIFTY / BANKNIFTY / SENSEX. Paper only.',
    blurb:
      'The index futures paper lane — the same directional signal as the options lane, expressed as an outright futures position. Paper only.',
    reads: 'Tuned through Settings → Strategy tuning (.env), not the Builder.',
    paperDefault: true,
    builder: false,
    params: [],
  },
]

export const byId = (id: string) => STRATEGIES.find((s) => s.id === id)

/** The one mechanic left visible when internals are redacted. */
export function sideLabel(def: StrategyDef): string {
  if (def.kind === 'index') {
    return def.side === 'selling'
      ? 'Option selling'
      : def.side === 'mixed'
        ? 'Option buying & selling'
        : 'Option buying'
  }
  return 'Directional (long / short)'
}

/** Fill {tokens} in a `reads` template from a param-value map. */
export function renderReads(tpl: string, values: Record<string, number | boolean | string>): string {
  return tpl.replace(/\{(\w+)\}/g, (_, k) => String(values[k] ?? `{${k}}`))
}
