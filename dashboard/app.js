const state = {
  lastAnalysis: null,
  lastFindingId: "",
};

const $ = (id) => document.getElementById(id);

function fmt(n) {
  return Number(n || 0).toLocaleString();
}

/** Display DB UTC ISO timestamps in Asia/Kolkata for operator clarity. */
function formatIst(isoLike) {
  if (!isoLike || typeof isoLike !== "string") return isoLike;
  const t = isoLike.trim();
  if (t.length < 10) return isoLike;
  try {
    const normalized = t.includes("T") ? t : t.replace(" ", "T");
    const d = new Date(normalized.endsWith("Z") || normalized.includes("+") ? normalized : `${normalized}Z`);
    if (Number.isNaN(d.getTime())) return isoLike;
    return `${d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour12: false })} IST`;
  } catch {
    return isoLike;
  }
}

function nowStamp() {
  return new Date().toISOString().replace("T", " ").slice(0, 19);
}

function logUiError(message) {
  const el = $("ui-error-log");
  if (!el) return;
  const line = `[${nowStamp()}] ${message}`;
  if (el.textContent.trim() === "No errors yet.") el.textContent = line;
  else el.textContent = `${line}\n${el.textContent}`;
}

function isPuterAvailable() {
  try {
    const p = globalThis.puter;
    return p != null && typeof p === "object" && p.ai != null && typeof p.ai.chat === "function";
  } catch {
    return false;
  }
}

function refreshPuterStatusPill() {
  const el = $("puter-status");
  if (!el) return;
  if (isPuterAvailable()) {
    el.textContent = "Puter ready";
    el.classList.add("ok");
    el.classList.remove("waiting");
  } else {
    el.textContent = "Not loaded";
    el.classList.remove("ok");
    el.classList.add("waiting");
  }
}

function normalizePuterChatResponse(r) {
  if (r == null) return "";
  if (typeof r === "string") return r;
  if (typeof r === "object") {
    if (typeof r.message === "string") return r.message;
    if (typeof r.text === "string") return r.text;
    const msg = r.message;
    if (msg && typeof msg === "object") {
      const c = msg.content;
      if (typeof c === "string") return c;
      if (Array.isArray(c)) return c.map((part) => (part && (part.text || part.content)) || "").join("");
    }
  }
  try {
    return JSON.stringify(r, null, 2);
  } catch {
    return String(r);
  }
}

function buildPuterAnalysisContext() {
  const data = state.lastAnalysis;
  if (!data) return "";
  const slim = {
    symbol: data.symbol,
    finding_id: data.finding_id,
    brain: data.brain,
    ml: data.ml,
    trade_plan: data.trade_plan,
    metrics_subset: {
      ohlc_bars: data.metrics && data.metrics.ohlc_bars,
      ohlc_interval: data.metrics && data.metrics.ohlc_interval,
      market_focus: data.metrics && data.metrics.market_focus,
    },
  };
  return `CONTEXT_JSON (machine snapshot, not advice):\n${JSON.stringify(slim, null, 2)}\n\n`;
}

async function runPuterUserChat() {
  const out = $("puter-output");
  const rawPrompt = (($("puter-prompt") && $("puter-prompt").value) || "").trim();
  if (!rawPrompt) {
    if (out) out.textContent = "Enter a question first.";
    return;
  }
  if (!isPuterAvailable()) {
    if (out) out.textContent = "Puter.js did not load. Check network / blockers and reload.";
    logUiError("Puter.js not available");
    return;
  }
  const attach = $("puter-attach-analysis") && $("puter-attach-analysis").checked;
  const prefix = attach ? buildPuterAnalysisContext() : "";
  const model = ($("puter-model") && $("puter-model").value) || "gpt-4o-mini";
  if (out) out.textContent = "Waiting for Puter…";
  try {
    const resp = await globalThis.puter.ai.chat(prefix + rawPrompt, {
      model,
      temperature: 0.25,
    });
    if (out) out.textContent = normalizePuterChatResponse(resp) || "(empty response)";
  } catch (e) {
    const msg = e && (e.message || String(e));
    if (out) out.textContent = `Puter error: ${msg}`;
    logUiError(`Puter: ${msg}`);
  }
}

async function runPuterExplainAnalysis() {
  const out = $("puter-output");
  if (!state.lastAnalysis) {
    if (out) out.textContent = "Run Analyze in the Trading portal first.";
    return;
  }
  if (!isPuterAvailable()) {
    if (out) out.textContent = "Puter.js did not load.";
    logUiError("Puter.js not available");
    return;
  }
  const model = ($("puter-model") && $("puter-model").value) || "gpt-4o-mini";
  const prompt = [
    buildPuterAnalysisContext(),
    "Task: In 2 short paragraphs, explain what this snapshot suggests about structure vs risk,",
    "and list concrete caveats (data gaps, overfitting risk, no trade recommendation).",
    "Plain English; not financial advice.",
  ].join(" ");
  if (out) out.textContent = "Waiting for Puter…";
  try {
    const resp = await globalThis.puter.ai.chat(prompt, { model, temperature: 0.2 });
    if (out) out.textContent = normalizePuterChatResponse(resp) || "(empty response)";
  } catch (e) {
    const msg = e && (e.message || String(e));
    if (out) out.textContent = `Puter error: ${msg}`;
    logUiError(`Puter: ${msg}`);
  }
}

function writeHub(text) {
  const el = $("ml-online-readout");
  if (el) el.textContent = typeof text === "string" ? text : String(text);
}

function setPill(id, text, ok = true) {
  const el = $(id);
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("ok", ok);
  el.classList.toggle("waiting", !ok);
}

