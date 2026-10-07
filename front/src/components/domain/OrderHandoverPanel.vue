<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { OrderHandoverShipment, OrderSnapshot } from '@/types/orders'
import { handoverDateTime } from './orderPresentation'
import OrderAddressNote from './OrderAddressNote.vue'

const props = defineProps<{ order: OrderSnapshot }>()
const emit = defineEmits<{ lock: [value: boolean] }>()
const result = computed(() => props.order.handover)
const notice = ref('')
const statuses: Record<string, string> = {
  OUTBOUND_CREATED: '批次准备中', OUTBOUND_READY_FOR_CONFIRMATION: '待确认',
  OUTBOUND_CONFIRMED: '已确认', OUTBOUND_SIGNED: '交接单已签署',
  ACCEPTED: '已接收', ACCEPTED_WITH_DISCREPANCIES: '接收有差异',
  FINISHED: '已完成', ERROR: '已取消（异常）',
}
function location(shipment: OrderHandoverShipment) {
  if (shipment.shipment_type === 'IMPORT') return shipment.destination
  if (shipment.shipment_type === 'WITHDRAW') return shipment.origin
  return null
}
async function copyAddress(shipment: OrderHandoverShipment) {
  const point = location(shipment)
  if (!point?.address) return
  try {
    await navigator.clipboard.writeText([point.name, point.address].filter(Boolean).join('\n'))
    notice.value = '交货地址已复制'
  } catch {
    notice.value = '复制失败，请手动选择地址文字复制'
  }
}
watch(() => props.order.id, () => { notice.value = '' })
</script>

<template>
  <section class="order-handover" aria-label="平台交货信息" data-testid="order-handover">
    <header class="order-row">
      <h3>平台交货信息</h3>
    </header>
    <p v-if="!result" class="order-muted mt-3">交货信息尚未同步，请同步订单后查看</p>
    <template v-else>
      <p v-if="result.message" class="order-muted mt-3">{{ result.message }}</p>
      <article v-for="shipment in result.shipments" :key="shipment.shipment_id" class="handover-shipment">
        <div class="order-row">
          <strong>发货单 {{ shipment.shipment_id }}</strong>
          <span class="order-badge" :data-tone="['ERROR', 'ACCEPTED_WITH_DISCREPANCIES'].includes(shipment.status) ? 'red' : 'neutral'" :title="shipment.status">{{ statuses[shipment.status] || '状态待确认' }}</span>
        </div>
        <p class="order-muted mt-2">{{ shipment.shipment_type === 'IMPORT' ? '自行送达交货点' : shipment.shipment_type === 'WITHDRAW' ? '平台上门揽收' : '平台未提供明确的交货方式' }}</p>
        <dl class="handover-facts">
          <div class="handover-address">
            <dt>{{ shipment.shipment_type === 'WITHDRAW' ? '揽收地址' : '交货地址' }}</dt>
            <dd>
              <p v-if="location(shipment)?.name" class="font-medium">{{ location(shipment)?.name }}</p>
              <p class="mt-1 whitespace-pre-wrap">{{ location(shipment)?.address || '平台尚未提供地址，请在 Yandex 后台核对' }}</p>
              <div v-if="location(shipment)?.address" class="order-actions mt-2">
                <button type="button" class="order-link" @click="copyAddress(shipment)">复制地址</button>
                <OrderAddressNote :key="`${order.id}:${shipment.shipment_id}:${location(shipment)?.address}`" :order-id="order.id" :shipment-id="shipment.shipment_id" :address="location(shipment)!.address" @lock="emit('lock', $event)" />
              </div>
            </dd>
          </div>
          <div><dt>计划交货开始</dt><dd>{{ handoverDateTime(shipment.planned_from) }}</dd></div>
          <div><dt>计划交货截止</dt><dd>{{ handoverDateTime(shipment.planned_to) }}</dd></div>
        </dl>
      </article>
      <p v-if="result.checked_at" class="order-muted mt-3">同步时间：{{ handoverDateTime(result.checked_at) }}</p>
    </template>
    <p v-if="notice" role="status" class="order-muted mt-2">{{ notice }}</p>
  </section>
</template>

<style scoped>
.order-handover { margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--order-border); }
h3 { font-size: 14px; font-weight: 600; }
.handover-shipment { margin-top: 12px; padding: 16px; border: 1px solid var(--order-border); border-radius: 8px; background: var(--order-soft); font-size: 12px; }
strong { overflow-wrap: anywhere; }
.handover-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-top: 16px; }
.handover-address { grid-column: 1 / -1; }
dt { color: var(--order-muted); }
dd { margin-top: 6px; overflow-wrap: anywhere; }
@media (max-width: 480px) { .handover-facts { grid-template-columns: 1fr; } }
</style>
