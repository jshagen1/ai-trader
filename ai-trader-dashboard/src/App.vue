<script setup>
import { ref, onMounted } from "vue";
import axios from "axios";
import SessionChart from "./components/SessionChart.vue";

const summary = ref(null);

const fetchSummary = async () => {
  const res = await axios.get("http://localhost:8000/dashboard/summary");
  summary.value = res.data;
};


const formatMoney = (value) =>
  Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

/** M/d/Y HH:mm:ss (local time) */
const formatTimestamp = (value) => {
  if (value == null || value === "") return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  const M = d.getMonth() + 1;
  const dDay = d.getDate();
  const Y = d.getFullYear();
  const HH = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${M}/${dDay}/${Y} ${HH}:${mm}:${ss}`;
};

function pnlClass(value) {
  if (value == null || value === "") return "neutral";
  const n = Number(value);
  if (Number.isNaN(n)) return "neutral";
  if (n > 0) return "win";
  if (n < 0) return "loss";
  return "neutral";
}

onMounted(fetchSummary);
</script>

<template>
  <main class="page">
    <header class="topbar">
      <h1>AI Trader</h1>
      <span class="subtitle">strategy dashboard</span>
    </header>

    <p v-if="!summary" class="loading">Loading…</p>

    <section v-else>
      <div class="cards">
        <div class="card">
          <span class="label">Total Trades</span>
          <strong class="value">{{ summary.total_trades }}</strong>
        </div>

        <div class="card">
          <span class="label">Win Rate</span>
          <strong class="value">{{ summary.win_rate }}%</strong>
        </div>

        <div class="card">
          <span class="label">Net PnL</span>
          <strong class="value" :class="pnlClass(summary.net_pnl)">
            ${{ summary.net_pnl }}
          </strong>
        </div>

        <div class="card">
          <span class="label">Expectancy</span>
          <strong class="value" :class="pnlClass(summary.expectancy)">
            ${{ summary.expectancy }}
          </strong>
        </div>

        <div class="card">
          <span class="label">Profit Factor</span>
          <strong class="value">{{ summary.profit_factor }}</strong>
        </div>

        <div class="card">
          <span class="label">Max Drawdown</span>
          <strong class="value loss">${{ summary.max_drawdown }}</strong>
        </div>

        <div class="card">
          <span class="label">Skips Today</span>
          <strong class="value">{{ summary.skips_today ?? 0 }}</strong>
          <small v-if="summary.total_skips">
            {{ summary.total_skips }} total
          </small>
        </div>
      </div>

      <div class="panel">
        <h2 class="section-title">Session Decisions</h2>
        <p class="hint">
          Pick a trading date to see candlesticks for that session, the ORB high/low,
          and every BUY / SELL decision the engine would make today against historical
          data — including each trade's stop, target, and exit outcome. Hover any
          marker for the entry reason.
        </p>
        <SessionChart />
      </div>

      <div v-if="summary.recent_skips && summary.recent_skips.length" class="panel">
        <h2 class="section-title">Recent Skipped Orders</h2>

        <p class="hint">
          Orders the bridge declined to submit because the live quote drifted too far
          from the signal entry between bar close and submission.
        </p>

        <div class="reason-pills">
          <span
            v-for="(count, reason) in summary.skips_by_reason"
            :key="reason"
            class="pill"
          >
            {{ reason }}: {{ count }}
          </span>
        </div>

        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Symbol</th>
              <th>Action</th>
              <th>Signal Entry</th>
              <th>Live Quote</th>
              <th>Drift (pts)</th>
              <th>Reason</th>
            </tr>
          </thead>

          <tbody>
            <tr v-for="skip in summary.recent_skips" :key="skip.id">
              <td>{{ formatTimestamp(skip.timestamp) }}</td>
              <td>{{ skip.symbol }}</td>
              <td>{{ skip.action }}</td>
              <td>{{ skip.signal_entry }}</td>
              <td>{{ skip.live_quote }}</td>
              <td>{{ Number(skip.drift_points).toFixed(2) }}</td>
              <td>{{ skip.reason }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="panel">
        <h2 class="section-title">Recent Trades</h2>

        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Symbol</th>
              <th>Action</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>PnL</th>
            </tr>
          </thead>

          <tbody>
            <tr v-for="trade in summary.recent_trades" :key="trade.id">
              <td>{{ formatTimestamp(trade.timestamp) }}</td>
              <td>{{ trade.symbol }}</td>
              <td :class="trade.action === 'BUY' ? 'win' : 'loss'">
                {{ trade.action }}
              </td>
              <td class="num">{{ trade.entry_price }}</td>
              <td class="num">{{ trade.exit_price }}</td>
              <td class="num" :class="pnlClass(trade.pnl)">${{ trade.pnl }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>
</template>

<style>
.page {
  max-width: 1280px;
  margin: 0 auto;
  padding: 24px 28px 48px;
}

.topbar {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 8px 0 18px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 24px;
}

.topbar h1 {
  letter-spacing: 0.2px;
}

.topbar .subtitle {
  color: var(--text-muted);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 1.2px;
}

.loading {
  color: var(--text-muted);
  padding: 12px 0;
}

.cards {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}

@media (max-width: 1100px) {
  .cards {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
@media (max-width: 720px) {
  .cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.card {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px 14px;
  min-width: 0;
}

.card .label {
  display: block;
  color: var(--text-muted);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  margin-bottom: 6px;
}

.card .value {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-strong);
  font-variant-numeric: tabular-nums;
}

.card .value.win {
  color: var(--green-strong);
}

.card .value.loss {
  color: var(--red-strong);
}

.card small {
  display: block;
  color: var(--text-dim);
  font-size: 11px;
  margin-top: 4px;
}

.panel {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 20px;
  margin-top: 16px;
}

.section-title {
  margin-bottom: 8px;
}

.chart-wrap {
  position: relative;
  height: 320px;
  margin-top: 8px;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

th {
  text-align: left;
  padding: 8px 10px;
  color: var(--text-muted);
  font-weight: 500;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  border-bottom: 1px solid var(--border-strong);
  background: var(--bg-elevated);
  position: sticky;
  top: 0;
}

td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  color: var(--text);
}

td.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.win {
  color: var(--green-strong);
  font-weight: 500;
}

.loss {
  color: var(--red-strong);
  font-weight: 500;
}

.neutral {
  color: var(--text-muted);
}

.hint {
  color: var(--text-muted);
  font-size: 12px;
  margin: 4px 0 14px;
}

.reason-pills {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.pill {
  background: var(--bg-elevated);
  border: 1px solid var(--border-strong);
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 11px;
  color: var(--text-muted);
}
</style>