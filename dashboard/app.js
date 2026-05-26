async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let msg = `${path} → ${response.status} ${response.statusText}`;
    try {
      const j = await response.clone().json();
      if (j?.detail != null) {
        msg += ` | ${typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail)}`;
      } else if (j?.error) {
        msg += ` | ${j.error}`;
      }
    } catch {
      const text = (await response.clone().text()).trim();
      if (text && text.length < 800) msg += ` | ${text}`;
    }
    throw new Error(msg);
  }
  return response.json();
}

let analyticsData = null;
let activePeriod = "today";
let autoPollTimer = null;
let mtmPollTimer = null;

function $(id) {
  return document.getElementById(id);
}

function write(id, value) {
  const el = $(id);
  if (!el) return;
  el.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtNum(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}

function fmtPnl(value) {
  if (value == null) return "—";
  const n = Number(value);
  const sign = n >= 0 ? "+" : "";
  return `${sign}₹${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function fmtIst(iso) {
  if (!iso) return "—";
  if (String(iso).includes("IST")) return String(iso);
  try {
    const d = new Date(String(iso).replace("Z", "+00:00"));
    const text = d.toLocaleString("en-IN", {
      timeZone: "Asia/Kolkata",
      dateStyle: "medium",
      timeStyle: "medium",
      hour12: true,
    });
    return `${text} IST`;
  } catch {
    return iso;
  }
}

function tradeTime(t) {
  return t?.created_at_ist || fmtIst(t?.created_at);
}

async function detectWrongServerOnPort() {
  try {
    const health = await fetch("/api/health");
    if (!health.ok) return null;
    const body = await health.json();
    if (body.app === "index-options-ai") return null;
    if (body.app) {
      return `Port 8000 is running "${body.app}", not Index Options AI. Use Start Index Options AI.cmd.`;
    }
    return "Port 8000 is running the old Trading Workstation. Use Start Index Options AI.cmd.";
  } catch {
    return null;
  }
}

function handleDhanAuthRedirect() {
  const params = new URLSearchParams(window.location.search);
  const auth = params.get("dhan_auth");
  if (!auth) return;
  const detail = params.get("detail") || "";
  const out = $("auth-output");
  if (auth === "success") {
    if (out) out.textContent = "Dhan token saved from OAuth redirect. Charts should work after Verify.";
    loadStatus();
    api("/api/auth/health")
      .then(renderDhanHealth)
      .catch(() => {});
  } else if (out) {
    out.textContent = detail ? `OAuth failed: ${detail}` : "OAuth redirect failed — paste tokenId manually.";
  }
  params.delete("dhan_auth");
  params.delete("detail");
  const qs = params.toString();
  const clean = window.location.pathname + (qs ? `?${qs}` : "");
  window.history.replaceState({}, "", clean);
}

function periodBlock() {
  if (!analyticsData) return null;
  if (activePeriod === "today") return analyticsData.today;
  if (activePeriod === "week") return analyticsData.week;
  if (activePeriod === "month") return analyticsData.month;
  return analyticsData.overview;
}

function tradesForPeriod() {
  if (!analyticsData?.trades?.length) return [];
  if (activePeriod === "all") return analyticsData.trades;
  const block = periodBlock();
  if (!block) return [];
  const instKeys = Object.keys(block.by_instrument || {});
  if (!instKeys.length && block.trades === 0) return [];
  const now = new Date();
  let startMs = 0;
  if (activePeriod === "today") {
    const ist = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
    ist.setHours(0, 0, 0, 0);
    startMs = ist.getTime();
  } else if (activePeriod === "week") {
    const ist = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
    const day = ist.getDay();
    const diff = day === 0 ? 6 : day - 1;
    ist.setDate(ist.getDate() - diff);
    ist.setHours(0, 0, 0, 0);
    startMs = ist.getTime();
  } else if (activePeriod === "month") {
    const ist = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
    ist.setDate(1);
    ist.setHours(0, 0, 0, 0);
    startMs = ist.getTime();
  }
  return analyticsData.trades.filter((t) => {
    const ts = new Date(String(t.created_at).replace("Z", "+00:00")).getTime();
    return ts >= startMs;
  });
}

function renderBreakdownList(elId, data) {
  const el = $(elId);
  if (!el) return;
  const entries = Object.entries(data || {});
  el.innerHTML = entries.length
    ? entries.map(([k, v]) => `<li><span>${escapeHtml(k)}</span><strong>${v}</strong></li>`).join("")
    : '<li class="muted">None in this period</li>';
}

function renderPeriodStats() {
  const openMtm = analyticsData?.open_mtm_rupees;
  const openMtmEl = $("stat-open-mtm");
  if (openMtmEl) {
    openMtmEl.textContent = openMtm != null ? fmtPnl(openMtm) : "—";
    openMtmEl.classList.toggle("pnl-win", (openMtm ?? 0) > 0);
    openMtmEl.classList.toggle("pnl-loss", (openMtm ?? 0) < 0);
  }
  const block = periodBlock() || {
    trades: 0,
    closed: 0,
    open: 0,
    wins: 0,
    losses: 0,
    win_rate: null,
    pnl_rupees: 0,
    by_instrument: {},
    by_action: {},
    by_leg: {},
  };
  $("stat-trades").textContent = String(block.trades ?? 0);
  $("stat-closed-open").textContent = `${block.closed ?? 0} / ${block.open ?? 0}`;
  $("stat-wins-losses").textContent = `${block.wins ?? 0} / ${block.losses ?? 0}`;
  $("stat-win-rate").textContent =
    block.win_rate != null ? `${(block.win_rate * 100).toFixed(1)}%` : "—";
  const pnlEl = $("stat-pnl");
  pnlEl.textContent = fmtPnl(block.pnl_rupees);
  pnlEl.classList.toggle("pnl-win", (block.pnl_rupees ?? 0) > 0);
  pnlEl.classList.toggle("pnl-loss", (block.pnl_rupees ?? 0) < 0);
  renderBreakdownList("breakdown-instrument", block.by_instrument);
  renderBreakdownList("breakdown-action", block.by_action);
  renderBreakdownList("breakdown-leg", block.by_leg);
}

function legBadges(t) {
  const tx = (t.transaction_type || "BUY").toUpperCase();
  const side = (t.option_side || "").toUpperCase();
  const txCls = tx === "SELL" ? "leg-sell" : "leg-buy";
  const optCls = side === "CE" ? "leg-ce" : side === "PE" ? "leg-pe" : "";
  const parts = [];
  parts.push(`<span class="leg-pill ${txCls}">${tx === "SELL" ? "Sell" : "Buy"}</span>`);
  if (t.strike_display) {
    parts.push(`<span class="leg-pill leg-strike">${escapeHtml(t.strike_display)}</span>`);
  }
  if (side) {
    parts.push(`<span class="leg-pill ${optCls}">${side}</span>`);
  }
  return parts.join(" ");
}

function formatSpreadLegRows(t) {
  const legs = t.legs_detail || [];
  if (!legs.length) return "";
  return `<div class="spread-legs">${legs
    .map((leg) => {
      const tx = (leg.transaction_type || "BUY").toUpperCase();
      const txCls = tx === "SELL" ? "leg-sell" : "leg-buy";
      const side = (leg.option_type || "").toUpperCase();
      const optCls = side === "CALL" ? "leg-ce" : side === "PUT" ? "leg-pe" : "";
      const strike = leg.strike != null ? String(leg.strike) : "";
      return `<div class="spread-leg-row">
        <span class="leg-pill ${txCls}">${tx === "SELL" ? "Sell" : "Buy"}</span>
        ${strike ? `<span class="leg-pill leg-strike">${escapeHtml(strike)}</span>` : ""}
        ${side ? `<span class="leg-pill ${optCls}">${side}</span>` : ""}
      </div>`;
    })
    .join("")}</div>`;
}

function formatPositionCell(t) {
  const spreadRows = formatSpreadLegRows(t);
  const risk = t.credit_risk_label
    ? `<div class="muted credit-risk">${escapeHtml(t.credit_risk_label)}</div>`
    : "";
  const expiry = t.expiry ? `<div class="muted leg-expiry">Exp ${escapeHtml(String(t.expiry))}</div>` : "";
  const qty = t.lot_label
    ? `<div class="muted">${escapeHtml(t.lot_label)}</div>`
    : t.quantity
      ? `<div class="muted">${Number(t.quantity)} qty</div>`
      : "";
  if (spreadRows) {
    return `<div class="leg-cell">${spreadRows}${risk}${qty}${expiry}</div>`;
  }
  const badges = legBadges(t);
  const line = t.leg_display || t.side_label || t.action || "—";
  return `<div class="leg-cell">${badges || `<strong>${escapeHtml(line)}</strong>`}${risk}${qty}${expiry}</div>`;
}

function formatExitPremium(t) {
  const price =
    t.current_option_ltp ?? t.exit_option_ltp ?? t.last_option_ltp ?? null;
  if (price == null || Number.isNaN(Number(price))) return "—";
  const label = t.is_open ? " live" : "";
  const prefix = (t.legs_detail || []).length ? "close " : "";
  return `${prefix}₹${fmtNum(price)}${label}`;
}

function renderMtmSparkline(history) {
  if (!history?.length) return "";
  const pts = history.slice(-8).map((h) => Number(h.mtm_pnl));
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const range = max - min || 1;
  const bars = pts
    .map((v) => {
      const h = Math.max(4, Math.round(((v - min) / range) * 20) + 4);
      const cls = v >= 0 ? "spark-win" : "spark-loss";
      return `<span class="spark-bar ${cls}" style="height:${h}px" title="${fmtPnl(v)}"></span>`;
    })
    .join("");
  return `<span class="mtm-spark">${bars}</span>`;
}

function renderAnalyticsTrades() {
  const tbody = $("analytics-trades-body");
  if (!tbody) return;
  const rows = tradesForPeriod();
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="muted">No trades in this period.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows
    .map((t) => {
      const entryParts = [
        t.entry_index_price != null ? `idx ${fmtNum(t.entry_index_price)}` : null,
      ];
      if ((t.legs_detail || []).length && t.net_credit_points != null) {
        entryParts.push(`credit ₹${fmtNum(t.net_credit_points)}`);
      } else if (t.entry_option_ltp != null) {
        entryParts.push(`prem ₹${fmtNum(t.entry_option_ltp)}`);
      }
      const entry = entryParts.filter(Boolean).join(" · ");
      const exitOpt = formatExitPremium(t);
      let pnlCell = '<span class="muted">—</span>';
      if (t.pnl != null) {
        pnlCell = `<span class="${Number(t.pnl) >= 0 ? "pnl-win" : "pnl-loss"}">${fmtPnl(t.pnl)}</span>`;
      } else if (t.mtm_pnl != null) {
        pnlCell = `<span class="mtm-live ${Number(t.mtm_pnl) >= 0 ? "pnl-win" : "pnl-loss"}">${fmtPnl(t.mtm_pnl)}</span>`;
      }
      const spark = t.is_open ? renderMtmSparkline(t.mtm_history) : "";
      const mtmNote =
        t.is_open && t.mtm_updated_at_ist
          ? `<div class="mtm-note muted">${escapeHtml(t.mtm_updated_at_ist)}</div>`
          : "";
      const conf = t.confidence != null ? `${(Number(t.confidence) * 100).toFixed(0)}%` : "—";
      const rowCls = t.is_open ? "row-open" : "";
      return `<tr class="${rowCls}">
        <td>${tradeTime(t)}</td>
        <td>${escapeHtml(t.instrument || "")}</td>
        <td>${formatPositionCell(t)}</td>
        <td><span class="muted">${escapeHtml(t.action || "")}</span><div class="muted">${conf}</div></td>
        <td>${escapeHtml(entry || "—")}</td>
        <td>${escapeHtml(exitOpt)}</td>
        <td>${pnlCell}${spark}${mtmNote}</td>
        <td><span class="muted">${escapeHtml(t.mode || "")}</span> ${escapeHtml(t.status || "")}</td>
      </tr>`;
    })
    .join("");
}

function mergeLiveMtmRows(liveRows) {
  if (!analyticsData?.trades?.length || !liveRows?.length) return;
  const byId = Object.fromEntries(liveRows.map((r) => [r.id, r]));
  analyticsData.trades = analyticsData.trades.map((t) => byId[t.id] || t);
  if (analyticsData.open_mtm_rupees != null) {
    analyticsData.open_mtm_rupees = liveRows.reduce(
      (s, r) => s + (Number(r.mtm_pnl) || 0),
      0,
    );
  }
}

async function refreshLiveMtm() {
  try {
    const data = await api("/api/trades/live-mtm");
    if (data.error) return;
    mergeLiveMtmRows(data.trades || []);
    const label = $("mtm-updated-label");
    if (label) label.textContent = data.updated_at_ist ? `· MTM ${data.updated_at_ist}` : "";
    const openMtmEl = $("stat-open-mtm");
    if (openMtmEl) {
      openMtmEl.textContent = fmtPnl(data.open_mtm_rupees ?? 0);
      openMtmEl.classList.toggle("pnl-win", (data.open_mtm_rupees ?? 0) > 0);
      openMtmEl.classList.toggle("pnl-loss", (data.open_mtm_rupees ?? 0) < 0);
    }
    renderAnalyticsTrades();
  } catch {
    /* Dhan may be offline */
  }
}

function startMtmPolling() {
  if (mtmPollTimer) clearInterval(mtmPollTimer);
  const hasOpen = () => (analyticsData?.trades || []).some((t) => t.is_open);
  mtmPollTimer = setInterval(() => {
    if (hasOpen()) refreshLiveMtm();
  }, 12000);
}

function renderSeriesTable(elId, series) {
  const el = $(elId);
  if (!el) return;
  if (!series?.length) {
    el.innerHTML = '<p class="muted">No history yet.</p>';
    return;
  }
  el.innerHTML = `<table class="mini-table"><thead><tr><th>Period</th><th>Trades</th><th>W/L</th><th>PnL</th></tr></thead><tbody>${series
    .map(
      (s) => `<tr>
      <td>${escapeHtml(s.period)}</td>
      <td>${s.trades}</td>
      <td>${s.wins}/${s.losses}</td>
      <td class="${s.pnl_rupees >= 0 ? "pnl-win" : "pnl-loss"}">${fmtPnl(s.pnl_rupees)}</td>
    </tr>`,
    )
    .join("")}</tbody></table>`;
}

function renderAnalytics() {
  if (!analyticsData) return;
  renderPeriodStats();
  renderAnalyticsTrades();
  renderSeriesTable("series-daily", analyticsData.daily_series);
  renderSeriesTable("series-weekly", analyticsData.weekly_series);
  renderSeriesTable("series-monthly", analyticsData.monthly_series);
}

function renderStrategyTuning(strategy) {
  const grid = $("strategy-tuning-grid");
  const note = $("strategy-tuning-note");
  if (!grid) return;
  if (!strategy) {
    grid.innerHTML = "";
    return;
  }
  if (note && strategy.cpr_width_note) {
    note.textContent = `${strategy.cpr_width_note} ${strategy.strategy_style_note || ""} Edit .env and restart the server to change.`;
  }
  const rows = [
    ["Style", strategy.strategy_style],
    ["Credit enabled", strategy.enable_credit_strategies ? "Yes" : "No"],
    ["CPR narrow %", strategy.cpr_narrow_width_pct],
    ["CPR wide %", strategy.cpr_wide_width_pct],
    ["Credit min conf.", strategy.credit_min_confidence],
    [
      "Credit profit exit",
      strategy.credit_profit_target_pct != null
        ? `${(Number(strategy.credit_profit_target_pct) * 100).toFixed(0)}% of max`
        : "—",
    ],
    [
      "Credit stop exit",
      strategy.credit_stop_loss_pct != null
        ? `${(Number(strategy.credit_stop_loss_pct) * 100).toFixed(0)}% of max loss`
        : "—",
    ],
    ["Wing strikes", strategy.credit_wing_strikes],
    ["Short leg steps", strategy.credit_short_strike_steps],
    ["Supertrend align", strategy.require_supertrend_align ? "Required" : "Off"],
    ["Break Res/Sup required", strategy.require_breakout_tag ? "Yes" : "No"],
    ["Supertrend", `${strategy.supertrend_period} / ${strategy.supertrend_multiplier}`],
  ];
  grid.innerHTML = rows
    .map(
      ([label, val]) =>
        `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(String(val ?? "—"))}</dd></div>`,
    )
    .join("");
}

function renderKillSwitch(ks) {
  const banner = $("kill-switch-banner");
  if (!banner || !ks) return;
  if (ks.active) {
    banner.classList.remove("hidden");
    banner.textContent = `Kill switch active — ${(ks.reasons || []).join(" ")}`;
  } else {
    banner.classList.add("hidden");
    banner.textContent = "";
  }
}

function renderDhanHealth(health) {
  const banner = $("dhan-health-banner");
  if (!banner) return;
  if (!health) {
    banner.classList.add("hidden");
    return;
  }
  if (health.ok && health.charts_ok) {
    banner.classList.remove("hidden");
    banner.classList.add("ok");
    const validity = health.token_validity ? ` Token valid until ${health.token_validity} IST.` : "";
    banner.textContent = `Dhan data access OK (plan: ${health.data_plan || "Active"}).${validity}`;
    return;
  }
  banner.classList.remove("ok");
  banner.classList.remove("hidden");
  const parts = [...(health.issues || []), ...(health.actions || [])];
  banner.textContent = parts.join(" ");
}

function formatHealthResult(health) {
  if (!health) return "No health data.";
  const lines = [];
  if (health.ok && health.charts_ok) lines.push("Dhan OK — charts and profile working.");
  else lines.push("Dhan needs attention:");
  for (const issue of health.issues || []) lines.push(`• ${issue}`);
  for (const action of health.actions || []) lines.push(`→ ${action}`);
  if (health.data_plan) lines.push(`Data plan: ${health.data_plan}`);
  if (health.token_validity) lines.push(`Token validity: ${health.token_validity}`);
  return lines.join("\n");
}

async function loadAnalytics() {
  analyticsData = await api("/api/analytics");
  renderAnalytics();
}

function actionClass(action) {
  if (action === "BUY_CALL") return "heat-bull";
  if (action === "BUY_PUT") return "heat-bear";
  if (action === "ERROR") return "heat-error";
  return "heat-neutral";
}

function renderHeatmap(data) {
  const grid = $("heatmap-grid");
  if (!grid) return;
  if (data?.error) {
    grid.innerHTML = `<p class="muted">${escapeHtml(data.error)}</p>`;
    return;
  }
  const cells = data?.cells || [];
  if (!cells.length) {
    grid.innerHTML = '<p class="muted">No heatmap data.</p>';
    return;
  }
  const summary = data.summary || {};
  grid.innerHTML = `
    <p class="heatmap-summary muted">${summary.executable ?? 0} executable · ${summary.credit_signals ?? 0} credit · ${summary.bullish_signals ?? 0} buy call · ${summary.bearish_signals ?? 0} buy put</p>
    <div class="heatmap-cells">
      ${cells
        .map((c) => {
          const heat = Math.round((c.heat ?? 0) * 100);
          const cls = actionClass(c.action);
          const allowed = c.plan_allowed ? "ready" : "blocked";
          return `<article class="heat-cell ${cls} ${allowed}" style="--heat:${heat}%">
            <header>${escapeHtml(c.instrument || "")}</header>
            <strong class="heat-action">${escapeHtml(c.action || "—")}</strong>
            <span class="heat-meta">${escapeHtml(c.cpr_regime || c.cpr_width_class || "")} · ${escapeHtml(c.cpr_position || "")} · ${escapeHtml(c.ema_bias || "")}${c.structure ? ` · ${escapeHtml(c.structure)}` : ""}</span>
            <span class="heat-conf">${c.confidence != null ? `${(c.confidence * 100).toFixed(0)}% conf` : ""}</span>
            <span class="heat-meta">${c.pcr != null ? `PCR ${Number(c.pcr).toFixed(2)} · ${escapeHtml(c.oi_bias || "")}` : ""}</span>
            <span class="heat-plan muted">${c.plan_allowed ? "Plan OK" : escapeHtml(c.plan_reason || c.error || "—")}</span>
          </article>`;
        })
        .join("")}
    </div>`;
}

async function loadHeatmap() {
  try {
    renderHeatmap(await api("/api/heatmap"));
  } catch (err) {
    renderHeatmap({ error: err.message });
  }
}

function renderLearning(data) {
  const learned = data?.learned || {};
  const expl = $("learning-explanation");
  if (expl) expl.textContent = learned.explanation || "No learning data yet.";
  const eff = learned.effective_min_confidence;
  $("learn-effective-conf").textContent =
    eff != null ? `${(eff * 100).toFixed(0)}%` : "—";
  const adj = learned.min_confidence_adjustment;
  $("learn-adjustment").textContent =
    adj != null ? `${adj >= 0 ? "+" : ""}${(adj * 100).toFixed(0)}%` : "—";
  const wr = learned.trade_win_rate;
  $("learn-win-rate").textContent =
    wr != null ? `${(wr * 100).toFixed(1)}%` : "—";
  const ml = learned.ml || {};
  const mlVer = $("learn-ml-version");
  if (mlVer) {
    mlVer.textContent = ml.ready
      ? `v${ml.version || "?"}`
      : ml.status === "collecting_data"
        ? "Collecting data"
        : "Not trained";
    mlVer.classList.toggle("ml-ready", !!ml.ready);
  }
  const mlAcc = $("learn-ml-accuracy");
  if (mlAcc) {
    mlAcc.textContent =
      ml.holdout_accuracy != null ? `${(ml.holdout_accuracy * 100).toFixed(0)}%` : "—";
  }
  const mlGate = $("learn-ml-gate");
  if (mlGate) {
    const g = ml.min_win_prob_gate ?? learned.ml_min_win_prob;
    mlGate.textContent = g != null ? `${(g * 100).toFixed(0)}%` : "—";
  }
  const mlMsg = $("learning-ml-message");
  if (mlMsg) {
    mlMsg.textContent = ml.message || (ml.ready ? "ML active — auto-retrains when trades close." : "");
  }
  const hf = learned.hf || {};
  const hfSt = $("learn-hf-status");
  if (hfSt) {
    hfSt.textContent = hf.ready
      ? "Active"
      : hf.token_configured
        ? "Error"
        : "No token";
    hfSt.classList.toggle("ml-ready", !!hf.ready);
  }
  const hfRows = $("learn-hf-rows");
  if (hfRows) hfRows.textContent = hf.dataset_rows != null ? String(hf.dataset_rows) : "—";
  const hfModel = $("learn-hf-model");
  if (hfModel) hfModel.textContent = hf.model || "—";
  const hfMsg = $("learning-hf-message");
  if (hfMsg) {
    hfMsg.textContent =
      hf.message ||
      (hf.hub_repo ? `Hub repo: ${hf.hub_repo}` : "") ||
      "";
  }
  const list = $("learning-feedback");
  if (!list) return;
  const rows = data?.recent_feedback || [];
  list.innerHTML = rows.length
    ? rows
        .map(
          (f) =>
            `<li><span>${f.rating > 0 ? "+" : f.rating < 0 ? "−" : "0"}</span> ${escapeHtml(f.note_short || f.note || f.trade_id || "feedback")} <time class="muted">${f.created_at_ist || fmtIst(f.created_at)}</time></li>`,
        )
        .join("")
    : '<li class="muted">Log trade PnL below to feed learning.</li>';
}

async function loadLearning() {
  try {
    renderLearning(await api("/api/learning"));
  } catch (err) {
    $("learning-explanation").textContent = err.message;
  }
}

function renderMarket(mkt) {
  const el = $("market-status-line");
  if (!el || !mkt) return;
  el.textContent = mkt.message || mkt.now_ist || "—";
  el.classList.toggle("market-open", !!mkt.is_open);
  el.classList.toggle("market-closed", !mkt.is_open);
}

function renderAutoStatus(auto) {
  if (!auto) return;
  const running = !!auto.running;
  $("auto-start").disabled = running;
  $("auto-stop").disabled = !running;
  if (auto.market) renderMarket(auto.market);
  const line = $("auto-status-line");
  if (line) {
    const mkt = auto.market;
    const parts = [
      running ? "Scanner running" : "Scanner stopped",
      mkt?.is_open ? "Market open" : mkt?.phase === "square_off" ? "Square-off" : "Market closed",
      auto.cycles != null ? `${auto.cycles} cycles` : null,
      auto.executions != null ? `${auto.executions} auto executions` : null,
      auto.open_trades != null ? `${auto.open_trades} open` : null,
    ].filter(Boolean);
    if (auto.auth_blocked) parts.push("Dhan token expired — re-login");
    if (auto.last_error) parts.push(`⚠ ${auto.last_error}`);
    if (auto.indices_skipped?.length) {
      parts.push(`Skipped: ${auto.indices_skipped.join(", ")} (set .env ids)`);
    }
    if (auto.kill_switch?.active) parts.push("Kill switch active");
    line.textContent = parts.join(" · ");
    line.classList.toggle(
      "error",
      !!auto.auth_blocked || !!auto.last_error || !!auto.kill_switch?.active,
    );
  }
  const log = $("scanner-log");
  if (log) {
    const events = (auto.events || []).slice(0, 25);
    log.textContent = events.length
      ? events
          .map((e) => {
            const bits = [
              e.at_ist || fmtIst(e.at),
              e.event,
              e.instrument,
              e.action,
              e.trade_id,
              e.pnl != null ? `PnL ${fmtPnl(e.pnl)}` : null,
              e.error,
              e.reason,
              e.message,
            ]
              .filter(Boolean);
            return bits.join(" · ");
          })
          .join("\n")
      : "No scanner events yet.";
  }
}

async function loadAutoStatus() {
  try {
    renderAutoStatus(await api("/api/auto/status"));
  } catch (err) {
    $("auto-status-line").textContent = err.message;
  }
}

function startAutoPolling() {
  if (autoPollTimer) clearInterval(autoPollTimer);
  autoPollTimer = setInterval(async () => {
    await loadAutoStatus();
    await loadAnalytics();
  }, 15000);
}

async function loadStatus() {
  const status = await api("/api/status");
  const tok = status.dhan_token;
  const dh = status.dhan_health;
  const dhanEl = $("dhan-status");
  dhanEl?.classList.remove("error");
  if (!status.dhan_ready) {
    dhanEl.textContent = "Need token";
  } else if (dh && !dh.ok) {
    dhanEl.textContent = "Token rejected";
    dhanEl.classList.add("error");
  } else if (dh?.ok && dh?.charts_ok) {
    dhanEl.textContent = dh.token_validity
      ? `OK until ${String(dh.token_validity).replace(" IST", "")}`
      : "Dhan OK";
  } else if (tok?.expired) {
    dhanEl.textContent = "Expired";
    dhanEl.classList.add("error");
  } else if (tok?.expires_ist) {
    dhanEl.textContent = `JWT ${tok.expires_ist.replace(" IST", "")} — verify`;
  } else {
    dhanEl.textContent = "Configured";
  }
  const live = status.trading_mode === "LIVE";
  $("toggle-trading-mode").checked = live;
  const pill = $("mode-pill");
  const ks = status.kill_switch;
  renderKillSwitch(ks);
  if (ks?.active) {
    pill.textContent = "Kill switch";
    pill.classList.add("error");
  } else if (live && status.dhan_ready) {
    pill.textContent = "Live trading";
    pill.classList.remove("error");
  } else if (live) {
    pill.textContent = "Live (need Dhan)";
    pill.classList.remove("error");
  } else {
    pill.textContent = "Paper";
    pill.classList.remove("error");
  }
  if (status.market) renderMarket(status.market);
  if (status.strategy) renderStrategyTuning(status.strategy);
  if (status.auto) renderAutoStatus(status.auto);
  if (status.learned) renderLearning({ learned: status.learned, recent_feedback: [] });
  renderDhanHealth(status.dhan_health);
}

async function setTradingMode(live) {
  const mode = live ? "LIVE" : "PAPER";
  if (
    live &&
    !window.confirm(
      "Switch to LIVE? The auto scanner will send real broker MARKET orders when signals pass gates.",
    )
  ) {
    $("toggle-trading-mode").checked = false;
    return;
  }
  try {
    await api("/api/trading/mode", {
      method: "POST",
      body: JSON.stringify({ mode }),
    });
    await loadStatus();
  } catch (err) {
    alert(err.message);
    $("toggle-trading-mode").checked = !live;
  }
}

function formatAuthResult(result) {
  if (!result || typeof result !== "object") return String(result ?? "");
  if (result.error) return String(result.error);
  const lines = [];
  if (result.message) lines.push(result.message);
  if (result.login_url) lines.push(`Login URL:\n${result.login_url}`);
  if (result.expiryTime) lines.push(`Expires: ${result.expiryTime}`);
  return lines.join("\n\n") || JSON.stringify(result, null, 2);
}

function setBootError(message) {
  $("mode-pill").textContent = "API offline";
  $("mode-pill").classList.add("error");
  write("auth-output", message);
  const tbody = $("analytics-trades-body");
  if (tbody) tbody.innerHTML = `<tr><td colspan="8">${escapeHtml(message)}</td></tr>`;
}

document.querySelectorAll(".period-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".period-tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    activePeriod = btn.dataset.period;
    renderAnalytics();
  });
});

$("toggle-trading-mode")?.addEventListener("change", (e) => {
  setTradingMode(e.target.checked);
});

$("create-consent")?.addEventListener("click", async () => {
  write("auth-output", "Creating login link…");
  try {
    const result = await api("/api/auth/generate-consent", { method: "POST" });
    write("auth-output", formatAuthResult(result));
    if (result.login_url) window.open(result.login_url, "_blank", "noopener");
  } catch (err) {
    write("auth-output", err.message);
  }
});

$("save-token")?.addEventListener("click", async () => {
  const tokenId = $("token-id").value.trim();
  if (!tokenId) {
    write("auth-output", "Paste token from Dhan redirect.");
    return;
  }
  try {
    const result = await api("/api/auth/consume-consent", {
      method: "POST",
      body: JSON.stringify({ token_id: tokenId }),
    });
    write("auth-output", formatAuthResult(result) + (result.health ? `\n\n${formatHealthResult(result.health)}` : ""));
    renderDhanHealth(result.health);
    await loadStatus();
    await loadHeatmap();
  } catch (err) {
    write("auth-output", err.message);
  }
});

$("check-dhan-setup")?.addEventListener("click", async () => {
  try {
    write("auth-output", formatAuthResult(await api("/api/auth/setup")));
    await loadStatus();
  } catch (err) {
    write("auth-output", err.message);
  }
});

$("verify-dhan-health")?.addEventListener("click", async () => {
  const pasted = $("token-id")?.value?.trim() || "";
  write("auth-output", pasted ? "Saving pasted token and checking access…" : "Checking profile and chart access…");
  try {
    const health = await api("/api/auth/health", {
      method: "POST",
      body: JSON.stringify(pasted ? { token_id: pasted } : {}),
    });
    write("auth-output", formatHealthResult(health));
    renderDhanHealth(health);
    await loadHeatmap();
    await loadStatus();
  } catch (err) {
    write("auth-output", err.message);
  }
});

$("renew-dhan-token")?.addEventListener("click", async () => {
  write("auth-output", "Renewing token…");
  try {
    const result = await api("/api/auth/renew-token", { method: "POST" });
    write("auth-output", formatAuthResult(result) + "\n\n" + formatHealthResult(result.health));
    renderDhanHealth(result.health);
    await loadStatus();
    await loadHeatmap();
  } catch (err) {
    write("auth-output", err.message);
  }
});

$("auto-start")?.addEventListener("click", async () => {
  const pasted = $("token-id")?.value?.trim() || "";
  $("auto-status-line").textContent = "Starting scanner…";
  try {
    renderAutoStatus(
      await api("/api/auto/start", {
        method: "POST",
        body: JSON.stringify(pasted ? { token_id: pasted } : {}),
      }),
    );
    startAutoPolling();
    await loadHeatmap();
  } catch (err) {
    $("auto-status-line").textContent = err.message;
    write(
      "auth-output",
      `${err.message}\n\nUse Method A: Dhan Web → Generate Access Token → paste eyJ… → Save Token → Verify data access.`,
    );
  }
});

$("auto-stop")?.addEventListener("click", async () => {
  try {
    renderAutoStatus(await api("/api/auto/stop", { method: "POST" }));
  } catch (err) {
    $("auto-status-line").textContent = err.message;
  }
});

$("refresh-heatmap")?.addEventListener("click", () => loadHeatmap());
$("refresh-learning")?.addEventListener("click", () => loadLearning());

$("hf-sync")?.addEventListener("click", async () => {
  try {
    const result = await api("/api/learning/hf-sync", { method: "POST" });
    renderLearning(result.learning || result);
    if ($("learning-hf-message") && result.hf?.message) {
      $("learning-hf-message").textContent = result.hf.message;
    }
  } catch (err) {
    $("learning-hf-message").textContent = err.message;
  }
});

$("hf-upload")?.addEventListener("click", async () => {
  if (!window.confirm("Upload local outcomes.jsonl to your Hugging Face dataset repo?")) return;
  try {
    const result = await api("/api/learning/hf-upload", { method: "POST" });
    renderLearning(result.learning || result);
    const msg = result.upload?.message || result.upload?.detail || "Upload finished.";
    $("learning-hf-message").textContent = msg;
  } catch (err) {
    $("learning-hf-message").textContent = err.message;
  }
});

$("retrain-ml")?.addEventListener("click", async () => {
  const btn = $("retrain-ml");
  if (btn) btn.disabled = true;
  try {
    const result = await api("/api/learning/retrain-ml", { method: "POST" });
    renderLearning(result.learning || result);
    if (result.ml?.message) {
      const mlMsg = $("learning-ml-message");
      if (mlMsg) mlMsg.textContent = result.ml.message;
    }
  } catch (err) {
    $("learning-ml-message").textContent = err.message;
  } finally {
    if (btn) btn.disabled = false;
  }
});

$("cleanup-learning")?.addEventListener("click", async () => {
  if (!window.confirm("Remove test/automation feedback from learning? Real trades are kept.")) return;
  try {
    const result = await api("/api/learning/cleanup", { method: "POST" });
    renderLearning(result.learning || result);
    const extra =
      result.removed?.feedback_removed > 0
        ? ` Removed ${result.removed.feedback_removed} test feedback row(s).`
        : "";
    const expl = $("learning-explanation");
    if (expl && result.learning?.learned?.explanation) {
      expl.textContent = result.learning.learned.explanation + extra;
    }
    await loadStatus();
  } catch (err) {
    $("learning-explanation").textContent = err.message;
  }
});

$("close-trade-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const tradeId = $("close-trade-id").value.trim();
  const pnlRaw = $("close-trade-pnl").value.trim();
  if (!tradeId || !pnlRaw) return;
  try {
    await api("/api/outcome", {
      method: "POST",
      body: JSON.stringify({ trade_id: tradeId, pnl: Number(pnlRaw) }),
    });
    $("close-trade-output").textContent = "Saved";
    await loadAnalytics();
    await loadStatus();
    await loadLearning();
  } catch (err) {
    $("close-trade-output").textContent = err.message;
  }
});

async function bootDashboard() {
  handleDhanAuthRedirect();
  const wrong = await detectWrongServerOnPort();
  if (wrong) {
    setBootError(wrong);
    return;
  }
  try {
    await loadStatus();
    await loadAnalytics();
    startMtmPolling();
    await refreshLiveMtm();
    await loadLearning();
    try {
      const health = await api("/api/auth/health");
      renderDhanHealth(health);
    } catch {
      /* optional on boot */
    }
    await loadHeatmap();
    await loadAutoStatus();
    startAutoPolling();
  } catch (err) {
    setBootError(String(err.message || err));
  }
}

bootDashboard();
