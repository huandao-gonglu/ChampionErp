<script setup lang="ts">
import type { PrecheckIssue } from '@/types/workflow'

defineProps<{ skus?: PrecheckIssue['affectedSkus'] }>()
</script>

<template>
  <details v-if="skus?.length" class="mt-2 text-xs">
    <summary class="cursor-pointer font-medium">受影响的 {{ skus.length }} 个 SKU</summary>
    <ul class="mt-2 max-h-48 space-y-1 overflow-auto pl-4" aria-label="受影响的 SKU">
      <li v-for="sku in skus" :key="sku.skuId" class="break-words">
        {{ sku.name || sku.sku }}<span v-if="sku.name && sku.sku" class="ml-2 font-mono">{{ sku.sku }}</span>
      </li>
    </ul>
  </details>
</template>