async function api(path, options = {}) {
  try {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!res.ok) {
      let msg = `${path} -> ${res.status} ${res.statusText}`;
      try {
        const j = await res.clone().json();
        if (j && j.detail != null) msg += ` | ${typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail)}`;
      } catch {
        /* ignore */
      }
      logUiError(msg);
      throw new Error(msg);
    }
    return res.json();
  } catch (e) {
    if (e && String(e.message || e).includes("fetch")) logUiError(`Network: ${path} (${e.message || e})`);
    throw e;
  }
}

function write(id, value) {
  const el = $(id);
  if (!el) return;
  el.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function pct01(x, digits = 1) {
  if (x == null || Number.isNaN(Number(x))) return "—";
  const n = Number(x);
  return `${(n <= 1 && n >= -1 ? n * 100 : n).toFixed(digits)}%`;
}

function signalModeLabel(mode) {
  const m = {
    structural: "Structural (brain-style score)",
    trend_ma: "Trend (two moving averages)",
    mean_reversion_z: "Mean reversion (z-score vs average)",
  };
  return m[mode] || mode;
}

function formatEvalModesReport(data) {
  const sym = data.symbol || "—";
  const period = data.period || "";
  const interval = data.interval || "";
  const rows = data.rows || [];
  const lines = [
    "Research mode comparison (walk-forward, educational only — not live advice)",
    `Symbol: ${sym}  |  Chart window: ${period} at ${interval} bars`,
    "",
  ];
  rows.forEach((row) => {
    const mode = row.signal_mode || "?";
    const a = row.assumptions || {};
    const cost = a.round_trip_cost_bps != null ? `${a.round_trip_cost_bps} basis points per full buy/sell` : "default costs";
    const horizon = a.horizon_bars != null ? `${a.horizon_bars} bars forward` : "?";
    const bar = a.bar_interval || "?";
    lines.push(`--- ${signalModeLabel(mode)} ---`);
    lines.push(`  Status: ${row.status || "—"}`);
    lines.push(`  Simulated trades: ${fmt(row.trades)}`);
    lines.push(`  Win rate (closed trades): ${pct01(row.win_rate)}`);
    lines.push(`  Profit factor: ${row.profit_factor != null ? Number(row.profit_factor).toFixed(3) : "—"}`);
    lines.push(`  Ending equity (normalized run): ${row.ending_equity != null ? Number(row.ending_equity).toFixed(4) : "—"}`);
    lines.push(`  Worst peak-to-trough drop: ${pct01(row.max_drawdown)}`);
    lines.push(`  How this run was modeled: ${bar} bars, hold horizon ${horizon}, costs about ${cost}.`);
    lines.push("");
  });
  lines.push("Lower drawdown and steadier profit factor usually matter more than raw win rate.");
  return lines.join("\n");
}

function formatOnlineLearningStatus(st) {
  const lines = ["Online learning (Hugging Face Hub)", ""];
  lines.push(
    st.hf_learning_datasets_env
      ? `Configured datasets: ${st.hf_learning_datasets_env}`
      : "No Hub datasets configured yet (set TRADING_AI_HF_LEARNING_DATASETS in your .env).",
  );
  lines.push(`Hub login token on this PC: ${st.hub_token_configured ? "Yes" : "No — private repos need HF_TOKEN"}`);
  lines.push(
    `Python "datasets" library: ${st.datasets_package_installed ? "Installed" : 'Not installed — run: pip install -e ".[hf]"'}`,
  );
  lines.push("");
  lines.push("How to type dataset names in .env:");
  lines.push("  • repo name, or repo:split, or repo:config:split (comma-separated, up to four).");
  if (st.install_hint) lines.push(`Tip: ${st.install_hint}`);
  return lines.join("\n");
}

function formatOnlineLearningPreview(pv) {
  const meta = pv.meta || {};
  const lines = ["Hub sample preview", ""];
  lines.push(`Status: ${meta.status || "—"}`);
  if (meta.hint) lines.push(`Note: ${meta.hint}`);
  if (Array.isArray(meta.datasets)) {
    meta.datasets.forEach((d) => {
      lines.push(`  • ${d.spec || d.repo || "?"} — ${d.status || ""}${d.error ? ` (${d.error})` : ""}`);
    });
  }
  lines.push("");
  lines.push("Short text sample the brain may digest (trimmed):");
  lines.push(pv.digest_preview || "(empty)");
  return lines.join("\n");
}

function formatDhanQuoteMap(d) {
  const lines = ["Dhan live quotes (LTP map)", ""];
  lines.push(`Broker credentials loaded: ${d.credentials_ready ? "Yes" : "No — set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN"}`);
  const syms = d.mapped_symbols || [];
  lines.push(
    syms.length
      ? `Symbols you mapped for LTP: ${syms.join(", ")}`
      : "No symbol map yet — set TRADING_AI_DHAN_LTP_MAP (JSON) so each Yahoo ticker points to Dhan security ids.",
  );
  if (d.api_base_url) lines.push(`API base: ${d.api_base_url}`);
  if (d.doc) lines.push(`Docs: ${d.doc}`);
  return lines.join("\n");
}

function formatStrategyTaxonomy(t) {
  const lines = [];
  if (t.reference_title) lines.push(`Reference: ${t.reference_title}`);
  if (t.reference_url) lines.push(`Read more: ${t.reference_url}`);
  if (t.notes) lines.push("", String(t.notes).replace(/`/g, "'"), "");
  const strategies = t.strategies || [];
  strategies.forEach((s, i) => {
    const name = s.groww_name || s.id || `Strategy ${i + 1}`;
    const ws = s.workstation || {};
    lines.push(`--- ${name} ---`);
    if (s.idea) lines.push(String(s.idea).replace(/`/g, "'"));
    lines.push(`In this app: ${ws.status || "—"}`);
    if (ws.implementation) lines.push(`How we use it: ${ws.implementation.replace(/`/g, "'")}`);
    lines.push("");
  });
  return lines.join("\n").trim();
}

function formatParameterCatalog(c) {
  const lines = ["Learnable parameters (knobs you can tune later)", ""];
  if (c.version) lines.push(`Catalog version: ${c.version}`, "");
  (c.families || []).forEach((fam) => {
    lines.push(`--- ${fam.label || fam.id} ---`);
    if (fam.description) lines.push(fam.description.replace(/`/g, "'"));
    (fam.workstation_mapping || []).forEach((m) => {
      const bits = [m.name];
      if (m.type) bits.push(`(${m.type})`);
      if (m.env) bits.push("— environment variable");
      const tail = m.range_hint || m.api || m.note || m.module || "";
      lines.push(`  • ${bits.join(" ")}${tail ? `: ${tail}` : ""}`);
    });
    lines.push("");
  });
  if (Array.isArray(c.feedback_loops) && c.feedback_loops.length) {
    lines.push("--- Feedback loops ---");
    c.feedback_loops.forEach((fb) => {
      lines.push(`  • ${fb.id || "?"}: ${(fb.description || "").replace(/`/g, "'")}`);
    });
  }
  return lines.join("\n").trim();
}

function coalesceJsonObject(raw) {
  if (raw == null) return {};
  if (typeof raw === "object" && !Array.isArray(raw)) return raw;
  if (typeof raw === "string") {
    try {
      const o = JSON.parse(raw);
      return typeof o === "object" && o != null && !Array.isArray(o) ? o : {};
    } catch {
      return {};
    }
  }
  return {};
}

function formatPostMortemWindow(s) {
  s = coalesceJsonObject(s);
  if (!s || Object.keys(s).length === 0) return "No summary yet.";
  const wr = s.win_rate;
  const winRateText = wr == null ? "not enough labeled trials yet" : pct01(wr);
  return [
    `Outcomes stored in the rolling window: ${fmt(s.window_outcomes)}`,
    `Labeled trials: ${fmt(s.labeled_trials)}`,
    `Wins counted: ${fmt(s.wins)}`,
    `Win rate in that window: ${winRateText}`,
  ].join("\n");
}

function formatPostMortemEventLine(e) {
  const p = e.payload || {};
  const bits = [`Time: ${formatIst(e.created_at)}`, `Type: ${e.event_type || "—"}`];
  if (p.finding_id) bits.push(`Finding: ${p.finding_id}`);
  if (p.symbol) bits.push(`Symbol: ${p.symbol}`);
  if (p.verdict != null) bits.push(`Verdict: ${p.verdict}`);
  if (p.forward_return != null) bits.push(`Forward return over window: ${(Number(p.forward_return) * 100).toFixed(2)}%`);
  return bits.join(" | ");
}

function formatHeatmapFeatures(f) {
  if (!f || typeof f !== "object") return "";
  return Object.entries(f)
    .map(([k, v]) => {
      const label = k.replace(/_/g, " ");
      if (v != null && typeof v === "object") return `  ${label}: (nested summary — see server logs if needed)`;
      return `  ${label}: ${v}`;
    })
    .join("\n");
}

function formatPostMortemResult(out) {
  if (!out || typeof out !== "object") return String(out);
  if (out.status === "error" || out.status === "insufficient_forward_bars") {
    return [
      `Could not finish post-mortem (${out.status}).`,
      out.message ? `Reason: ${out.message}` : "",
      out.symbol ? `Symbol: ${out.symbol}` : "",
      out.bars_available != null ? `Bars available after anchor: ${out.bars_available}` : "",
      out.horizon_bars != null ? `Needed horizon: ${out.horizon_bars}` : "",
    ]
      .filter(Boolean)
      .join("\n");
  }
  if (out.status !== "ok") return `Status: ${out.status || "unknown"}`;
  return [
    "Post-mortem finished (compares the old call to what price did next).",
    "",
    `Finding id: ${out.finding_id || "—"}`,
    `Symbol: ${out.symbol || "—"}`,
    `Brain action at the time: ${out.brain_action || "—"}`,
    `Price change over the next window: ${(Number(out.forward_return || 0) * 100).toFixed(3)}%`,
    `Verdict: ${out.verdict || "—"}${out.correct === true ? " (directionally matched)" : out.correct === false ? " (directionally missed)" : ""}`,
  ].join("\n");
}

function renderLearning(data) {
  const dataset = data.dataset || {};
  const frame = data.training_frame || {};
  const model = data.model || {};
  const metrics = model.metrics || {};
  $("metric-market-rows").textContent = fmt(dataset.rows);
  $("metric-training-rows").textContent = fmt(frame.rows);
  setPill("model-pill", model.version ? "Model ready" : "No model", Boolean(model.version));
  const dirAcc = metrics.directional_accuracy;
  const actAcc = metrics.active_accuracy;
  write(
    "learning-output",
    [
      "Local market model (NSE daily data you downloaded)",
      "",
      `Rows downloaded from Yahoo: ${fmt(dataset.rows)}`,
      `Symbols successfully pulled: ${fmt(dataset.symbols_ok)}`,
      `Rows used to train the small model: ${fmt(frame.rows)}`,
      `Model name: ${model.version || "not trained yet"}`,
      `Last trained (IST): ${model.trained_at ? formatIst(model.trained_at) : "—"}`,
      "",
      dirAcc != null
        ? `Directional hit rate: ${pct01(dirAcc)} of next-day moves called in the right direction (training metric).`
        : "Directional hit rate: not available until you train.",
      actAcc != null
        ? `Active-class hit rate: ${pct01(actAcc)} when the model is confident enough to act.`
        : "",
      "",
      "Hugging Face / Hub status is under Online sources (left panel).",
    ]
      .filter(Boolean)
      .join("\n"),
  );
}

function renderOrders(data) {
  const summary = data.summary || {};
  const orders = data.orders || [];
  $("metric-paper-orders").textContent = fmt(summary.orders);
  const ocDisp = summary.open_filled_orders != null ? fmt(summary.open_filled_orders) : "—";
  const ccDisp = summary.closed_orders != null ? fmt(summary.closed_orders) : "—";
  const rpDisp =
    summary.realized_pnl_total != null ? Number(summary.realized_pnl_total).toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—";
  $("orders-summary").textContent =
    `Orders ${fmt(summary.orders)} | Open ${ocDisp} | Closed ${ccDisp} | Realized PnL ${rpDisp} | Notional ${fmt(summary.notional)} | Risk ${fmt(summary.risk_amount)}`;
  write(
    "orders-output",
    orders.length
      ? orders
          .slice(0, 40)
          .map((o) => {
            const st = o.status === "closed_paper" ? "closed" : o.status;
            const pnl = o.realized_pnl != null ? ` pnl=${o.realized_pnl}` : "";
            return `${formatIst(o.created_at)} | ${o.symbol} | ${o.side} x${o.quantity} @ ${o.entry_price} | ${st}${pnl}`;
          })
          .join("\n")
      : "No paper orders yet.",
  );
}

function renderRisk(data) {
  $("metric-live").textContent = data.live_trading_enabled ? "Enabled" : "Blocked";
  setPill("paper-pill", data.paper_trading_enabled ? "Paper on" : "Paper off", data.paper_trading_enabled);
}

function renderReadiness(data) {
  const g = data.workstation_gates || {};
  const mf = g.market_focus || {};
  const kill = g.kill_switch_active ? "ON (paper blocked)" : "off";
  const ps = g.paper_sessions_ist || {};
  const today = g.paper_today_ist || {};
  const dq = g.data_quality || {};
  const cat = g.ml_profile_catalog || {};
  const chk = g.checklist_preview || {};
  write("readiness-output", [
    `Market focus (env default): ${mf.default_from_env || "-"}`,
    mf.blurb || "",
    "",
    `Kill switch: ${kill}`,
    `Paper IST sessions (days with orders): ${ps.sessions_with_orders ?? "-"} / target ${ps.roadmap_target_sessions ?? 20}`,
    `Today IST (${today.ist_date || "-"}): orders ${today.orders_today ?? 0}, risk sum ${today.risk_amount_today ?? 0}`,
    `ML profile catalog: files ${cat.files ?? "-"}, ingest errors ${cat.errors ?? "-"}`,
    `Data quality: ${dq.status || "-"} | issues: ${(dq.issues || []).join("; ") || "none"}`,
    `    warnings: ${(dq.warnings || []).join("; ") || "none"}`,
    "",
    "Checklist preview:",
    ...Object.entries(chk).map(([k, v]) => `  ${k}: ${v}`),
  ].join("\n"));
}

function renderLoops(data) {
  const r = data.refinement_for_next_brain || {};
  const pm = data.recent_post_mortems || [];
  const file = data.loop_file || {};
  const pmSummary = coalesceJsonObject(r.post_mortem_summary);
  write(
    "loops-output",
    [
      "Self-learning loop (how the desk improves over time)",
      "",
      `Score nudge applied to the next brain pass: ${r.refinement_score_nudge ?? 0} (small number from recent reviews).`,
      "",
      "Recent post-mortem window (paper / research checks):",
      formatPostMortemWindow(pmSummary),
      "",
      `Outcomes saved to disk for learning: ${fmt(file.outcomes_stored ?? 0)}`,
      "",
      pm.length ? "Latest post-mortem events:" : "No post-mortems yet — run one from the Post-mortem section above.",
      pm.length ? pm.map((e) => formatPostMortemEventLine(e)).join("\n") : "",
    ]
      .filter(Boolean)
      .join("\n\n"),
  );
}

function formatBrainCouncil(bc) {
  if (!bc || typeof bc !== "object") return "off";
  const mode = bc.mode || "?";
  const d = bc.disagreement != null ? Number(bc.disagreement).toFixed(2) : "?";
  const agents = Array.isArray(bc.agents) ? bc.agents : [];
  if (!agents.length) return `${mode} | disagreement=${d}`;
  const line = agents
    .map((a) => `${a.id || "?"}:${a.stance || "?"}@${(a.confidence != null ? Number(a.confidence).toFixed(2) : "?")}`)
    .join(" | ");
  return `${mode} | disagreement=${d} | ${line}`;
}

function renderAnalysis(data) {
  state.lastAnalysis = data;
  state.lastFindingId = data.finding_id || "";
  const pmInput = $("post-mortem-id");
  if (pmInput && state.lastFindingId) pmInput.value = state.lastFindingId;
  const brain = data.brain || {};
  const ml = data.ml || {};
  const plan = data.trade_plan || {};
  const ol = data.online_learning || {};
  const mx = data.metrics || {};
  const bar = mx.ohlc_interval ? `${mx.ohlc_interval}/${mx.ohlc_period || "?"}` : "";
  $("brain-summary").textContent =
    `${data.symbol} ${bar ? `(${bar}) ` : ""}| ${brain.action || "-"} | score ${Number(brain.score || 0).toFixed(3)} | ` +
    `plan ${plan.eligible ? "eligible" : "blocked"}`;
  $("place-paper-order").disabled = !plan.eligible;
  const hm = data.heatmap_context || {};
  const hmLine =
    hm && hm.features
      ? `Option heatmap (${hm.underlying || "?"} on ${hm.trade_date || "?"}):\n${formatHeatmapFeatures(hm.features)}`
      : "";
  write(
    "brain-output",
    [
      `Finding id: ${data.finding_id || "-"}`,
      `ML: ${ml.regime || "-"} | score ${Number(ml.score || 0).toFixed(3)}`,
      `Brain: ${brain.action || "-"} | confidence ${Number(brain.confidence || 0).toFixed(2)}`,
      `Paper side: ${plan.side || "flat"} | qty ${fmt(plan.quantity)} | type ${plan.instrument_type || "-"} | lots ${fmt(plan.lots)} @ lot ${plan.lot_size ?? "-"}`,
      `Entry: ${plan.entry_price ?? "-"} | Stop: ${plan.stop_loss ?? "-"} | Target: ${plan.target ?? "-"}`,
      `Vetoes: ${(plan.vetoes || []).join(", ") || "none"}`,
      `Warnings: ${(plan.warnings || []).join(", ") || "none"}`,
      `Bars: ${mx.ohlc_bars ?? "-"} | focus: ${mx.market_focus || "-"}`,
      `Brain council: ${formatBrainCouncil(data.brain_council)}`,
      `Online learning: Hugging Face ${(ol.hf_hub || {}).status || "—"} | Global context symbols OK: ${(ol.global_context || {}).symbols_ok ?? "—"} | Extra local file digest sent to brain: ${ol.local_file_digest_included ? "yes" : "no"}`,
      hmLine,
      "",
      ml.rationale || "",
    ]
      .filter(Boolean)
      .join("\n"),
  );
}

const PERIODS_EQUITY = [
  ["6mo", "6 months"],
  ["1y", "1 year", true],
  ["2y", "2 years"],
  ["5y", "5 years"],
];
const PERIODS_FNO = [
  ["5d", "5 days"],
  ["30d", "30 days"],
  ["60d", "60 days", true],
  ["120d", "120 days"],
];

function syncBrainPeriodOptions() {
  const focusEl = $("brain-market-focus");
  const sel = $("brain-period");
  const wrap = $("brain-interval-wrap");
  const label = $("brain-period-label");
  if (!focusEl || !sel) return;
  const focus = focusEl.value;
  const list = focus === "derivatives_intraday" ? PERIODS_FNO : PERIODS_EQUITY;
  sel.innerHTML = "";
  list.forEach((row) => {
    const [val, text] = row;
    const selected = row[2] === true;
    const opt = document.createElement("option");
    opt.value = val;
    opt.textContent = text;
    if (selected) opt.selected = true;
    sel.appendChild(opt);
  });
  if (wrap) wrap.style.display = focus === "derivatives_intraday" ? "" : "none";
  if (label) label.textContent = focus === "derivatives_intraday" ? "Intraday lookback" : "Period (daily)";
  const sym = $("brain-symbol");
  if (sym && focus === "derivatives_intraday") {
    if (!sym.value.trim() || sym.value === "RELIANCE.NS" || sym.value === "NIFTY.NS") sym.value = "^NSEI";
  } else if (sym && focus === "balanced" && (sym.value === "^NSEI" || sym.value === "NIFTY.NS")) {
    sym.value = "RELIANCE.NS";
  }
}

const REQUIRED_API_PATHS = [
  "/api/ml/online-learning/status",
  "/api/ml/online-learning/preview",
  "/api/research/eval-modes",
  "/api/quant/strategy-taxonomy",
];

/**
 * Confirms the browser is talking to THIS FastAPI app (not an old process / other tool on the port).
 */
async function verifyServerThenRefresh() {
  let res;
  try {
    res = await fetch("/api/health");
  } catch (e) {
    logUiError(`Network /api/health: ${e.message || e}`);
    setPill("server-pill", "Offline", false);
    write(
      "learning-output",
      "Could not reach /api/health. Check that the server is running and the URL is http://127.0.0.1:<port>/",
    );
    return false;
  }
  if (!res.ok) {
    const t = await res.text().catch(() => "");
    logUiError(`/api/health -> ${res.status} ${res.statusText}`);
    setPill("server-pill", "Wrong app / old PID", false);
    write(
      "learning-output",
      [
        `This page loaded, but /api/health returned ${res.status} (body: ${t.slice(0, 240)}).`,
        "",
        "That usually means port 8000 is owned by a different program or an OLD python server.",
        "Fix: Launcher [2] Stop server, then [1] Start (from THIS project folder).",
        "Or open http://127.0.0.1:8000/api/health in a tab — you should see JSON with api_paths and app_module_file.",
      ].join("\n"),
    );
    return false;
  }
  let meta;
  try {
    meta = await res.json();
  } catch (e) {
    logUiError(`Bad JSON from /api/health: ${e.message || e}`);
    return false;
  }
  const paths = new Set(meta.api_paths || []);
  const missing = REQUIRED_API_PATHS.filter((p) => !paths.has(p));
  if (missing.length) {
    const msg = [
      "The process on this port responded to /api/health but is missing routes:",
      "",
      ...missing.map((m) => `  - ${m}`),
      "",
      `Python: ${meta.python_executable || "?"}`,
      `App file: ${meta.app_module_file || "?"}`,
      "",
      "Stop all \"Trading AI\" / uvicorn windows, then start again from this repo.",
    ].join("\n");
    logUiError(`Health OK but missing routes: ${missing.join(", ")}`);
    setPill("server-pill", "Stale / wrong build", false);
    write("learning-output", msg);
    return false;
  }
  await refreshAll();
  return true;
}

async function refreshAll() {
  const endpoints = [
    { path: "/api/ml/market-learning/status", render: renderLearning },
    { path: "/api/trading/paper/orders", render: renderOrders },
    { path: "/api/trading/risk", render: renderRisk },
    { path: "/api/trading/readiness", render: renderReadiness },
    { path: "/api/learning/loops", render: renderLoops },
  ];
  const settled = await Promise.allSettled(endpoints.map((e) => api(e.path)));
  const errors = [];
  settled.forEach((r, i) => {
    if (r.status === "fulfilled") endpoints[i].render(r.value);
    else errors.push(String(r.reason?.message || r.reason));
  });
  if (errors.length === 0) {
    setPill("server-pill", "Online", true);
  } else if (errors.length === endpoints.length) {
    setPill("server-pill", "Offline", false);
    write(
      "learning-output",
      [
        "Could not reach the Trading AI API (all checks failed).",
        "",
        ...errors,
        "",
        "Stop the launcher server [2] then [1] Start, or fix the port conflict.",
      ].join("\n"),
    );
    errors.forEach((e) => logUiError(e));
  } else {
    setPill("server-pill", "Partial", false);
    const learningOk = settled[0].status === "fulfilled";
    const learningSnap = learningOk ? $("learning-output").textContent.trim() : "";
    write(
      "learning-output",
      ["--- Some API calls failed ---", "", ...errors, "", learningSnap].filter(Boolean).join("\n\n"),
    );
    errors.forEach((e) => logUiError(e));
  }
}

function wirePortalTabs() {
  document.querySelectorAll(".portal-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      const p = btn.getAttribute("data-portal");
      document.querySelectorAll(".portal-tab").forEach((b) => b.classList.toggle("active", b === btn));
      $("portal-ml").classList.toggle("hidden", p !== "ml");
      $("portal-trading").classList.toggle("hidden", p !== "trading");
    });
  });
  document.querySelectorAll(".subportal-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      const sub = btn.getAttribute("data-sub");
      document.querySelectorAll(".subportal-tab").forEach((b) => b.classList.toggle("active", b === btn));
      $("trading-paper").classList.toggle("hidden", sub !== "paper");
      $("trading-live").classList.toggle("hidden", sub !== "live");
    });
  });
}

function defaultPaperDates() {
  const to = new Date();
  const from = new Date();
  from.setDate(from.getDate() - 90);
  const toEl = $("paper-to-date");
  const fromEl = $("paper-from-date");
  if (toEl && !toEl.value) toEl.value = to.toISOString().slice(0, 10);
  if (fromEl && !fromEl.value) fromEl.value = from.toISOString().slice(0, 10);
}

function paperRangeQuery() {
  const q = new URLSearchParams();
  const df = ($("paper-from-date") && $("paper-from-date").value) || "";
  const dt = ($("paper-to-date") && $("paper-to-date").value) || "";
  if (df) q.set("date_from", df);
  if (dt) q.set("date_to", dt);
  return q.toString();
}

async function loadPaperHistoryPanel() {
  const qs = paperRangeQuery();
  write("paper-pnl-readout", "Loading...");
  try {
    const [hist, pnl] = await Promise.all([
      api(`/api/trading/paper/history?${qs}&limit=500`),
      api(`/api/trading/paper/pnl-summary?${qs}`),
    ]);
    const s = hist.summary || {};
    const tbody = $("paper-history-tbody");
    tbody.innerHTML = "";
    (hist.orders || []).forEach((o) => {
      const tr = document.createElement("tr");
      const exitCell =
        o.exit_price != null && o.exit_price !== ""
          ? escapeHtml(String(o.exit_price))
          : "—";
      const pnlCell = o.realized_pnl != null && o.realized_pnl !== "" ? escapeHtml(String(o.realized_pnl)) : "—";
      const canClose = o.status === "filled_paper";
      const closeBtn = canClose
        ? `<button type="button" class="secondary" data-close-order="${escapeHtml(o.id)}">Close @ Yahoo</button>`
        : "";
      tr.innerHTML = [
        `<td>${escapeHtml(formatIst(o.created_at))}</td>`,
        `<td>${escapeHtml(o.symbol)}</td>`,
        `<td>${escapeHtml(o.side)}</td>`,
        `<td>${escapeHtml(o.quantity)}</td>`,
        `<td>${escapeHtml(o.entry_price)}</td>`,
        `<td>${escapeHtml(o.notional)}</td>`,
        `<td>${escapeHtml(o.risk_amount)}</td>`,
        `<td>${escapeHtml(o.status)}</td>`,
        `<td>${exitCell}</td>`,
        `<td>${pnlCell}</td>`,
        `<td>${closeBtn}</td>`,
      ].join("");
      tbody.appendChild(tr);
    });
    const rtot = pnl.realized_pnl_total != null ? pnl.realized_pnl_total : s.realized_pnl_total;
    const openN = pnl.open_filled_orders != null ? pnl.open_filled_orders : s.open_filled_orders;
    const closedN = pnl.closed_orders != null ? pnl.closed_orders : s.closed_orders;
    write(
      "paper-pnl-readout",
      [
        `Orders in window: ${fmt(s.orders)} | Open: ${fmt(openN)} | Closed: ${fmt(closedN)} | Realized PnL sum: ${fmt(rtot)}`,
        `Notional sum: ${fmt(s.notional)} | Risk sum: ${fmt(s.risk_amount)}`,
        `First: ${s.first_order_at ? formatIst(s.first_order_at) : "-"} | Last: ${s.last_order_at ? formatIst(s.last_order_at) : "-"}`,
        "",
        pnl.disclaimer || "",
      ].join("\n"),
    );
  } catch (e) {
    write("paper-pnl-readout", String(e.message || e));
  }
}

async function loadFindingsTable() {
  const tbody = $("findings-tbody");
  if (!tbody) return;
  tbody.innerHTML = "";
  try {
    const data = await api("/api/ml/findings/recent?limit=50");
    (data.findings || []).forEach((f) => {
      const tr = document.createElement("tr");
      const tags = Array.isArray(f.tags) ? f.tags.join(", ") : "";
      tr.innerHTML = [
        `<td>${escapeHtml(formatIst(f.created_at))}</td>`,
        `<td>${escapeHtml(f.symbol)}</td>`,
        `<td>${escapeHtml((f.summary || "").slice(0, 220))}</td>`,
        `<td>${escapeHtml(tags)}</td>`,
      ].join("");
      tbody.appendChild(tr);
    });
    if (!data.findings || data.findings.length === 0) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td colspan="4">No findings stored yet. Run Analyze in the Trading portal.</td>`;
      tbody.appendChild(tr);
    }
  } catch (e) {
    logUiError(String(e.message || e));
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="4">${escapeHtml(String(e.message || e))}</td>`;
    tbody.appendChild(tr);
  }
}

/* ---- Event bindings ---- */

$("clear-ui-errors")?.addEventListener("click", () => {
  write("ui-error-log", "No errors yet.");
});

$("refresh-learning")?.addEventListener("click", refreshAll);

$("refresh-online-learning")?.addEventListener("click", async () => {
  writeHub("Loading...");
  try {
    const st = await api("/api/ml/online-learning/status");
    writeHub(formatOnlineLearningStatus(st));
  } catch (e) {
    writeHub(String(e.message || e));
  }
});

$("dhan-quote-map")?.addEventListener("click", async () => {
  writeHub("Loading...");
  try {
    writeHub(formatDhanQuoteMap(await api("/api/dhan/quote-map")));
  } catch (e) {
    writeHub(String(e.message || e));
  }
});

$("eval-research-modes")?.addEventListener("click", async () => {
  const sym = $("brain-symbol").value.trim() || "^NSEI";
  writeHub("Running mode comparison (may take a minute)...");
  try {
    const data = await api(
      `/api/research/eval-modes?symbol=${encodeURIComponent(sym)}&period=2y&interval=1d&horizon=5&cost_bps=12&spread_bps=0`
    );
    writeHub(formatEvalModesReport(data));
  } catch (e) {
    writeHub(String(e.message || e));
  }
});

$("preview-hf-digest")?.addEventListener("click", async () => {
  writeHub("Fetching Hub streaming preview...");
  try {
    const pv = await api("/api/ml/online-learning/preview?max_rows=8");
    writeHub(formatOnlineLearningPreview(pv));
  } catch (e) {
    writeHub(String(e.message || e));
  }
});

$("refresh-findings")?.addEventListener("click", loadFindingsTable);

$("load-strategy-taxonomy")?.addEventListener("click", async () => {
  try {
    write("ml-strat-readout", formatStrategyTaxonomy(await api("/api/quant/strategy-taxonomy")));
  } catch (e) {
    write("ml-strat-readout", String(e.message || e));
  }
});

$("load-param-catalog")?.addEventListener("click", async () => {
  try {
    write("ml-strat-readout", formatParameterCatalog(await api("/api/quant/parameter-catalog")));
  } catch (e) {
    write("ml-strat-readout", String(e.message || e));
  }
});

$("refresh-loops-ml")?.addEventListener("click", async () => {
  try {
    renderLoops(await api("/api/learning/loops"));
  } catch (e) {
    write("loops-output", String(e.message || e));
  }
});

$("refresh-orders")?.addEventListener("click", async () => {
  try {
    renderOrders(await api("/api/trading/paper/orders"));
  } catch (e) {
    logUiError(String(e.message || e));
  }
});

$("refresh-readiness")?.addEventListener("click", async () => {
  try {
    renderReadiness(await api("/api/trading/readiness"));
  } catch (e) {
    write("readiness-output", String(e.message || e));
  }
});

$("refresh-risk-live")?.addEventListener("click", async () => {
  try {
    write("live-risk-readout", JSON.stringify(await api("/api/trading/risk"), null, 2));
  } catch (e) {
    write("live-risk-readout", String(e.message || e));
  }
});

$("refresh-evolution")?.addEventListener("click", async () => {
  try {
    write("evolution-readout", JSON.stringify(await api("/api/trading/evolution"), null, 2));
  } catch (e) {
    write("evolution-readout", String(e.message || e));
  }
});

$("load-paper-history")?.addEventListener("click", loadPaperHistoryPanel);

$("download-trades-csv")?.addEventListener("click", () => {
  const qs = paperRangeQuery();
  window.location.assign(`/api/trading/paper/export.csv?${qs}`);
});

$("download-pnl-json")?.addEventListener("click", async () => {
  const qs = paperRangeQuery();
  try {
    const data = await api(`/api/trading/paper/pnl-summary?${qs}`);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `pnl_exposure_${($("paper-from-date") && $("paper-from-date").value) || "all"}_${($("paper-to-date") && $("paper-to-date").value) || "all"}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) {
    logUiError(String(e.message || e));
  }
});

