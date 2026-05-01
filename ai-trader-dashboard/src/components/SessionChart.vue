<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from "vue";
import axios from "axios";
import {
  Chart as ChartJS,
  TimeScale,
  LinearScale,
  CategoryScale,
  Tooltip,
  Legend,
  PointElement,
  LineElement,
  LineController,
  ScatterController,
  BarController,
  BarElement,
} from "chart.js";
import {
  CandlestickController,
  CandlestickElement,
} from "chartjs-chart-financial";
import zoomPlugin from "chartjs-plugin-zoom";
import "chartjs-adapter-date-fns";

ChartJS.register(
  TimeScale,
  LinearScale,
  CategoryScale,
  Tooltip,
  Legend,
  PointElement,
  LineElement,
  LineController,
  ScatterController,
  BarController,
  BarElement,
  CandlestickController,
  CandlestickElement,
  zoomPlugin,
);

// v0.2.1 of chartjs-chart-financial uses backgroundColors/borderColors (plural).
CandlestickElement.defaults.backgroundColors = {
  up: "#26c265",
  down: "#ef4444",
  unchanged: "#666",
};
CandlestickElement.defaults.borderColors = {
  up: "#26c265",
  down: "#ef4444",
  unchanged: "#666",
};

const dates = ref([]);
const selectedDate = ref(null);
const bars = ref([]);
const decisions = ref([]);
const orb = ref(null);
const loading = ref(false);
const error = ref(null);
const selectedIndex = ref(null);

const canvasRef = ref(null);
const chartWrapRef = ref(null);
const pnlCanvasRef = ref(null);
const lastUpdated = ref(null);
let chartInstance = null;
let pnlChartInstance = null;
let refreshInterval = null;

const REFRESH_INTERVAL_MS = 60_000;

const isToday = computed(() => {
  if (!selectedDate.value) return false;
  const t = new Date();
  const yyyy = t.getFullYear();
  const mm = String(t.getMonth() + 1).padStart(2, "0");
  const dd = String(t.getDate()).padStart(2, "0");
  return selectedDate.value === `${yyyy}-${mm}-${dd}`;
});

