<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { procurementCommand } from '@/api/orders'
import type { OrderDetail, OrderSnapshot, ProcurementLine, PurchaseRecord } from '@/types/orders'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import OrderSourceDialog from './OrderSourceDialog.vue'
import OrderAmountDetails from './OrderAmountDetails.vue'
import OrderThumbnail from './OrderThumbnail.vue'
import OrderPurchaseRecords from './OrderPurchaseRecords.vue'
const props = defineProps<{
  item: ProcurementLine
  order: OrderSnapshot
}>()
const emit = defineEmits<{
  updated: [detail: OrderDetail]
  purchase: [key: string]
  lock: [locked: boolean]
}>()
const error = ref('')
const notice = ref('')
const busy = ref(false)
const sourceEditor = ref(false)
const cancelledRecord = ref<PurchaseRecord | null>(null)
const selection = computed(() => props.item.selection)
const editable = computed(
  () =>
    !['cancelled', 'delivered'].includes(props.order.state) &&
    !!selection.value.line_key &&
    !selection.value.reason.includes('缺少唯一身份')
)
watch(
  () => busy.value || sourceEditor.value || !!cancelledRecord.value,
  (value) => emit('lock', value)
)
onBeforeUnmount(() => emit('lock', false))
async function execute(action: 'select-source' | 'cancel-purchase', body: Record<string, unknown>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    emit('updated', await procurementCommand(action, { order_id: props.order.id, ...body }))
    cancelledRecord.value = null
    notice.value = action === 'select-source' ? '采购来源已确认' : '采购记录已作废'
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '采购操作失败，请刷新后重试'
  } finally {
    busy.value = false
  }
}
function select(candidateId: string) {
  return execute('select-source', {
    line_key: selection.value.line_key,
    revision: selection.value.revision,
    candidate_id: candidateId,
  })
}
async function copySpecification() {
  try {
    await navigator.clipboard.writeText(
      selection.value.source?.specification || props.item.line.title
    )
    notice.value = '规格已复制'
  } catch {
    error.value = '复制失败，请手动复制规格文字'
  }
}
</script>
<template>
  <article class="order-procurement-line">
    <header class="order-line-product">
      <OrderThumbnail :src="item.line.image_url" :title="item.line.title || '商品'" large />
      <div class="order-line-identity">
        <h4 :title="item.line.title">{{ item.line.title || '暂无商品标题' }}</h4>
        <p class="order-muted">销售 SKU：{{ item.line.sku || '未提供' }}</p>
        <div class="order-line-quantities">
          <span>订购 <strong>{{ item.line.quantity }}</strong> 件</span>
          <span class="order-badge" :data-tone="item.remaining_quantity ? 'amber' : 'green'">已采购 {{ item.purchased_quantity }} / {{ item.line.quantity }}</span>
        </div>
      </div>
    </header>
    <details class="order-line-amount">
      <summary>SKU 金额明细 <span class="order-muted">该 SKU 全部数量的小计</span></summary>
      <OrderAmountDetails :value="item.line" :platform="order.platform" />
    </details>
    <section class="order-line-source" aria-label="采购来源">
      <div class="order-row">
        <h5>采购来源</h5>
        <div class="order-actions">
          <span class="order-source-state" :data-confirmed="selection.status === 'confirmed'">{{
            { unmatched: '待关联', matched: '待确认', ambiguous: '待选择', confirmed: '已确认' }[selection.status]
          }}</span>
          <button v-if="selection.source && editable" class="order-link" :disabled="busy" @click="sourceEditor = true">修改来源</button>
        </div>
      </div>
      <template v-if="selection.source">
        <dl class="order-source-facts">
          <div><dt>采购平台</dt><dd>{{ [selection.source.source_platform, selection.source.supplier].filter(Boolean).join(' · ') || '未提供' }}</dd></div>
          <div><dt>采购规格</dt><dd class="order-specification">{{ selection.source.specification }}</dd></div>
          <div><dt>来源 SKU：</dt><dd>{{ selection.source.source_sku_id || '未提供，请核对规格' }}</dd></div>
        </dl>
        <div class="order-actions order-source-tools">
          <a
            class="order-button"
            :href="selection.source.sku_url_verified ? selection.source.sku_url : selection.source.product_url"
            target="_blank" rel="noopener noreferrer"
            :title="selection.source.sku_url_verified ? '人工确认的 SKU 直达链接' : '打开商品页后，请核对上述规格'"
          >{{ selection.source.sku_url_verified ? '直达采购规格（人工确认）' : '打开采购商品' }}</a>
          <button class="order-link" @click="copySpecification">复制规格</button>
        </div>
      </template>
      <template v-else>
        <p class="order-muted mt-2">{{ selection.reason }}</p>
        <button v-if="editable" class="order-button mt-3" @click="sourceEditor = true">关联采购来源</button>
      </template>
      <div v-if="selection.status !== 'confirmed' && editable" class="order-source-confirmation">
        <p v-if="selection.source" class="order-muted">核对上方规格后确认来源，即可登记采购。</p>
        <div v-for="candidate in selection.candidates" :key="candidate.id" class="order-source-candidate">
          <div v-if="selection.candidates.length > 1" class="order-muted">
            <p>{{ candidate.source.specification }} · 来源 SKU {{ candidate.source.source_sku_id }}</p>
            <a :href="candidate.source.product_url" target="_blank" rel="noopener noreferrer">{{ candidate.source.supplier || candidate.source.source_platform }} · 查看候选商品</a>
          </div>
          <button class="order-button order-primary" :disabled="busy" @click="select(candidate.id)">{{ selection.candidates.length > 1 ? '确认此采购来源' : '确认来源' }}</button>
        </div>
      </div>
      <div v-if="selection.status === 'confirmed'" class="order-line-purchase">
        <p class="order-muted">{{ item.remaining_quantity > 0 ? `还需采购 ${item.remaining_quantity} 件` : '所需数量已登记齐全' }}</p>
        <button
          v-if="order.state === 'pending_shipment' && item.remaining_quantity > 0"
          class="order-button order-primary" :disabled="busy"
          @click="$emit('purchase', selection.line_key)"
        >
          记录采购
        </button>
      </div>
      <p v-if="error" class="order-error" role="alert">{{ error }}</p>
      <p v-if="notice" class="order-success" role="status">{{ notice }}</p>
    </section>
    <OrderPurchaseRecords v-if="item.records.length" :records="item.records" :busy="busy" @cancel="cancelledRecord = $event" />
    <OrderSourceDialog
      v-if="sourceEditor"
      :item="item"
      :order-id="order.id"
      @close="sourceEditor = false"
      @updated="$emit('updated', $event)"
    />
    <WorkspaceDialog
      v-if="cancelledRecord"
      :open="true"
      title="作废采购记录"
      width="496px"
      class="order-ui"
      :close-disabled="busy"
      @close="cancelledRecord = null"
    >
      <p class="font-semibold">
        {{ cancelledRecord?.purchase_order_number }} · {{ cancelledRecord?.quantity }} 件
      </p>
      <p class="order-muted mt-3">
        仅撤销 ERP 采购记录，不会取消采购平台订单。原记录及采购规格将保留，作废后可重新记录采购。
      </p>
      <p v-if="error" class="order-error" role="alert">{{ error }}</p>
      <template #footer>
        <div class="order-footer">
          <button class="order-button" :disabled="busy" @click="cancelledRecord = null">取消</button><button
            class="order-button order-danger"
            :disabled="busy"
            @click="execute('cancel-purchase', { record_id: cancelledRecord?.id })"
          >
            确认作废
          </button>
        </div>
      </template>
    </WorkspaceDialog>
  </article>