$("download-data")?.addEventListener("click", async () => {
  write("learning-output", "Downloading market data. This can take a minute.");
  try {
    const years = Number($("learn-years").value || 7);
    const maxSymbols = Number($("learn-symbols").value || 25);
    const data = await api(`/api/ml/market-learning/download?years=${years}&max_symbols=${maxSymbols}`, {
      method: "POST",
    });
    renderLearning(data.status);
  } catch (e) {
    write("learning-output", String(e.message || e));
  }
});

$("train-model")?.addEventListener("click", async () => {
  write("learning-output", "Training model from downloaded rows.");
  try {
    await api("/api/ml/market-learning/train", { method: "POST" });
    renderLearning(await api("/api/ml/market-learning/status"));
  } catch (e) {
    write("learning-output", String(e.message || e));
  }
});

$("analyze-symbol")?.addEventListener("click", async () => {
  write("brain-output", "Running brain.");
  try {
    const focus = ($("brain-market-focus") && $("brain-market-focus").value) || "derivatives_intraday";
    const intraday = focus === "derivatives_intraday";
    const data = await api("/api/brain/analyze", {
      method: "POST",
      body: JSON.stringify({
        symbol: $("brain-symbol").value.trim() || "^NSEI",
        period: $("brain-period").value,
        interval: intraday ? $("brain-interval").value : "1d",
        market_focus: focus,
        use_llm: Boolean($("brain-use-llm") && $("brain-use-llm").checked),
        use_brain_council: Boolean($("brain-use-council") && $("brain-use-council").checked),
        include_yahoo_deep: $("include-yahoo-deep").checked,
        include_ml_digest: false,
        include_heatmap: $("include-heatmap").checked,
        heatmap_underlying: $("heatmap-underlying").value,
        heatmap_source: $("heatmap-source").value,
      }),
    });
    renderAnalysis(data);
    refreshPuterStatusPill();
  } catch (e) {
    write("brain-output", String(e.message || e));
  }
});

