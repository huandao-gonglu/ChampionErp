<script setup lang="ts">
import { computed } from 'vue'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Filler,
  Tooltip,
  Legend,
  type ChartData,
  type ChartOptions,
} from 'chart.js'
import { Line } from 'vue-chartjs'
import type { DashboardTrendPoint } from '@/api/dashboard'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Tooltip, Legend)

const props = defineProps<{
  trends: DashboardTrendPoint[]
  mode: 'orders' | 'catalog'
  dark: boolean
}>()

const chartData = computed<ChartData<'line'>>(() => {
  const labels = props.trends.map((item) => item.label)
  if (props.mode === 'catalog') {
    return {
      labels,
      datasets: [
        {
          label: '新增采集',
          data: props.trends.map((item) => item.collected),
          borderColor: '#22c55e',
          backgroundColor: props.dark ? 'rgba(34, 197, 94, 0.18)' : 'rgba(34, 197, 94, 0.14)',
          borderWidth: 2.5,
          pointRadius: 3,
          pointHoverRadius: 5,
          pointBackgroundColor: '#22c55e',
          tension: 0.36,
          fill: true,
        },
        {
          label: '生成草稿',
          data: props.trends.map((item) => item.drafts),
          borderColor: '#06b6d4',
          backgroundColor: props.dark ? 'rgba(6, 182, 212, 0.10)' : 'rgba(6, 182, 212, 0.08)',
          borderWidth: 2,
          pointRadius: 2.5,
          pointHoverRadius: 5,
          pointBackgroundColor: '#06b6d4',
          tension: 0.36,
          fill: true,
        },
        {
          label: '已发布草稿',
          data: props.trends.map((item) => item.published),
          borderColor: '#a855f7',
          backgroundColor: 'transparent',
          borderWidth: 2,
          borderDash: [5, 4],
          pointRadius: 2.5,
          pointHoverRadius: 5,
          pointBackgroundColor: '#a855f7',
          tension: 0.36,
          fill: false,
        },
      ],
    }
  }

  return {
    labels,
    datasets: [
      {
        label: '总订单',
        data: props.trends.map((item) => item.orders),
        borderColor: '#22c55e',
        backgroundColor: props.dark ? 'rgba(34, 197, 94, 0.18)' : 'rgba(34, 197, 94, 0.14)',
        borderWidth: 2.5,
        pointRadius: 3,
        pointHoverRadius: 6,
        pointBackgroundColor: '#4ade80',
        pointBorderColor: props.dark ? '#05080d' : '#ffffff',
        pointBorderWidth: 1.5,
        tension: 0.36,
        fill: true,
      },
      {
        label: 'Yandex',
        data: props.trends.map((item) => item.by_platform?.yandex ?? 0),
        borderColor: '#06b6d4',
        backgroundColor: 'transparent',
        borderWidth: 1.8,
        pointRadius: 2,
        pointHoverRadius: 4,
        tension: 0.36,
        fill: false,
      },
      {
        label: 'Ozon',
        data: props.trends.map((item) => item.by_platform?.ozon ?? 0),
        borderColor: '#3b82f6',
        backgroundColor: 'transparent',
        borderWidth: 1.8,
        pointRadius: 2,
        pointHoverRadius: 4,
        tension: 0.36,
        fill: false,
      },
      {
        label: 'Mercado Libre',
        data: props.trends.map((item) => item.by_platform?.mercadolibre ?? 0),
        borderColor: '#f59e0b',
        backgroundColor: 'transparent',
        borderWidth: 1.8,
        pointRadius: 2,
        pointHoverRadius: 4,
        tension: 0.36,
        fill: false,
      },
    ],
  }
})

const chartOptions = computed<ChartOptions<'line'>>(() => {
  const tickColor = props.dark ? '#94a3b8' : '#64748b'
  const gridColor = props.dark ? 'rgba(148, 163, 184, 0.10)' : 'rgba(15, 23, 42, 0.07)'
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      mode: 'index',
      intersect: false,
    },
    plugins: {
      legend: {
        display: true,
        position: 'top',
        align: 'end',
        labels: {
          color: props.dark ? '#cbd5e1' : '#334155',
          usePointStyle: true,
          pointStyle: 'circle',
          boxWidth: 7,
          boxHeight: 7,
          padding: 16,
          font: { size: 12, weight: 600 },
        },
      },
      tooltip: {
        backgroundColor: props.dark ? 'rgba(9, 14, 22, 0.94)' : 'rgba(15, 23, 42, 0.92)',
        titleColor: '#f8fafc',
        bodyColor: '#e2e8f0',
        borderColor: props.dark ? 'rgba(34, 197, 94, 0.35)' : 'rgba(148, 163, 184, 0.3)',
        borderWidth: 1,
        padding: 10,
        cornerRadius: 10,
        displayColors: true,
      },
    },
    scales: {
      x: {
        grid: { display: false },
        border: { display: false },
        ticks: {
          color: tickColor,
          font: { size: 11 },
          maxRotation: 0,
          autoSkipPadding: 14,
        },
      },
      y: {
        beginAtZero: true,
        border: { display: false },
        grid: { color: gridColor },
        ticks: {
          color: tickColor,
          precision: 0,
          font: { size: 11 },
        },
      },
    },
  }
})
</script>

<template>
  <div class="relative h-72 w-full sm:h-80 xl:h-96">
    <Line :data="chartData" :options="chartOptions" />
  </div>
</template>
