const logEl = document.getElementById("log");
const statusEl = document.getElementById("ws-status");
const findingsEl = document.getElementById("findings");
const learningBody = document.querySelector("#learning-table tbody");
const briefingEl = document.getElementById("briefing-text");
const istLiveEl = document.getElementById("ist-live");
const sessionPillEl = document.getElementById("session-pill");
const watchlistUl = document.getElementById("watchlist-ul");
const indexPickerRoot = document.getElementById("index-picker-root");
const catalogDisclaimerEl = document.getElementById("catalog-disclaimer");
const mlDatasetsTextEl = document.getElementById("ml-datasets-text");
const mlAuditCardsEl = document.getElementById("ml-audit-cards");
const backtestTextEl = document.getElementById("backtest-text");
const heatmapSummaryEl = document.getElementById("heatmap-summary");
const heatmapTableWrapEl = document.getElementById("heatmap-table-wrap");
const dhanStatusTextEl = document.getElementById("dhan-status-text");

let lastBriefText = "";
let catalogData = null;

function getCheckedStocks() {
  const out = [];
  document.querySelectorAll("input.catalog-cb:checked").forEach((el) => {
    out.push({
      symbol: el.dataset.symbol || "",
      rank: Number(el.dataset.rank),
      name: el.dataset.name || "",
    });
  });
  return out;
}

function applyCatalogFilter() {
  const q = (document.getElementById("catalog-filter").value || "").trim().toLowerCase();
  document.querySelectorAll("label.picker-stock").forEach((lab) => {
    const hay = (lab.dataset.search || "").toLowerCase();
    lab.classList.toggle("hidden", q.length > 0 && !hay.includes(q));
  });
}

function buildCatalogUI() {
  indexPickerRoot.innerHTML = "";
  if (!catalogData || !catalogData.categories) return;
  catalogDisclaimerEl.textContent = catalogData.disclaimer || "";
  for (const cat of catalogData.categories) {
    const det = document.createElement("details");
    det.className = "cat";
    det.open = true;
    const sum = document.createElement("summary");
    sum.textContent = cat.label;
    det.append(sum);
    for (const idx of cat.indices || []) {
      const idet = document.createElement("details");
      idet.className = "idx";
      idet.open = cat.id === "broad" && idx.id === "nifty50";
      const isum = document.createElement("summary");
      isum.append(document.createTextNode(`${idx.label} `));
      const rn = document.createElement("span");
      rn.className = "rank-note";
      rn.textContent = idx.rank_note || "";
      isum.append(rn);
      idet.append(isum);
      const grid = document.createElement("div");
      grid.className = "picker-stock-grid";
      const stocks = [...(idx.stocks || [])].sort((a, b) => a.rank - b.rank);
      for (const st of stocks) {
        const lab = document.createElement("label");
        lab.className = "picker-stock";
        lab.dataset.search = `${st.symbol} ${st.name}`.toLowerCase();
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.className = "catalog-cb";
        cb.dataset.symbol = st.symbol;
        cb.dataset.rank = String(st.rank);
        cb.dataset.name = st.name || "";
        const span = document.createElement("span");
        const rk = document.createElement("span");
        rk.className = "rank";
        rk.textContent = `#${st.rank}`;
        const nm = document.createTextNode(` ${st.name} `);
        const sy = document.createElement("span");
        sy.className = "mono";
        sy.textContent = `(${st.symbol})`;
        span.append(rk, nm, sy);
        lab.append(cb, span);
        grid.append(lab);
      }
      idet.append(grid);
      det.append(idet);
    }
    indexPickerRoot.append(det);
  }
}

async function loadIndicesCatalog() {
  try {
    const r = await fetch("/api/india/indices-catalog");
    if (!r.ok) throw new Error(String(r.status));
    catalogData = await r.json();
    buildCatalogUI();
    log("index catalog loaded");
  } catch (e) {
    log(`index catalog failed: ${e}`);
    indexPickerRoot.textContent = "Could not load index catalog.";
  }
}

function log(line) {
  const ts = new Date().toISOString().slice(11, 19);
  logEl.textContent = `[${ts}] ${line}\n` + logEl.textContent;
}

function fmtInt(n) {
  return Number(n || 0).toLocaleString();
}

