<script setup lang="ts">
import type { PurchaseRecord } from '@/types/orders'
import { dateTime } from './orderPresentation'
defineProps<{ records: PurchaseRecord[]; busy: boolean }>()
defineEmits<{ cancel: [record: PurchaseRecord] }>()
</script>
<template>
  <details class="order-purchase-history">
    <summary>采购记录 <span class="order-muted">{{ records.length }} 条（含作废）</span></summary>
    <section aria-label="采购记录">
      <article v-for="record in records" :key="record.id" class="order-record">
        <div class="order-row">
          <strong>{{ record.purchase_order_number }} · {{ record.quantity }} 件</strong>
          <span class="order-badge" :data-tone="record.status === 'cancelled' ? 'neutral' : 'green'">{{ record.status === 'cancelled' ? '已作废' : '已采购' }}</span>
        </div>
        <p class="order-muted">{{ record.source.supplier || record.source.source_platform }} · {{ record.source.specification }}</p>
        <p class="order-muted">来源 SKU：{{ record.source.source_sku_id || '未提供' }}</p>
        <div class="order-row mt-2">
          <span class="order-muted">{{ dateTime(record.created_at) }}</span>
          <div class="order-actions">
            <a :href="record.source.product_url" target="_blank" rel="noopener noreferrer" class="order-link">当时采购商品</a>
            <button v-if="record.status !== 'cancelled'" class="order-link" :disabled="busy" @click="$emit('cancel', record)">作废</button>
          </div>
        </div>
      </article>
    </section>
  </details>
</template>
<style scoped>
.order-purchase-history { border-top: 1px solid var(--order-border); padding: 12px 16px; }
summary { cursor: pointer; font-size: 12px; font-weight: 600; }
summary span { margin-left: 8px; font-weight: 400; }
.order-record:first-child { border-top: 0; }
.order-record:last-child { padding-bottom: 0; }
strong { overflow-wrap: anywhere; min-width: 0; }
</style>
