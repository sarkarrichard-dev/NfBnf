const logEl = document.getElementById("log");
const statusEl = document.getElementById("ws-status");
const findingsEl = document.getElementById("findings");
const learningBody = document.querySelector("#learning-table tbody");
const briefingEl = document.getElementById("briefing-text");
const istLiveEl = document.getElementById("ist-live");
const sessionPillEl = document.getElementById("session-pill");
const watchlistUl = document.getElementById("watchlist-ul");

let lastBriefText = "";

function log(line) {
  const ts = new Date().toISOString().slice(11, 19);
  logEl.textContent = `[${ts}] ${line}\n` + logEl.textContent;
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
  sessionPillEl.textContent = `Session: ${india.phase} — ${india.label || ""}`;
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

function appendFinding(msg) {
  const card = document.createElement("article");
  card.className = "finding";

  const header = document.createElement("header");
  const title = document.createElement("strong");
  title.textContent = msg.symbol || "";
  const bias = document.createElement("span");
  bias.className = "bias";
  bias.textContent = `bias ${Number(msg.bias).toFixed(3)} · id ${msg.finding_id || ""}`;
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
      `JARVIS fused: ${b.action} · score ${Number(b.score).toFixed(3)} · conf ${Number(b.confidence).toFixed(2)} · ${b.agreement}`,
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

  const stack = [header, metrics, summary];
  if (brainEl) stack.push(brainEl);
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
    log("JARVIS link established");
    ws.send(JSON.stringify({ type: "learning_state" }));
    ws.send(JSON.stringify({ type: "watchlist_list" }));
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
  ws.send(JSON.stringify({ type: "analyze", symbol, period, use_llm }));
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
  const force = document.getElementById("sweep-force").checked;
  ws.send(JSON.stringify({ type: "sweep", period, use_llm, force }));
});

setInterval(tickIST, 1000);
tickIST();

connect();