function fmtBytes(n) {
  const bytes = Number(n || 0);
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 2)} ${units[i]}`;
}

function setStatus(connected) {
  statusEl.textContent = connected ? "WS: connected" : "WS: disconnected";
  statusEl.classList.toggle("connected", connected);
  statusEl.classList.toggle("disconnected", !connected);
}

function tickIST() {
  const s = new Date().toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  istLiveEl.textContent = s + " (Asia/Kolkata)";
}

function renderSessionPill(india) {
  if (!india) return;
  sessionPillEl.textContent = `Session: ${india.phase} - ${india.label || ""}`;
  sessionPillEl.classList.remove("unknown", "regular", "pre", "closed", "holiday");
  const ph = india.phase || "";
  if (ph === "regular") sessionPillEl.classList.add("regular");
  else if (ph === "pre_open") sessionPillEl.classList.add("pre");
  else if (ph === "weekend" || ph === "after_hours") sessionPillEl.classList.add("closed");
  else if (ph === "holiday") sessionPillEl.classList.add("holiday");
  else sessionPillEl.classList.add("unknown");
}

function renderBriefing(msg) {
  lastBriefText = msg.text || "";
  briefingEl.textContent = lastBriefText || JSON.stringify(msg.lines || [], null, 2);
  renderSessionPill(msg.india);
}

function renderWatchlist(symbols) {
  watchlistUl.innerHTML = "";
  for (const sym of symbols || []) {
    const li = document.createElement("li");
    li.className = "watchlist-li";
    const span = document.createElement("span");
    span.textContent = sym;
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "wl-rm secondary";
    rm.textContent = "Remove";
    rm.addEventListener("click", () => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "watchlist_remove", symbol: sym }));
      }
    });
    li.append(span, rm);
    watchlistUl.appendChild(li);
  }
}

function speakLastBrief() {
  if (!lastBriefText || !window.speechSynthesis) {
    log("nothing to speak or speech API unavailable");
    return;
  }
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(lastBriefText);
  u.lang = "en-IN";
  u.rate = 1.0;
  window.speechSynthesis.speak(u);
}

function renderMlDatasets(msg) {
  if (!mlDatasetsTextEl) return;
  const n = msg.count ?? 0;
  const rows = msg.datasets || [];
  const summary = msg.summary || {};
  if (mlAuditCardsEl) {
    const cards = [
      ["Files profiled", fmtInt(summary.files ?? n)],
      ["Source size", fmtBytes(summary.bytes)],
      ["Rows sampled", fmtInt(summary.rows_profiled)],
      ["Ingest errors", fmtInt(summary.errors)],
      ["Catalog mode", summary.mode === "profile_catalog" ? "Profiles only" : "Unknown"],
    ];
    mlAuditCardsEl.innerHTML = cards
      .map(([label, value]) => `<div class="audit-card"><span>${label}</span><strong>${value}</strong></div>`)
      .join("");
  }
  if (!n) {
    mlDatasetsTextEl.textContent = "No datasets profiled yet - click Rescan folders.";
    return;
  }
  const previewCap = 80;
  const lines = rows.slice(0, previewCap).map((r) => {
    const err = r.error ? ` ERR: ${r.error}` : "";
    return `${r.rel_path} | ${r.format} | rows~${r.rows_profiled}${err}`;
  });
  const formats = (summary.by_format || [])
    .map((r) => `${r.format}: ${fmtInt(r.files)} files, ${fmtBytes(r.bytes)}, rows~${fmtInt(r.rows_profiled)}`)
    .join("\n");
  const inPayload = rows.length;
  const hiddenInPayload = Math.max(0, inPayload - previewCap);
  const notSent = Math.max(0, n - inPayload);
  const tail = [
    "",
    "What happened to the files:",
    summary.meaning || "Files were profiled into SQLite for audit and digest context.",
    "",
    "By format:",
    formats || "(none)",
    "",
    `Total in database: ${fmtInt(n)}`,
    `Preview: ${Math.min(previewCap, inPayload)} of ${fmtInt(inPayload)} paths loaded in this view.`,
    hiddenInPayload > 0
      ? `(${fmtInt(hiddenInPayload)} more in this batch not shown below.)`
      : "",
    notSent > 0
      ? `(${fmtInt(notSent)} other files in DB - browser list is capped; use nbnf-ml-ingest or SQLite for a full export.)`
      : "",
  ]
    .filter(Boolean)
    .join("\n");
  mlDatasetsTextEl.textContent = `${lines.join("\n")}\n${tail}`;
}

function renderLearning(payload) {
  const rows = payload.signal_stats || [];
  learningBody.innerHTML = "";
  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${r.symbol}</td>
      <td>${r.tag}</td>
      <td>${Number(r.ema).toFixed(4)}</td>
      <td>${r.n}</td>
      <td>${r.updated_at || ""}</td>`;
    learningBody.appendChild(tr);
  }
}

