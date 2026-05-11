const state = {
  lastAnalysis: null,
  lastFindingId: "",
};

const $ = (id) => document.getElementById(id);

function fmt(n) {
  return Number(n || 0).toLocaleString();
}

function setPill(id, text, ok = true) {
  const el = $(id);
  el.textContent = text;
  el.classList.toggle("ok", ok);
  el.classList.toggle("waiting", !ok);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return res.json();
}

function write(id, value) {
  $(id).textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function renderLearning(data) {
  const dataset = data.dataset || {};
  const frame = data.training_frame || {};
  const model = data.model || {};
  const metrics = model.metrics || {};
  $("metric-market-rows").textContent = fmt(dataset.rows);
  $("metric-training-rows").textContent = fmt(frame.rows);
  setPill("model-pill", model.version ? "Model ready" : "No model", Boolean(model.version));
  write(
    "learning-output",
    [
      `Downloaded rows: ${fmt(dataset.rows)}`,
      `Symbols: ${fmt(dataset.symbols_ok)}`,
      `Training rows: ${fmt(frame.rows)}`,
      `Model: ${model.version || "not trained"}`,
      `Trained at: ${model.trained_at || "-"}`,
      `Directional accuracy: ${metrics.directional_accuracy ?? "-"}`,
      `Active accuracy: ${metrics.active_accuracy ?? "-"}`,
    ].join("\n"),
  );
}

function renderOrders(data) {
  const summary = data.summary || {};
  const orders = data.orders || [];
  $("metric-paper-orders").textContent = fmt(summary.orders);
  $("orders-summary").textContent =
    `Orders ${fmt(summary.orders)} | Notional ${fmt(summary.notional)} | Risk ${fmt(summary.risk_amount)}`;
  write(
    "orders-output",
    orders.length
      ? orders
          .slice(0, 20)
          .map((o) => `${o.created_at} | ${o.symbol} | ${o.side} x${o.quantity} @ ${o.entry_price}`)
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
  const kill = g.kill_switch_active ? "ON (paper blocked)" : "off";
  const ps = g.paper_sessions_ist || {};
  const today = g.paper_today_ist || {};
  const dq = g.data_quality || {};
  const cat = g.ml_profile_catalog || {};
  const chk = g.checklist_preview || {};
  write("readiness-output", [
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
  write(
    "loops-output",
    [
      `Next-brain nudge: ${r.refinement_score_nudge ?? "-"}`,
      `Post-mortem window: ${JSON.stringify(r.post_mortem_summary || {})}`,
      `Stored outcomes: ${file.outcomes_stored ?? 0}`,
      "",
      pm.length ? pm.map((e) => `${e.created_at} | ${e.event_type} | ${JSON.stringify(e.payload || {})}`).join("\n") : "No post-mortems yet.",
    ].join("\n"),
  );
}

function renderAnalysis(data) {
  state.lastAnalysis = data;
  state.lastFindingId = data.finding_id || "";
  const pmInput = $("post-mortem-id");
  if (pmInput && state.lastFindingId) pmInput.value = state.lastFindingId;
  const brain = data.brain || {};
  const ml = data.ml || {};
  const plan = data.trade_plan || {};
  $("brain-summary").textContent =
    `${data.symbol} | ${brain.action || "-"} | score ${Number(brain.score || 0).toFixed(3)} | ` +
    `plan ${plan.eligible ? "eligible" : "blocked"}`;
  $("place-paper-order").disabled = !plan.eligible;
  const hm = data.heatmap_context || {};
  const hmLine =
    hm && hm.features
      ? `Heatmap (${hm.underlying || "?"} @ ${hm.trade_date || "?"}): ${JSON.stringify(hm.features)}`
      : "";
  write(
    "brain-output",
    [
      `Finding id: ${data.finding_id || "-"}`,
      `ML: ${ml.regime || "-"} | score ${Number(ml.score || 0).toFixed(3)}`,
      `Brain: ${brain.action || "-"} | confidence ${Number(brain.confidence || 0).toFixed(2)}`,
      `Paper side: ${plan.side || "flat"} | quantity ${fmt(plan.quantity)}`,
      `Entry: ${plan.entry_price ?? "-"} | Stop: ${plan.stop_loss ?? "-"} | Target: ${plan.target ?? "-"}`,
      `Vetoes: ${(plan.vetoes || []).join(", ") || "none"}`,
      hmLine,
      "",
      ml.rationale || "",
    ]
      .filter(Boolean)
      .join("\n"),
  );
}

async function refreshAll() {
  try {
    setPill("server-pill", "Online", true);
    const [learning, orders, risk, readiness, loops] = await Promise.all([
      api("/api/ml/market-learning/status"),
      api("/api/trading/paper/orders"),
      api("/api/trading/risk"),
      api("/api/trading/readiness"),
      api("/api/learning/loops"),
    ]);
    renderLearning(learning);
    renderOrders(orders);
    renderRisk(risk);
    renderReadiness(readiness);
    renderLoops(loops);
  } catch (err) {
    setPill("server-pill", "Offline", false);
    write("learning-output", `Could not load status: ${err.message}`);
  }
}

$("refresh-learning").addEventListener("click", refreshAll);
$("refresh-orders").addEventListener("click", async () => renderOrders(await api("/api/trading/paper/orders")));
$("refresh-readiness").addEventListener("click", async () => renderReadiness(await api("/api/trading/readiness")));

$("download-data").addEventListener("click", async () => {
  write("learning-output", "Downloading market data. This can take a minute.");
  const years = Number($("learn-years").value || 7);
  const maxSymbols = Number($("learn-symbols").value || 25);
  const data = await api(`/api/ml/market-learning/download?years=${years}&max_symbols=${maxSymbols}`, {
    method: "POST",
  });
  renderLearning(data.status);
});

$("train-model").addEventListener("click", async () => {
  write("learning-output", "Training model from downloaded rows.");
  await api("/api/ml/market-learning/train", { method: "POST" });
  renderLearning(await api("/api/ml/market-learning/status"));
});

$("analyze-symbol").addEventListener("click", async () => {
  write("brain-output", "Running brain.");
  const data = await api("/api/brain/analyze", {
    method: "POST",
    body: JSON.stringify({
      symbol: $("brain-symbol").value.trim() || "RELIANCE.NS",
      period: $("brain-period").value,
      use_llm: false,
      include_yahoo_deep: false,
      include_ml_digest: false,
      include_heatmap: $("include-heatmap").checked,
      heatmap_underlying: $("heatmap-underlying").value,
      heatmap_source: $("heatmap-source").value,
    }),
  });
  renderAnalysis(data);
});

$("run-post-mortem").addEventListener("click", async () => {
  const fid = ($("post-mortem-id").value || "").trim() || state.lastFindingId;
  if (!fid) {
    write("loops-output", "Set finding id or run Analyze first.");
    return;
  }
  const h = Number($("post-mortem-horizon").value || 5);
  write("loops-output", "Running post-mortem...");
  try {
    const out = await api("/api/learning/post-mortem", {
      method: "POST",
      body: JSON.stringify({ finding_id: fid, horizon_bars: h }),
    });
    write("loops-output", JSON.stringify(out, null, 2));
    renderLoops(await api("/api/learning/loops"));
  } catch (e) {
    write("loops-output", String(e.message || e));
  }
});

$("refresh-loops").addEventListener("click", async () => renderLoops(await api("/api/learning/loops")));

$("run-backtest").addEventListener("click", async () => {
  const sym = $("brain-symbol").value.trim() || "RELIANCE.NS";
  const h = Number($("bt-horizon").value || 5);
  const cost = Number($("bt-cost").value || 8);
  write("backtest-output", "Running backtest...");
  try {
    const data = await api(
      `/api/research/backtest?symbol=${encodeURIComponent(sym)}&period=5y&horizon=${h}&cost_bps=${cost}`
    );
    const s = data.summary || {};
    write(
      "backtest-output",
      [
        `${data.symbol} | trades ${s.trades ?? "-"} | win rate ${s.win_rate ?? "-"}`,
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

$("place-paper-order").addEventListener("click", async () => {
  if (!state.lastAnalysis) return;
  const result = await api("/api/trading/paper/order", {
    method: "POST",
    body: JSON.stringify({
      finding_id: state.lastAnalysis.finding_id,
      symbol: state.lastAnalysis.symbol,
      plan: state.lastAnalysis.trade_plan,
      brain: state.lastAnalysis.brain,
    }),
  });
  write("brain-output", result);
  renderOrders(await api("/api/trading/paper/orders"));
});

refreshAll();
