<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { fetchOrderDetail, procurementCommand } from '@/api/orders'
import type { OrderDetail, OrderSnapshot, ProcurementLine } from '@/types/orders'
import OrderSourceDialog from './OrderSourceDialog.vue'
import OrderAmountDetails from './OrderAmountDetails.vue'
import { PhDotsThree, PhCaretDown } from '@phosphor-icons/vue'
import AlibabaSelfPurchaseDialog from './AlibabaSelfPurchaseDialog.vue'
const props = defineProps<{
  item: ProcurementLine
  order: OrderSnapshot
}>()
const emit = defineEmits<{
  updated: [detail: OrderDetail]
  lock: [locked: boolean]
}>()
const error = ref('')
const notice = ref('')
const busy = ref(false)
const sourceEditor = ref(false)
const alibabaPurchaseOpen = ref(false)
const expanded = ref(false)
const selection = computed(() => props.item.selection)
const editable = computed(
  () =>
    !['cancelled', 'delivered'].includes(props.order.state) &&
    !!selection.value.line_key &&
    !selection.value.reason.includes('缺少唯一身份')
)
watch(
  () => busy.value || alibabaPurchaseOpen.value || sourceEditor.value,
  (value) => emit('lock', value)
)
onBeforeUnmount(() => emit('lock', false))
async function execute(action: 'select-source', body: Record<string, unknown>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    emit('updated', await procurementCommand(action, { order_id: props.order.id, ...body }))
    notice.value = '采购来源已确认'
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
async function refreshPurchase() {
  try { emit('updated', await fetchOrderDetail(props.order.id)) }
  catch (exc) { error.value = exc instanceof Error ? exc.message : '采购记录刷新失败，请重新打开订单' }
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
defineExpose({ showSource: () => { expanded.value = true } })
</script>
<template>
  <article class="order-procurement-line">
    <div class="procurement-sku-row" role="row">
      <div role="cell" class="sku-specification" data-label="规格">
        <strong :title="item.line.sku">{{ selection.source?.specification || item.line.sku || '规格未提供' }}</strong>
        <span class="order-muted" :title="item.line.sku">
          {{ item.line.sku || '销售 SKU 未提供' }}
        </span>
      </div>
      <div role="cell" class="sku-number" data-label="需求">
        {{ item.line.quantity }}
      </div>
      <div
        role="cell"
        class="sku-number"
        data-label="已登记"
        :data-complete="item.remaining_quantity === 0"
      >
        {{ item.purchased_quantity }} / {{ item.line.quantity }}
      </div>
      <div role="cell" data-label="采购来源">
        <button
          class="order-link sku-source"
          :data-confirmed="selection.status === 'confirmed'"
          :aria-expanded="expanded"
          @click="expanded = !expanded"
        >
          {{ selection.source?.source_platform || '来源' }} · {{ { unmatched: '待关联', matched: '待确认', ambiguous: '待选择', confirmed: '已确认' }[selection.status] }}
        </button>
      </div>
      <div role="cell" class="sku-actions" data-label="操作">
        <button
          v-if="order.state === 'pending_shipment' && item.remaining_quantity > 0 && selection.line_key"
          class="order-link"
          :disabled="busy"
          @click="alibabaPurchaseOpen = true"
        >
          1688 采购
        </button>
        <button
          v-else
          class="order-link"
          :aria-expanded="expanded"
          @click="expanded = !expanded"
        >
          来源详情<PhCaretDown :size="14" aria-hidden="true" />
        </button>
        <details class="order-more sku-more">
          <summary class="order-link" :aria-label="`${item.line.sku || item.line.title} 更多操作`">
            <PhDotsThree :size="22" aria-hidden="true" />
          </summary>
          <div class="order-menu">
            <button
              v-if="order.state === 'pending_shipment' && selection.line_key"
              class="order-link"
              :disabled="busy"
              @click="alibabaPurchaseOpen = true"
            >
              1688 采购
            </button>
            <button
              v-if="editable"
              class="order-link"
              :disabled="busy"
              @click="sourceEditor = true"
            >
              {{ selection.source ? '修改来源' : '关联采购来源' }}
            </button>
            <button class="order-link" @click="expanded = !expanded">
              {{ expanded ? '收起详情' : '来源与金额详情' }}
            </button>
          </div>
        </details>
      </div>
    </div>
    <section v-if="expanded" class="order-line-source" aria-label="采购来源">
      <div class="order-row">
        <h5>采购来源详情</h5><button class="order-link" @click="expanded = false">
          收起
        </button>
      </div>
      <template v-if="selection.source">
        <dl class="order-source-facts">
          <div><dt>销售 SKU</dt><dd>{{ item.line.sku || '未提供' }}</dd></div>
          <div><dt>采购平台</dt><dd>{{ [selection.source.source_platform, selection.source.supplier].filter(Boolean).join(' · ') || '未提供' }}</dd></div>
          <div><dt>采购规格</dt><dd>{{ selection.source.specification }}</dd></div>
          <div><dt>来源 SKU</dt><dd>{{ selection.source.source_sku_id || '未提供，请核对规格' }}</dd></div>
        </dl>
        <div class="order-actions">
          <a
            class="order-button"
            :href="selection.source.sku_url_verified ? selection.source.sku_url : selection.source.product_url"
            target="_blank"
            rel="noopener noreferrer"
            :title="selection.source.sku_url_verified ? '人工确认的 SKU 直达链接' : '打开商品页后，请核对上述规格'"
          >
            {{ selection.source.sku_url_verified ? '直达采购规格（人工确认）' : '打开采购商品' }}
          </a>
          <button class="order-link" @click="copySpecification">
            复制规格
          </button>
          <button
            v-if="editable"
            class="order-link"
            :disabled="busy"
            @click="sourceEditor = true"
          >
            修改来源
          </button>
        </div>
        <p v-if="selection.source.purchase_block_reason" class="order-error">
          {{ selection.source.purchase_block_reason }}
        </p>
      </template>
      <template v-else>
        <p class="order-muted mt-2">
          {{ selection.reason }}
        </p>
        <button v-if="editable" class="order-button mt-3" @click="sourceEditor = true">
          关联采购来源
        </button>
      </template>
      <div v-if="selection.status !== 'confirmed' && editable" class="order-source-confirmation">
        <p v-if="selection.source" class="order-muted">
          核对规格并确认来源后，可以发起采购。
        </p>
        <div v-for="candidate in selection.candidates" :key="candidate.id" class="order-source-candidate">
          <div v-if="selection.candidates.length > 1" class="order-muted">
            <p>{{ candidate.source.specification }} · 来源 SKU {{ candidate.source.source_sku_id }}</p><a :href="candidate.source.product_url" target="_blank" rel="noopener noreferrer">
              {{ candidate.source.supplier || candidate.source.source_platform }} · 查看候选商品
            </a>
          </div>
          <button class="order-button order-primary" :disabled="busy" @click="select(candidate.id)">
            {{ selection.candidates.length > 1 ? '确认此采购来源' : '确认来源' }}
          </button>
        </div>
      </div>
      <details class="order-line-amount">
        <summary>SKU 金额明细</summary><OrderAmountDetails :value="item.line" :platform="order.platform" />
      </details>
    </section>
    <p v-if="error" class="order-error" role="alert">
      {{ error }}
    </p>
    <p v-if="notice" class="order-success" role="status">
      {{ notice }}
    </p>
    <AlibabaSelfPurchaseDialog
      v-if="alibabaPurchaseOpen"
      :order-id="order.id"
      :line-key="selection.line_key"
      :title="item.line.title"
      @close="alibabaPurchaseOpen = false"
      @updated="refreshPurchase"
    />
    <OrderSourceDialog
      v-if="sourceEditor"
      :item="item"
      :order-id="order.id"
      @close="sourceEditor = false"
      @updated="$emit('updated', $event)"
    />
  </article>
</template>
<style scoped>
.order-procurement-line { min-width: 0; border-top: 1px solid var(--order-border); }
.procurement-sku-row { display: grid; grid-template-columns: minmax(0, 1fr) 40px 64px 104px 120px; align-items: center; gap: 8px; min-height: 32px; padding: 2px 0; }
.sku-specification { min-width: 0; display: grid; gap: 2px; }
.sku-specification strong { font-weight: 500; overflow-wrap: anywhere; }
.sku-specification .order-muted { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sku-number { text-align: center; font-variant-numeric: tabular-nums; }
.sku-number[data-complete='true'], .sku-source[data-confirmed='true'] { color: var(--order-positive); }
.order-link.sku-source { padding: 2px 0; white-space: normal; text-align: left; }
.sku-actions { display: flex; justify-content: flex-end; align-items: center; gap: 0; }
.sku-actions > .order-link { padding: 2px 0; display: flex; align-items: center; gap: 4px; }
.sku-more summary { padding: 2px 6px; }
.order-line-source { padding: 16px; margin: 0 0 12px; border: 1px solid var(--order-border); border-radius: 6px; background: var(--order-soft); }
h5 { font-weight: 600; }
.order-source-facts { display: grid; gap: 8px; margin: 12px 0; }
.order-source-facts > div { display: grid; grid-template-columns: 80px minmax(0, 1fr); gap: 8px; }
.order-source-facts dt { color: var(--order-muted); }
.order-source-facts dd { overflow-wrap: anywhere; }
.order-source-confirmation { display: grid; gap: 10px; margin-top: 12px; }
.order-source-candidate { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 10px; align-items: center; }
.order-line-amount { margin-top: 12px; }
.order-line-amount summary { cursor: pointer; color: var(--order-link-color); }
@media (min-width: 601px) {
  .sku-specification > .order-muted { display: none; }
}
@media (max-width: 600px) {
  .procurement-sku-row { grid-template-columns: 1fr 1fr; gap: 12px; padding: 16px 0; }
  .sku-specification { grid-column: 1 / -1; }
  .sku-number { display: flex; gap: 10px; text-align: left; }
  .sku-number::before { content: attr(data-label); color: var(--order-muted); font-size: 14px; }
}
</style>