$("puter-ask")?.addEventListener("click", () => {
  runPuterUserChat().catch((e) => logUiError(String((e && e.message) || e)));
});

$("puter-explain-analysis")?.addEventListener("click", () => {
  runPuterExplainAnalysis().catch((e) => logUiError(String((e && e.message) || e)));
});

$("run-post-mortem")?.addEventListener("click", async () => {
  const fid = ($("post-mortem-id").value || "").trim() || state.lastFindingId;
  if (!fid) {
    write("loops-output", "Set finding id or run Analyze in Trading portal first.");
    return;
  }
  const h = Number($("post-mortem-horizon").value || 5);
  write("loops-output", "Running post-mortem...");
  try {
    const out = await api("/api/learning/post-mortem", {
      method: "POST",
      body: JSON.stringify({ finding_id: fid, horizon_bars: h }),
    });
    write("loops-output", formatPostMortemResult(out));
    renderLoops(await api("/api/learning/loops"));
  } catch (e) {
    write("loops-output", String(e.message || e));
  }
});

$("sweep-backtest")?.addEventListener("click", async () => {
  const sym = $("brain-symbol").value.trim() || "RELIANCE.NS";
  write("backtest-output", "Running coarse horizon x cost grid (may take a minute)...");
  try {
    const data = await api(`/api/quant/backtest-sweep?symbol=${encodeURIComponent(sym)}&period=5y`);
    write(
      "backtest-output",
      [
        `Best grid cell: ${JSON.stringify(data.best || {})}`,
        "",
        "Top ranked:",
        ...(data.ranked || []).map((r) => JSON.stringify(r)),
      ].join("\n")
    );
  } catch (e) {
    write("backtest-output", String(e.message || e));
  }
});

