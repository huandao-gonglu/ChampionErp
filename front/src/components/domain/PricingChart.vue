<script setup lang="ts">
import { computed } from 'vue'
import { Bar } from 'vue-chartjs'
import {
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Legend,
  LinearScale,
  Tooltip,
} from 'chart.js'
import type { PricingResult } from '@/types/workflow'

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip, Legend)

const props = defineProps<{
  result: PricingResult | null
}>()

const chartData = computed(() => ({
  labels: ['售价 USD', '净收益 CNY', '利润 CNY', '运费 USD'],
  datasets: [
    {
      label: '当前核价结果',
      borderRadius: 10,
      backgroundColor: ['#2563eb', '#10b981', '#f59e0b', '#64748b'],
      data: props.result
        ? [props.result.suggestedPriceUsd, props.result.netRevenueCny, props.result.profitCny, props.result.shippingCostUsd]
        : [0, 0, 0, 0],
    },
  ],
}))

const chartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: {
      display: false,
    },
  },
  scales: {
    y: {
      beginAtZero: true,
      ticks: {
        color: '#64748b',
      },
      grid: {
        color: '#cbd5e1',
      },
    },
    x: {
      ticks: {
        color: '#64748b',
      },
      grid: {
        display: false,
      },
    },
  },
}
</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <div class="flex items-center justify-between gap-3">
      <div>
        <h2 class="card-title">利润图表</h2>
        <p class="muted mt-1">Chart.js 展示核价关键指标。</p>
      </div>
      <span class="badge-muted">live</span>
    </div>
    <div class="mt-4 h-64">
      <Bar :data="chartData" :options="chartOptions" />
    </div>
  </section>
</template>
