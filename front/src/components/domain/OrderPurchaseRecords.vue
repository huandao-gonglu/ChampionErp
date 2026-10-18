<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { PhCaretRight, PhDotsThree } from '@phosphor-icons/vue'
import type { OrderDetail, PurchaseRecord } from '@/types/orders'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { procurementCommand } from '@/api/orders'
import { dateTime } from './orderPresentation'
import { purchaseLogisticsLabel } from './orderDetailPresentation'
import OrderPurchaseTracking from './OrderPurchaseTracking.vue'
import OrderThumbnail from './OrderThumbnail.vue'
const props = defineProps<{ orderId: string; records: PurchaseRecord[]; busy: boolean }>()
const emit = defineEmits<{ updated: [detail: OrderDetail]; lock: [boolean] }>()
const queries = reactive(new Set<string>())
const cancelling = ref<PurchaseRecord | null>(null)
const saving = ref(false)
const error = ref('')
const showCancelled = ref(false)
const refreshingAll = ref(false)
const trackers = ref<InstanceType<typeof OrderPurchaseTracking>[]>([])
const cancelledCount = computed(() => props.records.filter(record => record.status === 'cancelled').length)
const visibleRecords = computed(() => props.records.filter(record => showCancelled.value || record.status !== 'cancelled'))
const lastChecked = computed(() => props.records.map(record => record.progress?.data?.checked_at || '').filter(Boolean).sort().at(-1))
let disposed = false
watch(() => !!cancelling.value || queries.size > 0 || refreshingAll.value, locked => emit('lock', locked), { flush: 'sync' })
async function cancel() {
  if (!cancelling.value || saving.value) return
  saving.value = true; error.value = ''
  try {
    emit('updated', await procurementCommand('cancel-purchase', { order_id: props.orderId, record_id: cancelling.value.id }))
    cancelling.value = null
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '作废失败' }
  finally { saving.value = false }
}
function queryLock(id: string, busy: boolean) {
  if (busy) queries.add(id)
  else queries.delete(id)
}
async function refreshAll() {
  if (props.busy || refreshingAll.value) return
  refreshingAll.value = true
  try {
    for (const tracker of trackers.value) {
      if (disposed) break
      const record = tracker.$props.record
      if (record.status !== 'cancelled' && record.source_platform === '1688' && !await tracker.refresh()) break
    }
  } finally { refreshingAll.value = false }
}
onBeforeUnmount(() => { disposed = true; emit('lock', false) })
defineExpose({ refreshAll })
</script>
<template>
  <section class="order-purchase-history detail-card" aria-label="采购记录">
    <header class="order-row purchase-heading">
      <h3>
        已登记采购单 <span class="order-muted">
          {{ records.length - cancelledCount }} 笔
        </span>
      </h3><button
        v-if="cancelledCount"
        class="order-link"
        :aria-pressed="showCancelled"
        @click="showCancelled = !showCancelled"
      >
        {{ showCancelled ? '隐藏作废记录' : `查看作废记录（${cancelledCount}）` }}
      </button>
    </header>
    <p v-if="!visibleRecords.length" class="order-muted empty-purchases">
      尚未登记采购单。
    </p>
    <details v-for="record in visibleRecords" :key="record.id" class="purchase-record">
      <summary class="record-summary">
        <PhCaretRight class="record-caret" :size="18" aria-hidden="true" />
        <span class="record-identity">
          <strong>{{ record.purchase_order_number }}</strong><span class="order-muted">
            {{ record.source_platform }} · {{ record.items.reduce((n, item) => n + item.quantity, 0) }} 件 · {{ record.items.length }} 个明细
          </span><span v-if="record.progress?.error || record.progress?.data?.logistics_warning" class="record-warning">
            刷新异常，请展开查看
          </span>
        </span>
        <span class="record-status">
          <span class="order-muted">
            采购状态
          </span><span :class="{ 'record-warning': record.status !== 'cancelled' }">
            {{ record.status === 'cancelled' ? '已作废' : record.progress?.data?.order?.status_label || '待查询' }}
          </span>
        </span>
        <span class="record-logistics">
          <span class="order-muted">
            国内物流
          </span><span>{{ purchaseLogisticsLabel(record) }}</span>
        </span>
        <span class="order-link record-detail-link">
          查看详情
        </span>
      </summary>
      <div class="record-body">
        <div class="record-products">
          <div class="order-row">
            <h4>本单商品明细</h4><details v-if="record.status !== 'cancelled'" class="order-more">
              <summary class="order-link" :aria-label="`采购单 ${record.purchase_order_number} 更多操作`">
                <PhDotsThree :size="22" aria-hidden="true" />
              </summary><div class="order-menu">
                <button class="order-link order-danger" :disabled="busy" @click="cancelling = record; error = ''">
                  作废
                </button>
              </div>
            </details>
          </div>
          <p v-if="!record.items.length" class="order-muted mt-3">
            商品明细待查询或补充
          </p>
          <div v-for="item in record.items" :key="item.id" class="record-product">
            <OrderThumbnail :src="item.image_url" :title="item.title || '采购商品'" />
            <div>
              <p>{{ item.title || '商品' }}</p><p class="order-muted">
                {{ item.source?.specification || item.sku || '规格未提供' }} · {{ item.quantity }} 件
              </p><details v-if="item.source">
                <summary class="order-link">
                  来源详情
                </summary><p class="order-muted">
                  {{ item.source.source_platform }} · {{ item.source.source_sku_id }}
                </p><a
                  class="order-link"
                  :href="item.source.product_url"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  打开采购商品
                </a>
              </details>
            </div>
          </div>
          <p v-if="record.note" class="record-note">
            采购备注：{{ record.note }}
          </p>
          <p class="order-muted mt-3">
            登记时间：{{ dateTime(record.created_at) }}<span v-if="record.status === 'cancelled'">
              · 作废时间：{{ dateTime(record.cancelled_at) }}
            </span>
          </p>
        </div>
        <aside class="record-shipping">
          <h4>本单国内物流</h4>
          <OrderPurchaseTracking
            ref="trackers"
            :order-id="orderId"
            :record="record"
            @updated="$emit('updated', $event)"
            @lock="queryLock(record.id, $event)"
          />
          <details v-if="record.receiving_warehouse" class="record-warehouse">
            <summary class="order-link">
              当时收货仓：{{ record.receiving_warehouse.warehouse_name }}
            </summary><div class="order-muted mt-2">
              <p>{{ record.receiving_warehouse.transport_service }}</p><p>{{ record.receiving_warehouse.address.fullName }} · {{ record.receiving_warehouse.address.mobile || record.receiving_warehouse.address.phone }}</p><p>{{ [record.receiving_warehouse.address.provinceText, record.receiving_warehouse.address.cityText, record.receiving_warehouse.address.areaText, record.receiving_warehouse.address.townText, record.receiving_warehouse.address.address].filter(Boolean).join(' ') }}</p>
            </div>
          </details>
        </aside>
      </div>
    </details>
    <p v-if="lastChecked" class="order-muted purchase-updated">
      采购进度更新于 {{ dateTime(lastChecked) }}
    </p>
  </section>
  <WorkspaceDialog
    v-if="cancelling"
    :open="true"
    title="作废采购登记"
    width="560px"
    class="order-ui"
    :close-disabled="saving"
    @close="cancelling = null"
  >
    <p>确认作废采购单 {{ cancelling.purchase_order_number }} 的本地登记？</p>
    <p class="order-muted mt-3">
      涉及 {{ cancelling.items.length }} 个明细、{{ cancelling.items.reduce((sum, item) => sum + item.quantity, 0) }} 件商品。采购历史、国内包裹和报单保留，此操作不会取消采购平台订单或退款。
    </p>
    <p v-if="error" class="order-error" role="alert">
      {{ error }}
    </p>
    <template #footer>
      <div class="order-footer">
        <button class="order-button" :disabled="saving" @click="cancelling = null">
          返回
        </button><button class="order-button order-danger" :disabled="saving" @click="cancel">
          确认作废
        </button>
      </div>
    </template>
  </WorkspaceDialog>