$("run-backtest")?.addEventListener("click", async () => {
  const sym = $("brain-symbol").value.trim() || "RELIANCE.NS";
  const h = Number($("bt-horizon").value || 5);
  const cost = Number($("bt-cost").value || 8);
  const mode = ($("bt-signal-mode") && $("bt-signal-mode").value) || "structural";
  write("backtest-output", "Running backtest...");
  try {
    const sp = Number($("bt-spread")?.value || 0);
    const data = await api(
      `/api/research/backtest?symbol=${encodeURIComponent(sym)}&period=5y&horizon=${h}&cost_bps=${cost}&spread_bps=${sp}&signal_mode=${encodeURIComponent(mode)}`
    );
    const s = data.summary || {};
    const cfg = data.config || {};
    write(
      "backtest-output",
      [
        `mode ${cfg.signal_mode || mode} | ${data.symbol} | trades ${s.trades ?? "-"} | win rate ${s.win_rate ?? "-"}`,
        `avg return/trade ${s.avg_return_per_trade ?? "-"} | max DD ${s.max_drawdown ?? "-"}`,
        `ending equity ${s.ending_equity ?? "-"} | ${s.warning || ""}`,
        "",
        JSON.stringify(s, null, 2),
      ].join("\n")
    );
  } catch (e) {
    write("backtest-output", String(e.message || e));
  }
});

