<script setup lang="ts">
import { computed } from 'vue'
import type { OnlineListing } from '@/api/onlineProducts'
import WorkspaceHint from '@/components/shared/WorkspaceHint.vue'

const props = defineProps<{ row: OnlineListing; shopNames: Record<string, string>; storeName?: string }>()
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
const values = computed(() => [...new Set(entries.value.map(entry => entry.value))])
const hint = computed(() => {
  const row = props.row
  const names = row.platform === 'yandex'
    ? row.markets.map(market => props.shopNames[market.id]).filter(Boolean)
    : [props.storeName].filter(name => name && name !== row.account_id)
  const lines = entries.value.map(entry => `${entry.label}：${entry.value}`)
  if (names.length) lines.push(`关联店铺：${[...new Set(names)].join('、')}`)
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