function renderBacktest(msg) {
  if (!backtestTextEl) return;
  const s = msg.summary || {};
  const d = msg.dataset || {};
  const lines = [
    `${msg.symbol} research backtest`,
    `Status: ${s.status || "unknown"}`,
    `Bars: ${fmtInt(s.bars)} | Trades: ${fmtInt(s.trades)} | Win rate: ${((s.win_rate || 0) * 100).toFixed(2)}%`,
    `Ending equity: ${s.ending_equity ?? "-"} | Max drawdown: ${((s.max_drawdown || 0) * 100).toFixed(2)}%`,
    `Avg return/trade: ${((s.avg_return_per_trade || 0) * 100).toFixed(3)}% | Profit factor: ${s.profit_factor ?? "-"}`,
    "",
    "ML-ready dataset:",
    `Rows: ${fmtInt(d.rows)} | Labels: ${JSON.stringify(d.labels || {})}`,
    `Date range: ${d.date_min || "-"} to ${d.date_max || "-"}`,
    "",
    s.warning || "Research only.",
    "",
    "Recent simulated trades:",
    ...(msg.trades || []).slice(-12).map((t) => {
      return `${t.date} | ${t.side} | score=${t.score} | net=${(t.net_return * 100).toFixed(3)}% | ${t.tags.join(",")}`;
    }),
  ];
  backtestTextEl.textContent = lines.join("\n");
}

function heatClass(value, max) {
  if (!value || !max) return "";
  const pct = value / max;
  if (pct > 0.75) return "hot-3";
  if (pct > 0.45) return "hot-2";
  if (pct > 0.2) return "hot-1";
  return "";
}

function renderOptionsHeatmap(msg) {
  if (!heatmapSummaryEl || !heatmapTableWrapEl) return;
  const rows = msg.rows || [];
  const s = msg.summary || {};
  heatmapSummaryEl.textContent = `${msg.underlying} ${msg.trade_date || ""} | strikes=${fmtInt(s.strikes)} | CE OI=${fmtInt(s.total_ce_oi)} | PE OI=${fmtInt(s.total_pe_oi)} | PCR=${s.pcr_oi ?? "-"}`;
  if (!rows.length) {
    heatmapTableWrapEl.innerHTML = "<div class=\"hint\">No local option rows found.</div>";
    return;
  }
  const maxOi = Math.max(
    ...rows.flatMap((r) => [((r.CE || {}).oi || 0), ((r.PE || {}).oi || 0)]),
    0
  );
  const body = rows
    .map((r) => {
      const ce = r.CE || {};
      const pe = r.PE || {};
      return `<tr>
        <td class="${heatClass(ce.oi, maxOi)}">${fmtInt(ce.oi)}</td>
        <td>${ce.close ?? "-"}</td>
        <th>${r.strike}</th>
        <td>${pe.close ?? "-"}</td>
        <td class="${heatClass(pe.oi, maxOi)}">${fmtInt(pe.oi)}</td>
      </tr>`;
    })
    .join("");
  heatmapTableWrapEl.innerHTML = `<table class="heatmap-table">
    <thead><tr><th>CE OI</th><th>CE LTP</th><th>Strike</th><th>PE LTP</th><th>PE OI</th></tr></thead>
    <tbody>${body}</tbody>
  </table>`;
}

function renderDhanStatus(msg) {
  if (!dhanStatusTextEl) return;
  const readiness = msg.readiness || {};
  const feed = msg.feed || {};
  dhanStatusTextEl.textContent = [
    `Mode: ${readiness.mode || "unknown"}`,
    `Data credentials ready: ${readiness.data_ready ? "yes" : "no"}`,
    `Feed URL: ${feed.feed_url || "-"}`,
    `Supports: ${(feed.supports || []).join(", ")}`,
    `Limits: ${JSON.stringify(feed.limits || {})}`,
    "",
    ...(readiness.notes || []),
    "",
    feed.next_step || "",
  ].join("\n");
}

