<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { fetchOrderDetail } from '@/api/orders'
import type { OrderDetail } from '@/types/orders'
import { orderPlatformNames } from '@/types/orders'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import OrderAmountDetails from './OrderAmountDetails.vue'
import OrderProcurementLine from './OrderProcurementLine.vue'
import OrderPurchaseDialog from './OrderPurchaseDialog.vue'
import OrderFulfillmentPanel from './OrderFulfillmentPanel.vue'
import OrderHandoverPanel from './OrderHandoverPanel.vue'
import { amountLabel, deadline, money, platformStatusLabel, platformStatusNote, stateTone } from './orderPresentation'
const props = defineProps<{ orderId: string }>()
const activeTab = ref<'procurement' | 'fulfillment'>('procurement')
const fulfillmentLocked = ref(false)
const emit = defineEmits<{ back: []; updated: [] }>()
const detail = ref<OrderDetail | null>(null)
const error = ref('')
const loading = ref(false)
const locks = reactive(new Set<string>())
const purchaseKey = ref('')
const purchaseItem = computed(() =>
  detail.value?.lines.find((line) => line.selection.line_key === purchaseKey.value)
)
const totals = computed(() => ({
  ordered: detail.value?.lines.reduce((sum, line) => sum + line.line.quantity, 0) || 0,
  purchased: detail.value?.lines.reduce((sum, line) => sum + line.purchased_quantity, 0) || 0,
}))
const locked = computed(() => locks.size > 0 || !!purchaseKey.value || fulfillmentLocked.value)
const shipment = computed(() => deadline(detail.value?.order.shipment_deadline, Date.now()))
async function load() {
  if (loading.value) return
  loading.value = true
  try {
    detail.value = await fetchOrderDetail(props.orderId)
    error.value = ''
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '订单详情读取失败'
  } finally {
    loading.value = false
  }
}
function update(value: OrderDetail) {
  detail.value = value
  emit('updated')
}
function setLock(key: string, value: boolean) {
  if (value) locks.add(key)
  else locks.delete(key)
}
onMounted(load)
</script>
<template>
  <WorkspaceDialog
    :open="true"
    title="订单详情"
    variant="drawer"
    width="720px"
    class="order-ui order-detail"
    :close-disabled="locked"
    @close="$emit('back')"
  >
    <p v-if="error" role="alert" class="order-error">
      {{ error }}
      <button class="order-link" :disabled="loading" @click="load">重试</button>
    </p>
    <p v-if="loading" role="status" class="order-muted">正在读取订单…</p>
    <template v-if="detail">
      <header>
        <div class="order-row">
          <h2>{{ detail.order.order_id }}</h2>
          <span class="order-badge" :data-tone="stateTone(detail.order.state)" :title="`${detail.order.status} ${detail.order.shipping_status}`">{{
            platformStatusLabel(detail.order)
          }}</span>
        </div>
        <p class="order-muted mt-1">
          {{ orderPlatformNames[detail.order.platform] }} /
          {{ detail.order.delivery?.fulfillment_model || detail.order.fulfillment || '履约未提供' }}
        </p>
        <p
          v-if="platformStatusNote(detail.order)"
          class="order-muted mt-1"
        >
          {{ platformStatusNote(detail.order) }}
        </p>
        <dl v-if="activeTab === 'procurement'" class="order-overview">
          <div>
            <dt>{{ amountLabel(detail.order, detail.order.platform) }}</dt>
            <dd>{{ money(detail.order.amount) }} <small>{{ detail.order.currency }}</small></dd>
          </div>
          <div :class="{ urgent: detail.order.state === 'pending_shipment' && shipment.urgent }">
            <dt>{{ detail.order.platform === 'yandex' ? '发货日期' : '发货截止' }}</dt>
            <dd>{{ shipment.text }}<span class="order-overview-note order-muted">{{ shipment.note }}</span></dd>
          </div>
          <div>
            <dt>采购登记数量</dt>
            <dd>{{ totals.purchased }} <small>/ {{ totals.ordered }} 件</small><span class="order-overview-note order-muted">{{ detail.lines.length }} 个 SKU</span></dd>
          </div>
        </dl>
        <details v-if="activeTab === 'procurement'" class="order-financial-details">
          <summary>查看金额明细</summary>
          <OrderAmountDetails :value="detail.order" :platform="detail.order.platform" />
        </details>
      </header>
      <nav class="order-actions my-6" aria-label="订单详情内容">
        <button class="order-button" :class="{ 'order-primary': activeTab === 'procurement' }" :disabled="locked" @click="activeTab = 'procurement'">商品与采购</button>
        <button class="order-button" :class="{ 'order-primary': activeTab === 'fulfillment' }" :disabled="locked" @click="activeTab = 'fulfillment'">跨境履约</button>
      </nav>
      <OrderFulfillmentPanel v-if="activeTab === 'fulfillment'" :order="detail" @purchase-updated="update" @updated="load(); emit('updated')" @lock="fulfillmentLocked = $event" />
      <div v-if="activeTab === 'procurement'" class="order-section-heading">
        <h3>商品与采购</h3>
        <span class="order-muted">逐项核对规格并登记采购</span>
      </div>
      <div v-if="activeTab === 'procurement'" class="order-product-list">
        <OrderProcurementLine
          v-for="(line, index) in detail.lines"
          :key="`${line.selection.line_key}:${index}`"
          :item="line"
          :order="detail.order"
          @updated="update"
          @purchase="purchaseKey = $event"
          @lock="setLock(`${line.selection.line_key}:${index}`, $event)"
        />
      </div>
      <p v-if="activeTab === 'procurement' && !detail.lines.length" class="order-muted py-8">
        平台尚未提供商品明细，请同步订单后重试。
      </p>
      <OrderHandoverPanel
        v-if="detail.order.platform === 'yandex' && (detail.order.delivery?.fulfillment_model || detail.order.fulfillment).toUpperCase() === 'FBS'"
        v-show="activeTab === 'procurement'"
        :order="detail.order"
        @lock="setLock('address-note', $event)"
      />
    </template>
    <template #footer>
      <div class="order-actions justify-end">
        <button class="order-button" :disabled="locked" @click="$emit('back')">关闭详情</button>
      </div>
    </template>
  </WorkspaceDialog>
  <OrderPurchaseDialog
    v-if="purchaseItem"
    :item="purchaseItem"
    :order-id="orderId"
    @updated="update"
    @close="purchaseKey = ''"
  />
