const state = {
  lastAnalysis: null,
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

function renderAnalysis(data) {
  state.lastAnalysis = data;
  const brain = data.brain || {};
  const ml = data.ml || {};
  const plan = data.trade_plan || {};
  $("brain-summary").textContent =
    `${data.symbol} | ${brain.action || "-"} | score ${Number(brain.score || 0).toFixed(3)} | ` +
    `plan ${plan.eligible ? "eligible" : "blocked"}`;
  $("place-paper-order").disabled = !plan.eligible;
  write(
    "brain-output",
    [
      `ML: ${ml.regime || "-"} | score ${Number(ml.score || 0).toFixed(3)}`,
      `Brain: ${brain.action || "-"} | confidence ${Number(brain.confidence || 0).toFixed(2)}`,
      `Paper side: ${plan.side || "flat"} | quantity ${fmt(plan.quantity)}`,
      `Entry: ${plan.entry_price ?? "-"} | Stop: ${plan.stop_loss ?? "-"} | Target: ${plan.target ?? "-"}`,
      `Vetoes: ${(plan.vetoes || []).join(", ") || "none"}`,
      "",
      ml.rationale || "",
    ].join("\n"),
  );
}

async function refreshAll() {
  try {
    setPill("server-pill", "Online", true);
    const [learning, orders, risk] = await Promise.all([
      api("/api/ml/market-learning/status"),
      api("/api/trading/paper/orders"),
      api("/api/trading/risk"),
    ]);
    renderLearning(learning);
    renderOrders(orders);
    renderRisk(risk);
  } catch (err) {
    setPill("server-pill", "Offline", false);
    write("learning-output", `Could not load status: ${err.message}`);
  }
}

$("refresh-learning").addEventListener("click", refreshAll);
$("refresh-orders").addEventListener("click", async () => renderOrders(await api("/api/trading/paper/orders")));

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
    }),
  });
  renderAnalysis(data);
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
