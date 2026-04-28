<script setup>
import { ref, onMounted, computed } from "vue";
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

const equityCurve = computed(() => summary.value?.equity_curve ?? []);

const chartData = computed(() => ({
  labels: equityCurve.value.map((x) => String(x.trade)),
  datasets: [
    {
      label: "Cumulative equity",
      data: equityCurve.value.map((x) => x.equity),
      borderColor: "#2563eb",
      backgroundColor: "rgba(37, 99, 235, 0.12)",
      borderWidth: 2,
      tension: 0.2,
      pointRadius: 3,
      pointHoverRadius: 6,
      pointHoverBorderWidth: 2,
    },
  ],
}));

const formatMoney = (value) =>
  Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

const chartOptions = computed(() => {
  const curve = equityCurve.value;
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      mode: "index",
      intersect: false,
    },
    plugins: {
      legend: {
        display: true,
        position: "top",
      },
      tooltip: {
        enabled: true,
        callbacks: {
          title: (items) => {
            const i = items[0]?.dataIndex;
            const row = curve[i];
            return row != null ? `Trade ${row.trade}` : "";
          },
          label: (item) => {
            const y = item.parsed?.y ?? item.raw;
            return `Equity: $${formatMoney(y)}`;
          },
        },
      },
    },
    scales: {
      x: {
        title: {
          display: true,
          text: "Trade #",
        },
        ticks: {
          maxRotation: 45,
          autoSkip: true,
          maxTicksLimit: 12,
        },
      },
      y: {
        title: {
          display: true,
          text: "Equity ($)",
        },
        ticks: {
          callback: (value) => `$${formatMoney(value)}`,
        },
      },
    },
  };
});

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

        <div class="chart-wrap">
          <Line :data="chartData" :options="chartOptions" />
        </div>
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

.chart-wrap {
  position: relative;
  height: 360px;
  margin-top: 12px;
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