function appendFinding(msg) {
  const card = document.createElement("article");
  card.className = "finding";

  const header = document.createElement("header");
  const title = document.createElement("strong");
  title.textContent = msg.symbol || "";
  const bias = document.createElement("span");
  bias.className = "bias";
  bias.textContent = `bias ${Number(msg.bias).toFixed(3)} | id ${msg.finding_id || ""}`;
  header.append(title, bias);

  const metrics = document.createElement("div");
  metrics.className = "metrics";
  metrics.textContent = JSON.stringify(msg.metrics || {}, null, 2);

  const summary = document.createElement("div");
  summary.className = "summary";
  summary.textContent = msg.summary || "";

  let brainEl = null;
  if (msg.brain) {
    brainEl = document.createElement("div");
    brainEl.className = "brain";
    const b = msg.brain;
    const ml = msg.ml || {};
    const ai = msg.ai || {};
    brainEl.textContent = [
      `QuantTape fused: ${b.action} | score ${Number(b.score).toFixed(3)} | conf ${Number(b.confidence).toFixed(2)} | ${b.agreement}`,
      `ML: regime=${ml.regime} score=${Number(ml.score).toFixed(3)}`,
      `AI: ${ai.stance} conf=${Number(ai.confidence).toFixed(2)} (${ai.version || ""})`,
    ].join("\n");
  }

  const fb = document.createElement("div");
  fb.className = "feedback";
  const hint = document.createElement("span");
  hint.style.color = "#9aa7b5";
  hint.style.fontSize = "0.85rem";
  hint.textContent = "Feedback (trains memory):";
  const mkBtn = (label, rating, cls) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = cls;
    b.dataset.rating = String(rating);
    b.textContent = label;
    b.addEventListener("click", () => {
      ws.send(JSON.stringify({ type: "feedback", finding_id: msg.finding_id, rating }));
    });
    return b;
  };
  fb.append(
    hint,
    mkBtn("Disagree / bearish cue", -1, "bad"),
    mkBtn("Neutral", 0, "meh"),
    mkBtn("Agree / bullish cue", 1, "good"),
  );

  let yahooEl = null;
  if (msg.yahoo_study) {
    yahooEl = document.createElement("pre");
    yahooEl.className = "yahoo-study";
    const ys = msg.yahoo_study;
    yahooEl.textContent = [ys.yahoo_limits || "", "", ys.text_block || ""].join("\n");
  }
  const stack = [header, metrics, summary];
  if (brainEl) stack.push(brainEl);
  if (yahooEl) stack.push(yahooEl);
  stack.push(fb);
  card.append(...stack);
  findingsEl.prepend(card);
}

const proto = window.location.protocol === "https:" ? "wss" : "ws";
const wsUrl = `${proto}://${window.location.host}/ws`;
let ws;

function connect() {
  ws = new WebSocket(wsUrl);
  ws.addEventListener("open", () => {
    setStatus(true);
    log("QuantTape link established");
    ws.send(JSON.stringify({ type: "learning_state" }));
    ws.send(JSON.stringify({ type: "watchlist_list" }));
    ws.send(JSON.stringify({ type: "ml_datasets_list" }));
  });
  ws.addEventListener("close", () => {
    setStatus(false);
    log("socket closed, retrying in 2s");
    setTimeout(connect, 2000);
  });
  ws.addEventListener("error", () => log("socket error"));
  ws.addEventListener("message", (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      log("non-json message");
      return;
    }
    if (msg.type === "hello") log(msg.message || "hello");
    if (msg.type === "briefing") {
      renderBriefing(msg);
      renderWatchlist(msg.watchlist);
      log("sitrep updated");
    }
    if (msg.type === "watchlist") renderWatchlist(msg.symbols);
    if (msg.type === "status") log(msg.message || "status");
    if (msg.type === "error") log(`ERROR: ${msg.message}`);
    if (msg.type === "finding") appendFinding(msg);
    if (msg.type === "sweep_start") {
      log(`sweep start: ${msg.count} symbols (${(msg.symbols || []).join(", ")})`);
    }
    if (msg.type === "sweep_item") appendFinding(msg);
    if (msg.type === "sweep_error") log(`sweep error ${msg.symbol}: ${msg.message}`);
    if (msg.type === "sweep_done") log(`sweep done (${msg.count} symbols)`);
    if (msg.type === "learning_update") renderLearning(msg);
    if (msg.type === "feedback_ack") log(`feedback stored for ${msg.finding_id} (${msg.rating})`);
    if (msg.type === "pong") log("pong");
    if (msg.type === "ml_ingest_done") {
      log(
        `Profile scan: ${msg.processed ?? 0} file(s) from ${(msg.roots || []).join(" | ")}` +
          ((msg.errors || []).length ? ` - ${msg.errors.length} error(s), see server log` : "")
      );
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "ml_datasets_list" }));
      }
    }
    if (msg.type === "ml_datasets") renderMlDatasets(msg);
    if (msg.type === "research_backtest") renderBacktest(msg);
    if (msg.type === "options_heatmap") renderOptionsHeatmap(msg);
    if (msg.type === "dhan_status") renderDhanStatus(msg);
  });
}