</template>
<style scoped>
h3 { font-weight: 600; font-size: 16px; }
h3 .order-muted { margin-left: 12px; font-weight: 400; }
h4 { font-weight: 600; }
.purchase-heading { margin-bottom: 10px; }
.purchase-record { border: 1px solid var(--order-border); border-radius: 6px; }
.purchase-record + .purchase-record { margin-top: 4px; }
.record-summary { display: grid; grid-template-columns: 18px minmax(0, 1fr) 132px 110px 68px; gap: 12px; align-items: center; padding: 6px 12px; cursor: pointer; list-style: none; }
.record-summary::-webkit-details-marker { display: none; }
.record-summary strong { display: block; font-size: 14px; font-weight: 600; overflow-wrap: anywhere; }
.record-identity, .record-status, .record-logistics { min-width: 0; display: grid; gap: 2px; font-size: 14px; }
.record-status, .record-logistics { padding-left: 12px; border-left: 1px solid var(--order-border); }
.record-detail-link { padding: 0; font-size: 14px; }
.purchase-record[open] > summary .record-caret { transform: rotate(90deg); }
.record-body { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); gap: 16px; padding: 12px; border-top: 1px solid var(--order-border); background: var(--order-soft); }
.record-products, .record-shipping { min-width: 0; }
.record-shipping { border-left: 1px solid var(--order-border); padding-left: 16px; }
.record-shipping :deep(.purchase-tracking) { margin-top: 4px; padding-top: 0; border-top: 0; }
.record-product { display: flex; gap: 12px; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--order-border); }
.record-product > div { min-width: 0; overflow-wrap: anywhere; }
.record-product summary, .record-warehouse > summary { padding-left: 0; }
.record-note { margin-top: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
.record-warehouse { margin-top: 12px; }
.record-warning { color: var(--order-warning); font-size: 14px; }
.purchase-updated { margin-top: 8px; }
.empty-purchases { padding: 12px 0; }
@media (max-width: 600px) {
  .record-body { grid-template-columns: minmax(0, 1fr); }
  .record-shipping { border-left: 0; border-top: 1px solid var(--order-border); padding-left: 0; padding-top: 12px; }
  .record-summary { grid-template-columns: 18px minmax(0, 1fr) 68px; }
  .record-identity { grid-column: 2; }
  .record-detail-link { grid-column: 3; grid-row: 1; }
  .record-status, .record-logistics { grid-column: 2 / -1; display: flex; flex-wrap: wrap; gap: 12px; border: 0; padding-left: 0; }
}
</style>
