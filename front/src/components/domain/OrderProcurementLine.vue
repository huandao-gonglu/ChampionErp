<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { procurementCommand } from '@/api/orders'
import type { OrderDetail, OrderSnapshot, ProcurementLine, PurchaseRecord } from '@/types/orders'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import OrderSourceDialog from './OrderSourceDialog.vue'
import OrderAmountDetails from './OrderAmountDetails.vue'
import { dateTime } from './orderPresentation'
const props = defineProps<{
  item: ProcurementLine
  order: OrderSnapshot
  showPurchaseAction?: boolean
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
  <article class="order-procurement-line order-section">
    <div class="order-row">
      <div>
        <h4 class="font-semibold">{{ item.line.title || '暂无商品标题' }}</h4>
        <p class="order-muted mt-1">
          {{ item.line.sku || 'SKU 未提供' }} · {{ item.line.quantity }} 件
        </p>
      </div>
      <span class="order-badge" :data-tone="item.remaining_quantity ? 'amber' : 'green'">已采购 {{ item.purchased_quantity }} / {{ item.line.quantity }}</span>
    </div>
    <details class="order-muted mt-2">
      <summary class="cursor-pointer">SKU 金额明细</summary>
      <OrderAmountDetails :value="item.line" :platform="order.platform" />
      <p>该 SKU 全部数量的小计</p>
    </details>
    <section class="order-source" aria-label="采购来源">
      <div class="order-row">
        <h5 class="font-semibold">采购来源</h5>
        <span
          class="order-badge"
          :data-tone="selection.status === 'confirmed' ? 'green' : 'amber'"
        >{{
          {
            unmatched: '待关联',
            matched: '待确认',
            ambiguous: '待选择',
            confirmed: '已确认',
          }[selection.status]
        }}</span>
      </div>
      <template v-if="selection.source">
        <p>
          {{
            [selection.source.source_platform, selection.source.supplier]
              .filter(Boolean)
              .join(' · ') || '采购商品'
          }}
        </p>
        <p class="font-medium">{{ selection.source.specification }}</p>
        <p class="order-muted">
          来源 SKU：{{ selection.source.source_sku_id || '未提供，请核对规格' }}
        </p>
        <div class="order-row mt-4">
          <div class="order-actions">
            <a
              class="order-button"
              :href="
                selection.source.sku_url_verified
                  ? selection.source.sku_url
                  : selection.source.product_url
              "
              target="_blank"
              rel="noopener noreferrer"
              :title="
                selection.source.sku_url_verified
                  ? '人工确认的 SKU 直达链接'
                  : '打开商品页后，请核对上述规格'
              "
            >{{
              selection.source.sku_url_verified ? '直达采购规格（人工确认）' : '打开采购商品'
            }}</a><button class="order-button" @click="copySpecification">复制规格</button>
          </div>
          <button v-if="editable" class="order-link" :disabled="busy" @click="sourceEditor = true">
            修改来源
          </button>
        </div>
      </template>
      <template v-else>
        <p class="order-muted">{{ selection.reason }}</p>
        <button v-if="editable" class="order-button mt-3" @click="sourceEditor = true">
          关联采购来源
        </button>
      </template>
    </section>
    <template v-if="selection.status !== 'confirmed' && editable">
      <p v-if="selection.source" class="order-muted mt-5">核对商品与规格后，确认采购来源。</p>
      <div v-for="candidate in selection.candidates" :key="candidate.id" class="mt-4">
        <div v-if="selection.candidates.length > 1" class="order-muted mb-2">
          <p>
            {{ candidate.source.specification }} · 来源 SKU
            {{ candidate.source.source_sku_id }}
          </p>
          <a :href="candidate.source.product_url" target="_blank" rel="noopener noreferrer">{{ candidate.source.supplier || candidate.source.source_platform }} · 查看候选商品</a>
        </div>
        <button class="order-button order-primary" :disabled="busy" @click="select(candidate.id)">
          {{ selection.candidates.length > 1 ? '确认此采购来源' : '确认来源' }}
        </button>
      </div>
    </template>
    <button
      v-if="
        showPurchaseAction &&
          selection.status === 'confirmed' &&
          order.state === 'pending_shipment' &&
          item.remaining_quantity > 0
      "
      class="order-button order-primary mt-4"
      :disabled="busy"
      @click="$emit('purchase', selection.line_key)"
    >
      记录采购
    </button>
    <p v-if="error" class="order-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="order-success" role="status">{{ notice }}</p>
    <section v-if="item.records.length" class="mt-6" aria-label="采购记录">
      <h5 class="font-semibold mb-3">采购记录</h5>
      <article v-for="record in item.records" :key="record.id" class="order-record">
        <div class="order-row">
          <strong>{{ record.purchase_order_number }} · {{ record.quantity }} 件</strong><span
            class="order-badge"
            :data-tone="record.status === 'cancelled' ? 'neutral' : 'green'"
          >{{ record.status === 'cancelled' ? '已作废' : '已采购' }}</span>
        </div>
        <p class="order-muted">
          {{ record.source.supplier || record.source.source_platform }} ·
          {{ record.source.specification }} · 来源 SKU
          {{ record.source.source_sku_id }}
        </p>
        <div class="order-row mt-2">
          <span class="order-muted">{{ dateTime(record.created_at) }}</span>
          <div class="order-actions">
            <a
              :href="record.source.product_url"
              target="_blank"
              rel="noopener noreferrer"
              class="order-link"
            >当时采购商品</a><button
              v-if="record.status !== 'cancelled'"
              class="order-link"
              :disabled="busy"
              @click="cancelledRecord = record"
            >
              作废
            </button>
          </div>
        </div>
      </article>
    </section>
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