function formatLastUpdated(d) {
  if (!d) return "";
  const h = String(d.getHours()).padStart(2, "0");
  const m = String(d.getMinutes()).padStart(2, "0");
  const s = String(d.getSeconds()).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

// Theme constants — green/red picked to match the NinjaTrader screenshots:
// vivid trader-green and trader-red. Border = fill (same as the PnL bars) so
// candles render with the same vibrancy as the bar chart.
const THEME = {
  text: "#e6e6e6",
  textMuted: "#8a8a8a",
  grid: "rgba(255, 255, 255, 0.06)",
  border: "#2a2a2a",
  panel: "#1c1c1c",
  green: "#26c265",
  greenBorder: "#26c265",
  red: "#ef4444",
  redBorder: "#ef4444",
  unchanged: "#666",
  orbHigh: "rgba(38, 194, 101, 0.9)",
  orbLow: "rgba(239, 68, 68, 0.9)",
  highlight: "#fbbf24",
  highlightFill: "rgba(251, 191, 36, 0.18)",
};

const fetchDates = async () => {
  try {
    const res = await axios.get("http://localhost:8000/dashboard/sessions");
    dates.value = res.data.dates ?? [];
    if (dates.value.length && !selectedDate.value) {
      selectedDate.value = dates.value[0];
    }
    if (!dates.value.length) {
      error.value = "API returned 0 dates. Is market_bars populated?";
    }
  } catch (e) {
    const status = e.response?.status;
    if (status === 404) {
      error.value =
        "Backend missing /dashboard/sessions endpoint — restart the API server (uvicorn) to pick up the latest code.";
    } else if (e.message?.includes("Network Error")) {
      error.value =
        "Cannot reach API at http://localhost:8000 — is uvicorn running?";
    } else {
      error.value = `Failed to load dates: ${e.message ?? e}`;
    }
  }
};

// silent=true: update chart data in-place to preserve zoom/pan state (used for auto-refresh).
// silent=false: destroy and recreate the chart (used when switching dates).
const fetchSession = async (date, { silent = false } = {}) => {
  if (!date) return;
  if (!silent) {
    loading.value = true;
    selectedIndex.value = null;
  }
  error.value = null;
  try {
    const res = await axios.get(
      `http://localhost:8000/dashboard/sessions/${date}`,
    );
    bars.value = res.data.bars ?? [];
    decisions.value = res.data.decisions ?? [];
    orb.value = res.data.orb ?? null;
    lastUpdated.value = new Date();
    if (silent) {
      updateChartData();
      updatePnlChartData();
    } else {
      renderChart();
      renderPnlChart();
    }
  } catch (e) {
    error.value = e.message ?? "Failed to load session.";
  } finally {
    if (!silent) loading.value = false;
  }
};

function selectDecision(i) {
  selectedIndex.value = selectedIndex.value === i ? null : i;
  if (selectedIndex.value == null) {
    if (chartInstance) {
      chartInstance.options.scales.x.min = undefined;
      chartInstance.options.scales.x.max = undefined;
    }
  } else {
    focusOnDecision(decisions.value[i]);
  }
  renderChart();
  renderPnlChart();
  // Scroll the chart into view if user clicked a row that's below it.
  if (chartWrapRef.value && selectedIndex.value != null) {
    chartWrapRef.value.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

function focusOnDecision(d) {
  if (!chartInstance) return;
  const tEntry = tsToDate(d.entry_time).valueOf();
  const tExit = d.exit_time
    ? tsToDate(d.exit_time).valueOf()
    : tEntry + 5 * 60 * 1000;
  const buffer = 8 * 60 * 1000; // 8 min before/after for context
  chartInstance.options.scales.x.min = tEntry - buffer;
  chartInstance.options.scales.x.max = tExit + buffer;
}

function visibleCandleCount(chart) {
  const x = chart.scales.x;
  if (!x) return tradingHourBars.value.length || 1;
  const lo = x.min;
  const hi = x.max;
  if (lo == null || hi == null) return tradingHourBars.value.length || 1;
  const n = tradingHourBars.value.filter((b) => {
    const t = tsToDate(b.timestamp).valueOf();
    return t >= lo && t <= hi;
  }).length;
  return n || 1;
}

// Resize candle bars to the visible range. The financial plugin computes width
// once from the full data range, so without this hook bars stay 1-2px thin even
// when you zoom into 30 bars over a 480px chart.
function applyDynamicBarThickness(chart) {
  if (!chart) return;
  const ds = chart.data.datasets?.[0];
  if (!ds) return;
  const x = chart.scales.x;
  if (!x || x.right == null || x.left == null) return;

  const visible = visibleCandleCount(chart);
  const slotPx = (x.right - x.left) / visible;
  const desired = Math.max(1, Math.min(slotPx * 0.85, 28));

  const current = ds.barThickness;
  if (current == null || Math.abs(current - desired) > 0.5) {
    ds.barThickness = desired;
    chart.update("none");
  }
}

const tradingHourBars = computed(() =>
  bars.value.filter((b) => {
    const m = parseHHMM(b.timestamp);
    // Show 08:00-15:30 CT window — trims overnight bars that crowd the X axis
    return m >= 8 * 60 && m <= 15 * 60 + 30;
  }),
);

function parseHHMM(ts) {
  // Accepts "YYYY-MM-DDTHH:mm:ss..." or "YYYY-MM-DD HH:mm:ss"
  const t = String(ts);
  const sep = t.indexOf("T") >= 0 ? "T" : " ";
  const time = t.split(sep)[1] ?? "00:00:00";
  const [hh, mm] = time.split(":");
  return parseInt(hh, 10) * 60 + parseInt(mm, 10);
}

function tsToDate(ts) {
  // Treat naive timestamps as already in local clock (CT for this user).
  // Strings with offsets are honored by Date constructor automatically.
  if (typeof ts === "string" && !/[zZ+-]\d/.test(ts.slice(10))) {
    return new Date(ts.replace(" ", "T"));
  }
  return new Date(ts);
}

function classifyExit(reason) {
  if (reason === "TARGET") return { color: "#16a34a", label: "Target" };
  if (reason === "STOP") return { color: "#dc2626", label: "Stop" };
  return { color: "#6b7280", label: reason || "Time exit" };
}

function buildDatasets() {
  const visibleBars = tradingHourBars.value;

  const candles = visibleBars.map((b) => ({
    x: tsToDate(b.timestamp).valueOf(),
    o: b.open,
    h: b.high,
    l: b.low,
    c: b.close,
  }));

  const buyEntries = [];
  const sellEntries = [];
  const exitsTarget = [];
  const exitsStop = [];
  const exitsOther = [];

  for (const d of decisions.value) {
    const entryPoint = {
      x: tsToDate(d.entry_time).valueOf(),
      y: d.entry,
      _decision: d,
    };
    if (d.action === "BUY") buyEntries.push(entryPoint);
    else if (d.action === "SELL") sellEntries.push(entryPoint);

    if (d.exit_time && d.exit_price != null) {
      const exitPoint = {
        x: tsToDate(d.exit_time).valueOf(),
        y: d.exit_price,
        _decision: d,
      };
      if (d.exit_reason === "TARGET") exitsTarget.push(exitPoint);
      else if (d.exit_reason === "STOP") exitsStop.push(exitPoint);
      else exitsOther.push(exitPoint);
    }
  }

  const datasets = [
    {
      label: "Bars",
      type: "candlestick",
      data: candles,
      backgroundColors: {
        up: THEME.green,
        down: THEME.red,
        unchanged: THEME.unchanged,
      },
      borderColors: {
        up: THEME.greenBorder,
        down: THEME.redBorder,
        unchanged: THEME.unchanged,
      },
    },
  ];

  if (orb.value) {
    const xMin = candles.length ? candles[0].x : null;
    const xMax = candles.length ? candles[candles.length - 1].x : null;
    if (xMin != null && xMax != null) {
      datasets.push(
        {
          label: `ORB high (${orb.value.high})`,
          type: "line",
          data: [
            { x: xMin, y: orb.value.high },
            { x: xMax, y: orb.value.high },
          ],
          borderColor: THEME.orbHigh,
          borderWidth: 1.5,
          borderDash: [6, 4],
          pointRadius: 0,
          fill: false,
        },
        {
          label: `ORB low (${orb.value.low})`,
          type: "line",
          data: [
            { x: xMin, y: orb.value.low },
            { x: xMax, y: orb.value.low },
          ],
          borderColor: THEME.orbLow,
          borderWidth: 1.5,
          borderDash: [6, 4],
          pointRadius: 0,
          fill: false,
        },
      );
    }
  }

  if (buyEntries.length) {
    datasets.push({
      label: "BUY entry",
      type: "scatter",
      data: buyEntries,
      pointStyle: "triangle",
      pointRadius: 9,
      pointHoverRadius: 12,
      backgroundColor: "rgba(22, 163, 74, 0.95)",
      borderColor: "#14532d",
    });
  }

  if (sellEntries.length) {
    datasets.push({
      label: "SELL entry",
      type: "scatter",
      data: sellEntries,
      pointStyle: "triangle",
      rotation: 180,
      pointRadius: 9,
      pointHoverRadius: 12,
      backgroundColor: "rgba(220, 38, 38, 0.95)",
      borderColor: "#7f1d1d",
    });
  }

  if (exitsTarget.length) {
    datasets.push({
      label: "Exit (target)",
      type: "scatter",
      data: exitsTarget,
      pointStyle: "crossRot",
      pointRadius: 10,
      pointHoverRadius: 13,
      borderColor: "#16a34a",
      backgroundColor: "#16a34a",
    });
  }
  if (exitsStop.length) {
    datasets.push({
      label: "Exit (stop)",
      type: "scatter",
      data: exitsStop,
      pointStyle: "crossRot",
      pointRadius: 10,
      pointHoverRadius: 13,
      borderColor: "#dc2626",
      backgroundColor: "#dc2626",
    });
  }
  if (exitsOther.length) {
    datasets.push({
      label: "Exit (time)",
      type: "scatter",
      data: exitsOther,
      pointStyle: "crossRot",
      pointRadius: 10,
      pointHoverRadius: 13,
      borderColor: "#9ca3af",
      backgroundColor: "#9ca3af",
    });
  }

  // Highlighted (selected) decision: a yellow ring around its entry point.
  if (selectedIndex.value != null) {
    const d = decisions.value[selectedIndex.value];
    if (d) {
      datasets.push({
        label: "Selected",
        type: "scatter",
        data: [
          {
            x: tsToDate(d.entry_time).valueOf(),
            y: d.entry,
            _decision: d,
          },
        ],
        pointStyle: "circle",
        pointRadius: 14,
        pointHoverRadius: 16,
        borderColor: THEME.highlight,
        backgroundColor: THEME.highlightFill,
        borderWidth: 3,
      });
    }
  }

  return datasets;
}

// Module-level coords so the plugin closure avoids Vue reactivity overhead.
let _crossX = null;
let _crossY = null;

// Inline (per-chart) plugin — draws crosshair lines at the mouse position.
// Uses beforeEvent so Chart.js triggers the redraw automatically via args.changed.
const crosshairPlugin = {
  id: "crosshairLines",
  beforeEvent(chart, args) {
    const { type, x, y } = args.event;
    if (type === "mousemove") {
      _crossX = x;
      _crossY = y;
      args.changed = true;
      const hits = chart.getElementsAtEventForMode(
        args.event.native,
        "nearest",
        { intersect: true },
        false,
      );
      chart.canvas.style.cursor = hits.length ? "pointer" : "default";
    } else if (type === "mouseout") {
      _crossX = null;
      _crossY = null;
      args.changed = true;
      chart.canvas.style.cursor = "default";
    } else if (type === "click") {
      const hits = chart.getElementsAtEventForMode(
        args.event.native,
        "nearest",
        { intersect: true },
        false,
      );
      const active = chart.tooltip._active ?? [];
      const isSame =
        hits.length > 0 &&
        active.length > 0 &&
        active[0].datasetIndex === hits[0].datasetIndex &&
        active[0].index === hits[0].index;
      if (hits.length === 0 || isSame) {
        chart.tooltip.setActiveElements([], { x: 0, y: 0 });
      } else {
        chart.tooltip.setActiveElements(hits, { x, y });
      }
      args.changed = true;
    }
  },
  afterDraw(chart) {
    if (_crossX == null) return;
    const { ctx, chartArea } = chart;
    if (!chartArea) return;
    const { left, right, top, bottom } = chartArea;
    if (_crossX < left || _crossX > right || _crossY < top || _crossY > bottom) return;
    ctx.save();
    ctx.strokeStyle = "rgba(200,200,200,0.30)";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(_crossX, top);
    ctx.lineTo(_crossX, bottom);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(left, _crossY);
    ctx.lineTo(right, _crossY);
    ctx.stroke();
    ctx.restore();
  },
};

function renderChart() {
  if (!canvasRef.value) return;
  if (chartInstance) {
    chartInstance.destroy();
    chartInstance = null;
  }

  const datasets = buildDatasets();

  chartInstance = new ChartJS(canvasRef.value, {
    type: "candlestick",
    data: { datasets },
    plugins: [crosshairPlugin],
    // Initial barThickness is applied right after creation via
    // applyDynamicBarThickness() so the first paint already shows correctly
    // sized bars for whatever range the chart opens with.
    options: {
      responsive: true,
      maintainAspectRatio: false,
      parsing: false,
      // Candle colors must live here — chartjs-chart-financial reads them off
      // options.elements.candlestick rather than the dataset for the element
      // fill/outline. Without this it falls back to the plugin's default
      // teal/pink, which is what shows in the screenshot.
      elements: {
        candlestick: {
          backgroundColors: {
            up: THEME.green,
            down: THEME.red,
            unchanged: THEME.unchanged,
          },
          borderColors: {
            up: THEME.greenBorder,
            down: THEME.redBorder,
            unchanged: THEME.unchanged,
          },
        },
      },
      plugins: {
        legend: {
          position: "top",
          labels: {
            color: THEME.text,
            usePointStyle: true,
            boxWidth: 8,
            boxHeight: 8,
            padding: 14,
          },
        },
        zoom: {
          // Wheel zooms X (time). Drag selects a region to zoom into. Pinch zooms both
          // axes on touch devices. Hold shift while wheeling to zoom Y as well.
          pan: {
            // Plain click+drag pans (no modifier). Drag-to-box-zoom is disabled
            // below so the two interactions don't fight for the same gesture.
            enabled: true,
            mode: "xy",
            threshold: 6,
            onPanComplete: ({ chart }) => applyDynamicBarThickness(chart),
          },
          zoom: {
            // Lower speed = less zoom per wheel tick / pinch step.
            // 0.02 ≈ 5× less sensitive than the plugin default (0.1).
            wheel: { enabled: true, speed: 0.02 },
            pinch: { enabled: true, speed: 0.02 },
            drag: { enabled: false },
            mode: "x",
            onZoom: ({ chart }) => applyDynamicBarThickness(chart),
            onZoomComplete: ({ chart }) => applyDynamicBarThickness(chart),
          },
          limits: {
            x: { minRange: 5 * 60 * 1000 }, // don't zoom tighter than a 5-minute span
          },
        },
        tooltip: {
          events: [],
          backgroundColor: THEME.panel,
          titleColor: "#fafafa",
          bodyColor: THEME.text,
          borderColor: THEME.border,
          borderWidth: 1,
          padding: 10,
          callbacks: {
            label: (ctx) => {
              const ds = ctx.dataset;
              const raw = ctx.raw;
              if (ds.type === "candlestick" && raw) {
                return [
                  `O: ${Number(raw.o).toFixed(2)}  H: ${Number(raw.h).toFixed(2)}  L: ${Number(raw.l).toFixed(2)}  C: ${Number(raw.c).toFixed(2)}`,
                ];
              }
              if (ds.type === "scatter" && raw?._decision) {
                const d = raw._decision;
                const lines = [
                  `${ds.label}: ${Number(raw.y).toFixed(2)}`,
                  `Strategy: ${d.strategy}`,
                  `Stop: ${Number(d.stop_loss).toFixed(2)}   Target: ${Number(d.take_profit).toFixed(2)}`,
                  `Reason: ${d.reason}`,
                ];
                if (d.exit_reason) lines.push(`Exit: ${d.exit_reason} @ ${Number(d.exit_price).toFixed(2)}`);
                return lines;
              }
              return `${ds.label}: ${raw?.y != null ? Number(raw.y).toFixed(2) : raw}`;
            },
          },
        },
      },
      scales: {
        x: {
          type: "time",
          time: {
            unit: "minute",
            displayFormats: { minute: "HH:mm" },
            tooltipFormat: "yyyy-MM-dd HH:mm",
          },
          ticks: {
            color: THEME.textMuted,
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 14,
          },
          grid: { color: THEME.grid, tickColor: THEME.grid },
          border: { color: THEME.border },
        },
        y: {
          beginAtZero: false,
          position: "right",
          ticks: {
            color: THEME.textMuted,
            callback: (v) => v,
          },
          grid: { color: THEME.grid, tickColor: THEME.grid },
          border: { color: THEME.border },
        },
      },
    },
  });

  // Wait one frame so scales are laid out, then size bars to the visible range.
  requestAnimationFrame(() => applyDynamicBarThickness(chartInstance));
}

function exitClass(reason) {
  if (reason === "TARGET") return "win";
  if (reason === "STOP") return "loss";
  return "neutral";
}


function pnlClass(pnl) {
  if (pnl == null) return "neutral";
  if (pnl > 0) return "win";
  if (pnl < 0) return "loss";
  return "neutral";
}

function formatPnl(pnl) {
  if (pnl == null || Number.isNaN(Number(pnl))) return "—";
  const n = Number(pnl);
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  const abs = Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${sign}$${abs}`;
}

const sessionPnl = computed(() =>
  decisions.value.reduce((sum, d) => sum + (Number(d.pnl) || 0), 0),
);

const winCount = computed(
  () => decisions.value.filter((d) => Number(d.pnl) > 0).length,
);
const lossCount = computed(
  () => decisions.value.filter((d) => Number(d.pnl) < 0).length,
);

function formatTimeShort(ts) {
  const d = tsToDate(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  const h = d.getHours();
  const m = String(d.getMinutes()).padStart(2, "0");
  const ampm = h >= 12 ? "pm" : "am";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${m} ${ampm}`;
}

function formatDuration(ms) {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const totalSec = Math.round(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

/** Round a price to 2 decimal places; returns "—" for null/undefined. */
function fmt2(v) {
  if (v == null || v === "") return "—";
  const n = Number(v);
  return Number.isNaN(n) ? String(v) : n.toFixed(2);
}

/** MM/DD/YY H:MMam/pm — e.g. 05/01/26 2:42pm */
function formatDecisionTimestamp(ts) {
  const d = tsToDate(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  const M = String(d.getMonth() + 1).padStart(2, "0");
  const D = String(d.getDate()).padStart(2, "0");
  const Y = String(d.getFullYear()).slice(-2);
  const h = d.getHours();
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ampm = h >= 12 ? "pm" : "am";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${M}/${D}/${Y} ${h12}:${mm}${ampm}`;
}

const sessionStats = computed(() => {
  const ds = decisions.value;
  // Return a zero-state instead of null so the stats grid stays mounted with
  // dashes/zeros on days with no trades — the section is always visible.
  if (!ds.length) {
    return {
      realized: 0,
      avgPnl: 0,
      winPct: 0,
      drawdown: 0,
      count: 0,
      avgDuration: 0,
    };
  }

  const pnls = ds.map((d) => Number(d.pnl) || 0);
  const realized = pnls.reduce((a, b) => a + b, 0);
  const avgPnl = realized / ds.length;
  const wins = pnls.filter((p) => p > 0).length;
  const winPct = (wins / ds.length) * 100;

  // Peak-to-trough drawdown of cumulative PnL across the session.
  let cum = 0;
  let peak = 0;
  let maxDd = 0;
  for (const p of pnls) {
    cum += p;
    if (cum > peak) peak = cum;
    if (cum - peak < maxDd) maxDd = cum - peak;
  }

  const durations = ds
    .map((d) => {
      if (!d.exit_time || !d.entry_time) return null;
      return tsToDate(d.exit_time).valueOf() - tsToDate(d.entry_time).valueOf();
    })
    .filter((x) => x != null && Number.isFinite(x));
  const avgDuration = durations.length
    ? durations.reduce((a, b) => a + b, 0) / durations.length
    : 0;

  return {
    realized,
    avgPnl,
    winPct,
    drawdown: maxDd,
    count: ds.length,
    avgDuration,
  };
});

function renderPnlChart() {
  if (!pnlCanvasRef.value) return;
  if (pnlChartInstance) {
    pnlChartInstance.destroy();
    pnlChartInstance = null;
  }
  if (!decisions.value.length) return;

  const labels = decisions.value.map((_, i) => i);
  const data = decisions.value.map((d) => Number(d.pnl) || 0);
  const colors = decisions.value.map((d, i) => {
    if (i === selectedIndex.value) return "rgba(229, 231, 235, 0.9)";
    return Number(d.pnl) >= 0 ? THEME.green : THEME.red;
  });

  pnlChartInstance = new ChartJS(pnlCanvasRef.value, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          data,
          backgroundColor: colors,
          borderColor: colors,
          borderRadius: 2,
          borderSkipped: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      onClick: (_evt, elements) => {
        if (elements.length) selectDecision(elements[0].index);
      },
      onHover: (evt, elements) => {
        evt.native.target.style.cursor = elements.length ? "pointer" : "default";
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: THEME.panel,
          titleColor: "#fafafa",
          bodyColor: THEME.text,
          borderColor: THEME.border,
          borderWidth: 1,
          padding: 10,
          displayColors: false,
          callbacks: {
            title: (items) => {
              const i = items[0]?.dataIndex;
              const d = decisions.value[i];
              return d ? formatTimeShort(d.entry_time) : "";
            },
            label: (item) => formatPnl(item.parsed.y),
          },
        },
      },
      scales: {
        x: {
          ticks: {
            color: THEME.textMuted,
            autoSkip: true,
            maxTicksLimit: 6,
            callback(_val, idx) {
              const d = decisions.value[idx];
              return d ? formatTimeShort(d.entry_time) : "";
            },
            font: { size: 11 },
          },
          grid: { display: false },
          border: { color: THEME.border },
        },
        y: {
          position: "left",
          ticks: {
            color: THEME.textMuted,
            callback: (v) =>
              v === 0
                ? "$0.00"
                : (v >= 0 ? "$" : "-$") +
                  Math.abs(v).toLocaleString(undefined, {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  }),
          },
          grid: { color: THEME.grid, tickColor: THEME.grid },
          border: { color: THEME.border },
        },
      },
    },
  });
}

function resetZoom() {
  if (!chartInstance) return;
  chartInstance.resetZoom();
  // resetZoom() doesn't fire zoom callbacks and leaves stale bar thickness.
  // Force a full redraw one frame after the scales settle.
  requestAnimationFrame(() => {
    applyDynamicBarThickness(chartInstance);
    chartInstance.update();
  });
}

// Update the candlestick chart datasets in-place without destroying the instance.
// This preserves any zoom/pan state the user has set.
function updateChartData() {
  if (!chartInstance) {
    renderChart();
    return;
  }
  chartInstance.data.datasets = buildDatasets();
  chartInstance.update("none");
  requestAnimationFrame(() => applyDynamicBarThickness(chartInstance));
}

// Update the PnL bar chart in-place.
function updatePnlChartData() {
  if (!pnlChartInstance) {
    renderPnlChart();
    return;
  }
  const data = decisions.value.map((d) => Number(d.pnl) || 0);
  const colors = decisions.value.map((d, i) =>
    i === selectedIndex.value
      ? "rgba(229, 231, 235, 0.9)"
      : Number(d.pnl) >= 0 ? THEME.green : THEME.red,
  );
  pnlChartInstance.data.labels = decisions.value.map((_, i) => i);
  pnlChartInstance.data.datasets[0].data = data;
  pnlChartInstance.data.datasets[0].backgroundColor = colors;
  pnlChartInstance.data.datasets[0].borderColor = colors;
  pnlChartInstance.update();
}

function startAutoRefresh() {
  stopAutoRefresh();
  refreshInterval = setInterval(() => {
    if (selectedDate.value) fetchSession(selectedDate.value, { silent: true });
  }, REFRESH_INTERVAL_MS);
}

function stopAutoRefresh() {
  if (refreshInterval != null) {
    clearInterval(refreshInterval);
    refreshInterval = null;
  }
}

watch(selectedDate, (d) => {
  fetchSession(d);
  if (isToday.value) startAutoRefresh();
  else stopAutoRefresh();
});

watch(isToday, (val) => {
  if (val) startAutoRefresh();
  else stopAutoRefresh();
});

onMounted(async () => {
  await fetchDates();
  if (selectedDate.value) {
    await fetchSession(selectedDate.value);
    if (isToday.value) startAutoRefresh();
  }
});

onUnmounted(() => {
  stopAutoRefresh();
});
</script>

<template>
  <div class="session-panel">
    <div class="header">
      <label>
        Session date:
        <select v-model="selectedDate">
          <option v-for="d in dates" :key="d" :value="d">{{ d }}</option>
        </select>
      </label>

      <span v-if="loading" class="status">Loading…</span>
      <span v-else-if="error" class="status err">{{ error }}</span>
      <span v-else-if="orb" class="status">
        ORB {{ orb.high }} / {{ orb.low }} · {{ decisions.length }} decisions
      </span>
      <span v-else class="status muted">No ORB locked for this date</span>

      <span v-if="isToday" class="live-badge">
        <span class="live-dot"></span>live · updates every 60s
        <span v-if="lastUpdated" class="last-updated">
          · last {{ formatLastUpdated(lastUpdated) }}
        </span>
      </span>

      <button
        v-if="bars.length"
        class="reset-zoom"
        type="button"
        @click="resetZoom"
        title="Reset zoom (or double-click the chart)"
      >
        Reset zoom
      </button>
    </div>

    <p v-if="bars.length" class="zoom-hint">
      Scroll to zoom · drag to pan · pinch on touch
    </p>

    <div class="chart-wrap" ref="chartWrapRef">
      <canvas ref="canvasRef"></canvas>
    </div>

    <div class="performance-block">
      <div class="performance-header">
        <h3>Performance</h3>
        <span class="performance-sub">
          {{ decisions.length
              ? "PnL per trade · click a bar to focus its entry above"
              : "No trades for this session" }}
        </span>
      </div>

      <div v-if="decisions.length" class="pnl-chart-wrap">
        <canvas ref="pnlCanvasRef"></canvas>
      </div>
      <div v-else class="pnl-chart-empty">
        <p>No trades to chart for this session.</p>
        <p class="muted">
          The PnL chart appears here once the engine produces BUY / SELL decisions.
        </p>
      </div>

      <div class="stats-grid">
        <div class="stat">
          <span class="stat-label">Realized PnL</span>
          <strong class="stat-value" :class="pnlClass(sessionStats.realized)">
            {{ formatPnl(sessionStats.realized) }}
          </strong>
        </div>
        <div class="stat">
          <span class="stat-label">Average trade PnL</span>
          <strong class="stat-value" :class="pnlClass(sessionStats.avgPnl)">
            {{ formatPnl(sessionStats.avgPnl) }}
          </strong>
        </div>
        <div class="stat">
          <span class="stat-label">Winning trades percent</span>
          <strong class="stat-value">{{ sessionStats.winPct.toFixed(2) }}%</strong>
        </div>
        <div class="stat">
          <span class="stat-label">Drawdown</span>
          <strong
            class="stat-value"
            :class="sessionStats.drawdown < 0 ? 'loss' : 'neutral'"
          >
            {{ formatPnl(sessionStats.drawdown) }}
          </strong>
        </div>
        <div class="stat">
          <span class="stat-label">Number of trades</span>
          <strong class="stat-value">{{ sessionStats.count }}</strong>
        </div>
        <div class="stat">
          <span class="stat-label">Average trade time</span>
          <strong class="stat-value">
            {{ sessionStats.count > 0
                ? formatDuration(sessionStats.avgDuration)
                : "—" }}
          </strong>
        </div>
      </div>
    </div>

    <div v-if="decisions.length" class="decisions-list">
      <p class="row-hint">
        Click any row to focus that decision on the chart and highlight the entry.
      </p>
      <table>
        <thead>
          <tr>
            <th>Entry time</th>
            <th>Action</th>
            <th>Strategy</th>
            <th class="num">Qty</th>
            <th class="num">Entry</th>
            <th class="num">Stop</th>
            <th class="num">Target</th>
            <th>Exit</th>
            <th class="num">PnL</th>
            <th>Reason (entry)</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(d, i) in decisions"
            :key="i"
            :class="{ selected: selectedIndex === i }"
            @click="selectDecision(i)"
            tabindex="0"
            @keydown.enter="selectDecision(i)"
            @keydown.space.prevent="selectDecision(i)"
          >
            <td>{{ formatDecisionTimestamp(d.entry_time) }}</td>
            <td :class="d.action === 'BUY' ? 'buy' : 'sell'">{{ d.action }}</td>
            <td>{{ d.strategy }}</td>
            <td class="num">{{ d.quantity ?? 1 }}</td>
            <td class="num">{{ fmt2(d.entry) }}</td>
            <td class="num">{{ fmt2(d.stop_loss) }}</td>
            <td class="num">{{ fmt2(d.take_profit) }}</td>
            <td :class="exitClass(d.exit_reason)">
              {{ d.exit_reason }} @ {{ fmt2(d.exit_price) }}
            </td>
            <td class="num" :class="pnlClass(d.pnl)">{{ formatPnl(d.pnl) }}</td>
            <td class="reason">{{ d.reason }}</td>
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <td colspan="8" class="totals-label">
              Session totals · {{ winCount }} win{{ winCount === 1 ? "" : "s" }} /
              {{ lossCount }} loss{{ lossCount === 1 ? "" : "es" }}
            </td>
            <td class="num" :class="pnlClass(sessionPnl)">
              {{ formatPnl(sessionPnl) }}
            </td>
            <td></td>
          </tr>
        </tfoot>
      </table>
    </div>
  </div>
</template>

<style scoped>
.session-panel {
  background: var(--bg-page);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 14px 16px 18px;
  margin-top: 12px;
}

.header {
  display: flex;
  align-items: center;
  gap: 14px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 10px;
}

.header label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.6px;
}

.header select {
  padding: 4px 10px;
  background: var(--bg-elevated);
  color: var(--text);
  border: 1px solid var(--border-strong);
  border-radius: 4px;
  font-size: 13px;
  font-family: inherit;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}

.header select:hover {
  border-color: #4a4a4a;
}

.status {
  font-size: 12px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.status.muted {
  color: var(--text-dim);
}

.status.err {
  color: var(--red-strong);
}

.live-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--text-muted);
  letter-spacing: 0.3px;
}

.live-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #26c265;
  flex-shrink: 0;
  animation: pulse 2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

.last-updated {
  font-variant-numeric: tabular-nums;
}

.reset-zoom {
  margin-left: auto;
  padding: 5px 12px;
  border: 1px solid var(--border-strong);
  background: var(--bg-elevated);
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
  color: var(--text);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.reset-zoom:hover {
  background: var(--bg-hover);
  border-color: #4a4a4a;
}

.zoom-hint {
  font-size: 11px;
  color: var(--text-dim);
  margin: 0 0 8px;
  letter-spacing: 0.3px;
}

.chart-wrap {
  position: relative;
  height: 480px;
  background: var(--bg-page);
  border-radius: 4px;
  padding: 4px;
}

.performance-block {
  margin-top: 22px;
  padding-top: 18px;
  border-top: 1px solid var(--border);
}

.performance-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 12px;
}

.performance-header h3 {
  font-size: 16px;
  color: var(--text-strong);
  font-weight: 500;
  margin: 0;
}

.performance-sub {
  font-size: 11px;
  color: var(--text-dim);
  letter-spacing: 0.3px;
}

.pnl-chart-wrap {
  position: relative;
  height: 240px;
  background: var(--bg-page);
  border-radius: 4px;
  padding: 6px 4px;
}

.pnl-chart-empty {
  height: 240px;
  background: var(--bg-page);
  border: 1px dashed var(--border-strong);
  border-radius: 4px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  color: var(--text-muted);
  font-size: 13px;
  text-align: center;
  padding: 0 16px;
}

.pnl-chart-empty .muted {
  color: var(--text-dim);
  font-size: 12px;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
  margin-top: 14px;
}

@media (max-width: 1100px) {
  .stats-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
@media (max-width: 720px) {
  .stats-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.stat {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px 14px;
  min-width: 0;
}

.stat-label {
  display: block;
  font-size: 11px;
  color: var(--text-muted);
  margin-bottom: 6px;
  letter-spacing: 0.3px;
}

.stat-value {
  font-size: 19px;
  font-weight: 600;
  color: var(--text-strong);
  font-variant-numeric: tabular-nums;
}

.stat-value.win {
  color: var(--green-strong);
}

.stat-value.loss {
  color: var(--red-strong);
}

.stat-value.neutral {
  color: var(--text);
}

.decisions-list {
  margin-top: 22px;
  padding-top: 18px;
  border-top: 1px solid var(--border);
  font-size: 12px;
  overflow-x: auto;
}

.row-hint {
  margin: 0 0 6px;
  font-size: 11px;
  color: var(--text-dim);
  letter-spacing: 0.3px;
}

.decisions-list table {
  width: 100%;
  border-collapse: collapse;
}

.decisions-list th {
  text-align: left;
  padding: 8px 10px;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  color: var(--text-muted);
  font-weight: 500;
  border-bottom: 1px solid var(--border-strong);
  background: var(--bg-elevated);
}

.decisions-list td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  color: var(--text);
}

.decisions-list tbody tr {
  cursor: pointer;
  transition: background-color 60ms ease;
}

.decisions-list tbody tr:hover {
  background: var(--bg-hover);
}

.decisions-list tbody tr:focus-visible {
  outline: none;
  background: var(--bg-hover);
}

.decisions-list tbody tr.selected {
  background: rgba(251, 191, 36, 0.08);
  box-shadow: inset 3px 0 0 var(--highlight);
}

.decisions-list tbody tr.selected:hover {
  background: rgba(251, 191, 36, 0.12);
}

.decisions-list .buy {
  color: var(--green-strong);
  font-weight: 600;
}

.decisions-list .sell {
  color: var(--red-strong);
  font-weight: 600;
}

.decisions-list .win {
  color: var(--green-strong);
  font-weight: 500;
}

.decisions-list .loss {
  color: var(--red-strong);
  font-weight: 500;
}

.decisions-list .neutral {
  color: var(--text-muted);
}

.decisions-list .reason {
  color: var(--text-muted);
  max-width: 360px;
}

.decisions-list .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.decisions-list tfoot td {
  border-top: 2px solid var(--border-strong);
  border-bottom: none;
  font-weight: 600;
  background: var(--bg-elevated);
  color: var(--text-strong);
}

.decisions-list .totals-label {
  text-align: right;
  color: var(--text-muted);
  font-weight: 500;
  text-transform: uppercase;
  font-size: 11px;
  letter-spacing: 0.6px;
}
</style>