document.getElementById("analyze").addEventListener("click", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    log("socket not ready");
    return;
  }
  const symbol = document.getElementById("symbol").value.trim();
  const period = document.getElementById("period").value;
  const use_llm = document.getElementById("use-llm").checked;
  const include_yahoo_deep = document.getElementById("include-yahoo-deep").checked;
  const include_ml_digest = document.getElementById("include-ml-digest").checked;
  ws.send(
    JSON.stringify({ type: "analyze", symbol, period, use_llm, include_yahoo_deep, include_ml_digest })
  );
});

document.getElementById("refresh-learning").addEventListener("click", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "learning_state" }));
});

document.getElementById("sitrep").addEventListener("click", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const use_llm = document.getElementById("use-llm").checked;
  ws.send(JSON.stringify({ type: "brief", use_llm }));
});

document.getElementById("speak-brief").addEventListener("click", speakLastBrief);

document.getElementById("wl-add").addEventListener("click", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const symbol = document.getElementById("wl-symbol").value.trim();
  if (!symbol) return;
  ws.send(JSON.stringify({ type: "watchlist_add", symbol }));
  document.getElementById("wl-symbol").value = "";
});

document.getElementById("wl-refresh").addEventListener("click", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "watchlist_list" }));
});

document.getElementById("sweep-btn").addEventListener("click", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const period = document.getElementById("sweep-period").value;
  const use_llm = document.getElementById("use-llm").checked;
  const include_yahoo_deep = document.getElementById("include-yahoo-deep").checked;
  const include_ml_digest = document.getElementById("include-ml-digest").checked;
  const force = document.getElementById("sweep-force").checked;
  ws.send(
    JSON.stringify({ type: "sweep", period, use_llm, include_yahoo_deep, include_ml_digest, force })
  );
});

document.getElementById("ml-ingest").addEventListener("click", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    log("socket not ready");
    return;
  }
  ws.send(JSON.stringify({ type: "ingest_ml_data" }));
});

document.getElementById("ml-list").addEventListener("click", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "ml_datasets_list" }));
});

document.getElementById("run-backtest").addEventListener("click", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    log("socket not ready");
    return;
  }
  const symbol = document.getElementById("symbol").value.trim();
  const period = document.getElementById("bt-period").value;
  const horizon_bars = Number(document.getElementById("bt-horizon").value);
  ws.send(JSON.stringify({ type: "research_backtest", symbol, period, horizon_bars }));
});

document.getElementById("load-heatmap").addEventListener("click", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    log("socket not ready");
    return;
  }
  const underlying = document.getElementById("heatmap-underlying").value;
  const trade_date = document.getElementById("heatmap-date").value.trim();
  ws.send(JSON.stringify({ type: "options_heatmap", underlying, trade_date }));
});

document.getElementById("dhan-status").addEventListener("click", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    log("socket not ready");
    return;
  }
  ws.send(JSON.stringify({ type: "dhan_status" }));
});

document.getElementById("catalog-filter").addEventListener("input", applyCatalogFilter);
document.getElementById("catalog-clear-checks").addEventListener("click", () => {
  document.querySelectorAll("input.catalog-cb:checked").forEach((c) => {
    c.checked = false;
  });
});
document.getElementById("catalog-add").addEventListener("click", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    log("socket not ready - cannot add to watchlist");
    return;
  }
  const picks = getCheckedStocks();
  if (!picks.length) {
    log("no symbols checked in index picker");
    return;
  }
  for (const p of picks) {
    ws.send(JSON.stringify({ type: "watchlist_add", symbol: p.symbol }));
  }
  log(`watchlist add: ${picks.map((p) => p.symbol).join(", ")}`);
});
document.getElementById("catalog-set-symbol").addEventListener("click", () => {
  const picks = getCheckedStocks();
  if (!picks.length) {
    log("check one or more symbols; best rank (lowest #) fills analyse field");
    return;
  }
  picks.sort((a, b) => a.rank - b.rank);
  document.getElementById("symbol").value = picks[0].symbol;
  log(`analyse symbol -> ${picks[0].symbol} (best rank among checked)`);
});

setInterval(tickIST, 1000);
tickIST();

loadIndicesCatalog();
connect();