</template>
<style scoped>
h2 { font-size: 20px; font-weight: 700; overflow-wrap: anywhere; }
.order-overview {
  display: grid;
  grid-template-columns: 1.2fr 1fr 0.8fr;
  gap: 16px;
  margin-top: 20px;
  padding: 16px;
  border: 1px solid var(--order-border);
  border-radius: 8px;
  background: var(--order-soft);
}
.order-overview > div { min-width: 0; }
.order-overview dt { color: var(--order-muted); font-size: 11px; }
.order-overview dd { margin-top: 8px; font-size: 17px; font-weight: 600; overflow-wrap: anywhere; }
.order-overview small { font-size: 12px; font-weight: 400; }
.order-overview-note { display: block; font-weight: 400; }
.urgent dd { color: #b45309; }
.order-financial-details { margin-top: 12px; font-size: 12px; }
.order-financial-details summary { cursor: pointer; color: var(--order-muted); }
.order-financial-details :deep(.mt-2) { padding: 12px; background: var(--order-soft); border-radius: 6px; }
.order-section-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 24px 0 12px; }
.order-section-heading h3 { font-size: 14px; font-weight: 600; }
.order-product-list { display: grid; gap: 16px; }
@media (max-width: 480px) {
  .order-overview { grid-template-columns: 1fr 1fr; gap: 16px 12px; }
  .order-overview > div:first-child { grid-column: 1 / -1; }
  .order-section-heading { flex-wrap: wrap; }
}
</style>