$("place-paper-order")?.addEventListener("click", async () => {
  if (!state.lastAnalysis) return;
  try {
    const result = await api("/api/trading/paper/order", {
      method: "POST",
      body: JSON.stringify({
        finding_id: state.lastAnalysis.finding_id,
        symbol: state.lastAnalysis.symbol,
        plan: state.lastAnalysis.trade_plan,
        brain: state.lastAnalysis.brain,
      }),
    });
    write("brain-output", JSON.stringify(result, null, 2));
    renderOrders(await api("/api/trading/paper/orders"));
  } catch (e) {
    write("brain-output", String(e.message || e));
  }
});

$("paper-history-tbody")?.addEventListener("click", async (ev) => {
  const btn = ev.target && ev.target.closest ? ev.target.closest("[data-close-order]") : null;
  if (!btn) return;
  const id = btn.getAttribute("data-close-order");
  if (!id) return;
  btn.disabled = true;
  try {
    await api("/api/trading/paper/close", {
      method: "POST",
      body: JSON.stringify({ order_id: id }),
    });
    await loadPaperHistoryPanel();
    renderOrders(await api("/api/trading/paper/orders"));
  } catch (e) {
    btn.disabled = false;
    write("paper-pnl-readout", String(e.message || e));
  }
});

if ($("brain-market-focus")) {
  $("brain-market-focus").addEventListener("change", syncBrainPeriodOptions);
  syncBrainPeriodOptions();
}

wirePortalTabs();
defaultPaperDates();
verifyServerThenRefresh().then((ok) => {
  refreshPuterStatusPill();
  setTimeout(refreshPuterStatusPill, 600);
  setTimeout(refreshPuterStatusPill, 2500);
  if (ok) loadFindingsTable();
});
