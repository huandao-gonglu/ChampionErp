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

function cssColor(token: string, fallback: string) {
  if (typeof window === 'undefined') return fallback
  const value = window.getComputedStyle(document.documentElement).getPropertyValue(token).trim()
  return value ? `rgb(${value})` : fallback
}

const labels = computed(() => (props.result?.results.length
  ? props.result.results.map((item) => `${item.platform} · ${item.site}`)
  : ['待核价']))

const chartData = computed(() => ({
  labels: labels.value,
  datasets: [
    {
      label: '利润 CNY',
      borderRadius: 8,
      backgroundColor: cssColor('--color-success-500', 'rgb(34 197 94)'),
      data: props.result?.results.length ? props.result.results.map((item) => item.profitCny) : [0],
    },
    {
      label: '总成本 CNY',
      borderRadius: 8,
      backgroundColor: cssColor('--color-warning-500', 'rgb(245 158 11)'),
      data: props.result?.results.length ? props.result.results.map((item) => item.totalCostCny) : [0],
    },
  ],
}))

const chartOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: {
      labels: {
        color: cssColor('--color-accent-600', 'rgb(82 82 91)'),
      },
    },
  },
  scales: {
    y: {
      beginAtZero: true,
      ticks: {
        color: cssColor('--color-accent-500', 'rgb(113 113 122)'),
      },
      grid: {
        color: cssColor('--color-accent-300', 'rgb(212 212 216)'),
      },
    },
    x: {
      ticks: {
        color: cssColor('--color-accent-500', 'rgb(113 113 122)'),
      },
      grid: {
        display: false,
      },
    },
  },
}))
</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <div class="flex items-center justify-between gap-3">
      <div>
        <h2 class="card-title">利润对比</h2>
        <p class="muted mt-1">按目标市场展示利润和总成本。</p>
      </div>
      <span class="badge-muted">{{ props.result?.results.length || 0 }} 个市场</span>
    </div>
    <div class="mt-4 h-64">
      <Bar :data="chartData" :options="chartOptions" />
    </div>
  </section>
</template>
