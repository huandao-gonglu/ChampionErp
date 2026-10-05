<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { fetchOrderDetail } from '@/api/orders'
import type { OrderDetail } from '@/types/orders'
import { orderPlatformNames, orderStateNames } from '@/types/orders'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import OrderAmountDetails from './OrderAmountDetails.vue'
import OrderProcurementLine from './OrderProcurementLine.vue'
import OrderPurchaseDialog from './OrderPurchaseDialog.vue'
import { deadline, stateTone } from './orderPresentation'
const props = defineProps<{ orderId: string }>()
const emit = defineEmits<{ back: []; updated: [] }>()
const detail = ref<OrderDetail | null>(null)
const error = ref('')
const loading = ref(false)
const locks = reactive(new Set<string>())
const purchaseKey = ref('')
const purchaseItem = computed(() =>
  detail.value?.lines.find((line) => line.selection.line_key === purchaseKey.value)
)
const eligible = computed(() =>
  detail.value?.order.state === 'pending_shipment'
    ? detail.value.lines.filter(
        (line) => line.selection.status === 'confirmed' && line.remaining_quantity > 0
      )
    : []
)
const allPurchased = computed(
  () =>
    !!detail.value?.lines.length &&
    detail.value.lines.every((line) => line.remaining_quantity === 0 && line.line.quantity > 0)
)
const footerHint = computed(() => {
  if (!detail.value) return '正在读取订单'
  if (allPurchased.value) return '采购已记录，平台履约状态保持独立。'
  if (detail.value.order.state !== 'pending_shipment') return '当前平台状态不支持新增采购记录。'
  return eligible.value.length
    ? '在采购平台下单后，回到这里记录。'
    : '先确认采购来源，再记录已采购数量。'
})
const locked = computed(() => locks.size > 0 || !!purchaseKey.value)
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
    width="656px"
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
          <span class="order-badge" :data-tone="stateTone(detail.order.state)">{{
            orderStateNames[detail.order.state]
          }}</span>
        </div>
        <p class="order-muted mt-1">
          {{ orderPlatformNames[detail.order.platform] }} /
          {{ detail.order.fulfillment || '履约未提供' }}
        </p>
        <div
          v-if="detail.order.state === 'pending_shipment'"
          class="order-row order-deadline"
          :class="{ urgent: shipment.urgent }"
        >
          <span>发货截止 {{ shipment.text }}</span><span>{{ shipment.note }}</span>
        </div>
        <div class="order-detail-amount">
          <OrderAmountDetails :value="detail.order" :platform="detail.order.platform" />
        </div>
      </header>
      <h3 class="font-semibold mt-5">商品与采购</h3>
      <OrderProcurementLine
        v-for="(line, index) in detail.lines"
        :key="`${line.selection.line_key}:${index}`"
        :item="line"
        :order="detail.order"
        :show-purchase-action="detail.lines.length > 1"
        @updated="update"
        @purchase="purchaseKey = $event"
        @lock="setLock(`${line.selection.line_key}:${index}`, $event)"
      />
      <p v-if="!detail.lines.length" class="order-muted py-8">
        平台尚未提供商品明细，请同步订单后重试。
      </p>
    </template>
    <template #footer>
      <div class="order-row">
        <p class="order-muted">
          {{ footerHint }}
        </p>
        <button
          v-if="allPurchased"
          class="order-button order-primary"
          :disabled="locked"
          @click="$emit('back')"
        >
          完成
        </button><button
          v-else
          class="order-button order-primary"
          :disabled="!eligible.length || locked || loading"
          @click="purchaseKey = eligible[0]!.selection.line_key"
        >
          记录采购
        </button>
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
h2 {
  font-size: 23px;
  font-weight: 700;
}
.order-deadline {
  margin-top: 16px;
  padding: 10px 12px;
  border-radius: 5px;
  background: var(--order-soft);
  font-size: 12px;
}
.order-deadline.urgent {
  background: #fffbeb;
  color: #b45309;
}
.order-detail-amount {
  padding: 16px 0;
  border-bottom: 1px solid var(--order-border);
}
.order-detail-amount :deep(.mt-2) {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  margin: 0;
}
.order-detail-amount :deep(.font-semibold) {
  font-size: 23px;
}
.order-detail-amount :deep(.muted) {
  grid-column: 1 / -1;
  margin-top: 6px;
  font-size: 12px;
  color: var(--order-muted);
}
.order-detail :deep(.order-procurement-line:last-child) {
  border-bottom: 0;
}
</style>
