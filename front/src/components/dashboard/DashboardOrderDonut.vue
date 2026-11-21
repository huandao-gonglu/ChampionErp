<script setup lang="ts">
import { computed } from 'vue'
import {
  Chart as ChartJS,
  ArcElement,
  Tooltip,
  Legend,
  type ChartData,
  type ChartOptions,
} from 'chart.js'
import { Doughnut } from 'vue-chartjs'
import type { OrderState } from '@/types/orders'

ChartJS.register(ArcElement, Tooltip, Legend)

const props = defineProps<{
  counts: Partial<Record<OrderState, number>>
  total: number
  fulfillmentRate: number
  dark: boolean
}>()

const segments = computed(() => {
  const raw = [
    { key: 'pending_shipment', label: '待发货', value: props.counts.pending_shipment || 0, color: '#f59e0b' },
    { key: 'processing', label: '处理中', value: props.counts.processing || 0, color: '#38bdf8' },
    { key: 'shipped', label: '已发货', value: props.counts.shipped || 0, color: '#22c55e' },
    { key: 'delivered', label: '已送达', value: props.counts.delivered || 0, color: '#10b981' },
    { key: 'cancelled', label: '已取消', value: props.counts.cancelled || 0, color: '#f43f5e' },
    { key: 'unknown', label: '待确认', value: props.counts.unknown || 0, color: '#64748b' },
  ]
  const nonZero = raw.filter((item) => item.value > 0)
  return nonZero.length
    ? nonZero
    : [{ key: 'empty', label: '暂无订单', value: 1, color: props.dark ? '#1e293b' : '#e2e8f0' }]
})

const chartData = computed<ChartData<'doughnut'>>(() => ({
  labels: segments.value.map((item) => item.label),
  datasets: [
    {
      data: segments.value.map((item) => item.value),
      backgroundColor: segments.value.map((item) => item.color),
      borderColor: props.dark ? '#0b1017' : '#ffffff',
      borderWidth: 2,
      hoverOffset: 5,
    },
  ],
}))

const chartOptions = computed<ChartOptions<'doughnut'>>(() => ({
  responsive: true,
  maintainAspectRatio: false,
  cutout: '72%',
  plugins: {
    legend: { display: false },
    tooltip: {
      enabled: props.total > 0,
      backgroundColor: props.dark ? 'rgba(9, 14, 22, 0.94)' : 'rgba(15, 23, 42, 0.92)',
      titleColor: '#f8fafc',
      bodyColor: '#e2e8f0',
      borderColor: props.dark ? 'rgba(34, 197, 94, 0.35)' : 'rgba(148, 163, 184, 0.3)',
      borderWidth: 1,
      padding: 10,
      cornerRadius: 10,
    },
  },
}))
</script>

<template>
  <div class="relative mx-auto h-44 w-44 shrink-0">
    <Doughnut :data="chartData" :options="chartOptions" />
    <div class="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
      <span class="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent-500 dark:text-slate-400">
        履约率
      </span>
      <span class="mt-0.5 text-2xl font-black tabular-nums text-accent-950 dark:text-white">
        {{ fulfillmentRate }}%
      </span>
      <span class="text-[11px] tabular-nums text-accent-500 dark:text-primary-300/80">
        共 {{ total }} 单
      </span>
    </div>
  </div>
</template>
