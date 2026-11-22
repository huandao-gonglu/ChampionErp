<script setup lang="ts">
import { computed } from 'vue'
import type { OnlineListing } from '@/api/onlineProducts'
import WorkspaceHint from '@/components/shared/WorkspaceHint.vue'

const props = defineProps<{ row: OnlineListing; shopNames: Record<string, string>; storeName?: string }>()
function formatTime(stamp: string) {
  return stamp ? new Date(stamp).toLocaleString('zh-CN', { hour12: false }) : ''
}
const entries = computed(() => props.row.prices.flatMap(price => {
  if (!price.amount?.trim()) return []
  const value = [price.currency, price.amount].filter(Boolean).join(' ')
  let label = price.label
  if (props.row.platform === 'yandex' && price.id !== 'business') {
    const campaign = price.id === 'campaign' ? props.row.account_id.split(':')[1] : price.id.replace(/^campaign:/, '')
    label = props.shopNames[campaign] || '店铺独立价'
  } else if (price.kind === 'net_proceeds') {
    label = `${label} · 净收入报价`
  }
  return [{ value, label }]
}))
const errorLines = computed(() => props.row.prices.flatMap(price => {
  if (!price.read_error) return []
  let label = price.label
  if (props.row.platform === 'yandex' && price.id !== 'business') {
    const campaign = price.id === 'campaign' ? props.row.account_id.split(':')[1] : price.id.replace(/^campaign:/, '')
    label = props.shopNames[campaign] || price.label || '店铺独立价'
  }
  return [price.cached_at ? `${label}沿用 ${formatTime(price.cached_at)} 的值` : `${label}：${price.read_error}`]
}))
const values = computed(() => [...new Set(entries.value.map(entry => entry.value))])
const hint = computed(() => {
  const row = props.row
  const names = row.platform === 'yandex'
    ? row.markets.map(market => props.shopNames[market.id]).filter(Boolean)
    : [props.storeName].filter(name => name && name !== row.account_id)
  const lines = entries.value.map(entry => `${entry.label}：${entry.value}`)
  if (names.length) lines.push(`关联店铺：${[...new Set(names)].join('、')}`)
  if (errorLines.value.length) lines.push(...errorLines.value)
  return [...new Set(lines)].join('\n')
})
</script>

<template>
  <div class="flex items-center gap-1.5">
    <div v-if="values.length"><p v-for="value in values" :key="value" class="whitespace-nowrap">{{ value }}</p></div>
    <span v-else class="muted" aria-label="尚未取得价格">—</span>
    <WorkspaceHint v-if="hint" label="查看价格与店铺" :content="hint" />
  </div>
</template>
