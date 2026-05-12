/**
 * TradingView Lightweight Charts v4 — OHLC from /api/brain/analyze payload (`chart.bars`).
 */
(function () {
  const state = {
    chart: null,
    candle: null,
    line: null,
    vol: null,
    raw: [],
    mode: "candles",
  };

  function dispose() {
    if (state.chart) {
      state.chart.remove();
      state.chart = null;
      state.candle = null;
      state.line = null;
      state.vol = null;
    }
  }

  function mapCandles(bars) {
    return bars.map((b) => ({
      time: b.time,
      open: b.open,
      high: b.high,
      low: b.low,
      close: b.close,
    }));
  }

  function mapVol(bars) {
    return bars.map((b) => ({
      time: b.time,
      value: b.volume,
      color: b.close >= b.open ? "rgba(0,245,212,0.45)" : "rgba(255,92,108,0.45)",
    }));
  }

  function mapLine(bars) {
    return bars.map((b) => ({ time: b.time, value: b.close }));
  }

  function chartLayout() {
    return {
      background: { type: "solid", color: "#0b1018" },
      textColor: "#8fa6b8",
    };
  }

  function buildChart(mode) {
    const el = document.getElementById("chart-mount");
    if (!el || !globalThis.LightweightCharts) return;
    dispose();
    const LW = globalThis.LightweightCharts;
    const w = Math.max(280, el.clientWidth || el.offsetWidth || 600);
    const chart = LW.createChart(el, {
      width: w,
      height: 380,
      layout: chartLayout(),
      grid: {
        vertLines: { color: "rgba(30,42,58,0.45)" },
        horzLines: { color: "rgba(30,42,58,0.45)" },
      },
      rightPriceScale: { borderColor: "#2a3f5c" },
      timeScale: { borderColor: "#2a3f5c", timeVisible: true, secondsVisible: false },
      crosshair: { mode: LW.CrosshairMode.Normal },
    });
    state.chart = chart;
    const bars = state.raw;
    if (!bars.length) return;

    if (mode === "line") {
      state.line = chart.addLineSeries({
        color: "#00f5d4",
        lineWidth: 2,
        priceLineVisible: false,
      });
      state.line.setData(mapLine(bars));
      chart.timeScale().fitContent();
      return;
    }

    if (mode === "volume") {
      state.vol = chart.addHistogramSeries({
        priceFormat: { type: "volume" },
        priceScaleId: "",
        scaleMargins: { top: 0.05, bottom: 0 },
      });
      state.vol.setData(mapVol(bars));
      chart.timeScale().fitContent();
      return;
    }

    state.vol = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "",
      scaleMargins: { top: 0.78, bottom: 0 },
    });
    state.vol.setData(mapVol(bars));
    state.candle = chart.addCandlestickSeries({
      upColor: "#00f5d4",
      downColor: "#ff5c6c",
      borderVisible: false,
      wickUpColor: "#6feedb",
      wickDownColor: "#ff8a96",
      priceScaleId: "right",
      scaleMargins: { top: 0.06, bottom: 0.22 },
    });
    state.candle.setData(mapCandles(bars));
    chart.timeScale().fitContent();
  }

  function setMode(mode) {
    state.mode = mode;
    buildChart(mode);
    document.querySelectorAll(".chart-toggle").forEach((btn) => {
      btn.classList.toggle("active", btn.getAttribute("data-chart-mode") === mode);
    });
  }

  function onResize() {
    if (!state.chart) return;
    const el = document.getElementById("chart-mount");
    if (!el) return;
    state.chart.applyOptions({ width: Math.max(280, el.clientWidth || 600) });
  }

  const api = {
    renderFromAnalysis(data) {
      const hint = document.getElementById("chart-hint");
      const pack = data && data.chart;
      if (!pack || !Array.isArray(pack.bars) || !pack.bars.length) {
        dispose();
        if (hint) hint.textContent = "No chart data — run Analyze or check server response.";
        return;
      }
      state.raw = pack.bars;
      if (hint) {
        hint.textContent = `Loaded ${pack.count} bars · ${data.symbol || ""} · ${(data.metrics && data.metrics.ohlc_interval) || ""}`;
      }
      setMode(state.mode === "line" || state.mode === "volume" ? state.mode : "candles");
    },

    wireToolbar() {
      document.querySelectorAll(".chart-toggle").forEach((btn) => {
        btn.addEventListener("click", () => {
          const m = btn.getAttribute("data-chart-mode");
          if (m) setMode(m);
        });
      });
      window.addEventListener("resize", onResize);
    },
  };

  globalThis.TAWSChart = api;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => api.wireToolbar());
  } else {
    api.wireToolbar();
  }
})();
