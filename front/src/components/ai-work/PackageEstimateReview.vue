<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ input: unknown }>()
type Estimate = { sku_id: string; sku_name?: string; basis: string; dimensions: Record<string, { low: number; suggested: number; high: number }> }
const rows = computed(() => {
  const input = props.input as { estimates?: Estimate[] } | undefined
  return Array.isArray(input?.estimates) ? input.estimates : []
})
const labels: Record<string, string> = { length_cm: '长', width_cm: '宽', height_cm: '高' }
</script>

<template>
  <ul class="divide-y divide-slate-200/80 border-t border-slate-200/80 pt-1 dark:divide-dark-700 dark:border-dark-700" aria-label="待确认的包装外尺寸">
    <li v-for="row in rows" :key="row.sku_id" class="space-y-1 py-2 first:pt-1 last:pb-0">
      <p class="break-all text-xs text-slate-500 dark:text-accent-300">
        <strong v-if="row.sku_name" class="text-slate-800 dark:text-white">{{ row.sku_name }} · </strong>SKU {{ row.sku_id }}
      </p>
      <p v-for="(edge, key) in row.dimensions" :key="key" class="flex flex-wrap items-baseline gap-x-2 text-xs tabular-nums">
        <strong class="text-slate-900 dark:text-white">包装{{ labels[key] }} {{ edge.suggested }} cm</strong>
        <span class="text-slate-500 dark:text-accent-300">（{{ edge.low }}–{{ edge.high }} cm）</span>
      </p>
      <p class="break-words text-xs leading-5 text-slate-600 dark:text-accent-200">{{ row.basis }}</p>
    </li>
  </ul>
</template>
