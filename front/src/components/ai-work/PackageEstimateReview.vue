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
  <ul class="my-3 max-h-80 space-y-2 overflow-y-auto" aria-label="待确认的包装外尺寸">
    <li v-for="row in rows" :key="row.sku_id" class="rounded-lg border border-amber-200 p-2 dark:border-amber-800">
      <p class="break-all text-xs"><strong v-if="row.sku_name">{{ row.sku_name }} · </strong>SKU {{ row.sku_id }}</p>
      <p v-for="(edge, key) in row.dimensions" :key="key" class="mt-1 text-sm">
        <strong>包装{{ labels[key] }} {{ edge.suggested }} cm</strong>（{{ edge.low }}–{{ edge.high }} cm）
      </p>
      <p class="mt-1 break-words text-xs">{{ row.basis }}</p>
    </li>
  </ul>
</template>
