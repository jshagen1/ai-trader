<script setup>
import { ref, onMounted } from "vue";
import axios from "axios";
import {
  Chart as ChartJS,
  LineElement,
  PointElement,
  LinearScale,
  CategoryScale,
  Tooltip,
  Legend,
} from "chart.js";
import { Line } from "vue-chartjs";

ChartJS.register(LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Legend);

const summary = ref(null);

const fetchSummary = async () => {
  const res = await axios.get("http://localhost:8000/dashboard/summary");
  summary.value = res.data;
};

onMounted(fetchSummary);
</script>

<template>
  <main class="page">
    <h1>AI Trader Dashboard</h1>

    <p v-if="!summary">Loading...</p>

    <section v-else>
      <div class="cards">
        <div class="card">
          <span>Total Trades</span>
          <strong>{{ summary.total_trades }}</strong>
        </div>

        <div class="card">
          <span>Win Rate</span>
          <strong>{{ summary.win_rate }}%</strong>
        </div>

        <div class="card">
          <span>Net PnL</span>
          <strong>${{ summary.net_pnl }}</strong>
        </div>

        <div class="card">
          <span>Expectancy</span>
          <strong>${{ summary.expectancy }}</strong>
        </div>

        <div class="card">
          <span>Profit Factor</span>
          <strong>{{ summary.profit_factor }}</strong>
        </div>

        <div class="card">
          <span>Max Drawdown</span>
          <strong>${{ summary.max_drawdown }}</strong>
        </div>
      </div>

      <div class="panel">
        <h2>Equity Curve</h2>

        <Line
          :data="{
            labels: summary.equity_curve.map(x => x.trade),
            datasets: [
              {
                label: 'Equity',
                data: summary.equity_curve.map(x => x.equity),
              }
            ]
          }"
          :options="{
            responsive: true,
            plugins: { legend: { display: false } }
          }"
        />
      </div>

      <div class="panel">
        <h2>Recent Trades</h2>

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
              <td>{{ trade.timestamp }}</td>
              <td>{{ trade.symbol }}</td>
              <td>{{ trade.action }}</td>
              <td>{{ trade.entry_price }}</td>
              <td>{{ trade.exit_price }}</td>
              <td>${{ trade.pnl }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>
</template>

<style>
.page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 32px;
  font-family: system-ui, sans-serif;
}

.cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.card,
.panel {
  border: 1px solid #ddd;
  border-radius: 12px;
  padding: 20px;
  background: white;
}

.card span {
  display: block;
  color: #666;
  margin-bottom: 8px;
}

.card strong {
  font-size: 28px;
}

.panel {
  margin-top: 24px;
}

table {
  width: 100%;
  border-collapse: collapse;
}

th,
td {
  border-bottom: 1px solid #eee;
  padding: 10px;
  text-align: left;
}
</style>