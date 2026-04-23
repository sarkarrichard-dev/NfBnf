const logEl = document.getElementById("log");
const statusEl = document.getElementById("ws-status");
const findingsEl = document.getElementById("findings");
const learningBody = document.querySelector("#learning-table tbody");

function log(line) {
  const ts = new Date().toISOString().slice(11, 19);
  logEl.textContent = `[${ts}] ${line}\n` + logEl.textContent;
}

function setStatus(connected) {
  statusEl.textContent = connected ? "WS: connected" : "WS: disconnected";
  statusEl.classList.toggle("connected", connected);
  statusEl.classList.toggle("disconnected", !connected);
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

  const fb = document.createElement("div");
  fb.className = "feedback";
  const hint = document.createElement("span");
  hint.style.color = "#9aa7b5";
  hint.style.fontSize = "0.85rem";
  hint.textContent = "Feedback (feeds learning):";
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

  card.append(header, metrics, summary, fb);
  findingsEl.prepend(card);
}

const proto = window.location.protocol === "https:" ? "wss" : "ws";
const wsUrl = `${proto}://${window.location.host}/ws`;
let ws;

function connect() {
  ws = new WebSocket(wsUrl);
  ws.addEventListener("open", () => {
    setStatus(true);
    log("socket open");
    ws.send(JSON.stringify({ type: "learning_state" }));
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
    if (msg.type === "status") log(msg.message || "status");
    if (msg.type === "error") log(`ERROR: ${msg.message}`);
    if (msg.type === "finding") appendFinding(msg);
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

connect();