</template>

<style scoped>
.order-procurement-line { border: 1px solid var(--order-border); border-radius: 8px; overflow: hidden; background: var(--order-surface); }
.order-line-product { display: flex; align-items: flex-start; gap: 14px; padding: 16px; }
.order-line-identity { min-width: 0; flex: 1; }
h4 { font-weight: 600; line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
.order-line-identity > p { margin-top: 5px; overflow-wrap: anywhere; }
.order-line-quantities { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-top: 8px; font-size: 12px; }
.order-line-amount { padding: 0 16px 12px; font-size: 12px; }
.order-line-amount summary { cursor: pointer; color: var(--order-muted); }
.order-line-amount summary span { margin-left: 6px; font-size: 11px; }
.order-line-source { padding: 12px 16px 16px; background: var(--order-soft); border-top: 1px solid var(--order-border); }
h5 { font-size: 12px; font-weight: 600; }
.order-source-state { font-size: 11px; color: #b45309; }
.order-source-state[data-confirmed='true'] { color: #15803d; }
.order-source-facts { display: grid; gap: 8px; margin: 12px 0; }
.order-source-facts > div { display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: 8px; }
.order-source-facts dt { font-size: 12px; color: var(--order-muted); }
.order-source-facts dd { font-size: 12px; overflow-wrap: anywhere; }
.order-source-facts .order-specification { font-size: 13px; font-weight: 500; }
.order-source-tools .order-button { padding: 5px 12px; font-size: 12px; }
.order-source-confirmation { display: grid; gap: 10px; margin-top: 12px; }
.order-source-confirmation:empty { display: none; }
.order-source-candidate { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 10px; align-items: center; }
.order-line-purchase { display: flex; justify-content: space-between; align-items: center; gap: 12px; border-top: 1px solid var(--order-border); margin-top: 14px; padding-top: 12px; }
@media (max-width: 480px) {
  .order-line-product { gap: 10px; padding: 12px; }
  .order-line-source { padding: 12px; }
  .order-source-facts > div { grid-template-columns: 64px minmax(0, 1fr); }
}
</style>
