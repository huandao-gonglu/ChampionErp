<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, useId, watch } from 'vue'
import { PhX, PhDotsThree, PhCaretDown, PhPackage, PhCheckCircle } from '@phosphor-icons/vue'
import { useAiPageContext } from '@/composables/useAiPageContext'
import { fetchOrderDetail } from '@/api/orders'
import type { OrderDetail } from '@/types/orders'
import { orderPlatformNames } from '@/types/orders'
import type { ReceivingWarehouseView } from '@/api/receivingWarehouse'
import OrderThumbnail from './OrderThumbnail.vue'
import { procurementTask } from './orderDetailPresentation'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import OrderAmountDetails from './OrderAmountDetails.vue'
import OrderProcurementLine from './OrderProcurementLine.vue'
import OrderPurchaseDialog from './OrderPurchaseDialog.vue'
import OrderPurchaseRecords from './OrderPurchaseRecords.vue'
import AlibabaBatchPurchaseDialog from './AlibabaBatchPurchaseDialog.vue'
import OrderFulfillmentPanel from './OrderFulfillmentPanel.vue'
import OrderReceivingWarehouse from './OrderReceivingWarehouse.vue'
import OrderHandoverPanel from './OrderHandoverPanel.vue'
import OrderChatDialog from './OrderChatDialog.vue'
import { amountLabel, deadline, money, orderProcessingProgress, platformStatusLabel, platformStatusNote, stateTone } from './orderPresentation'
import type { OrderDetailTab } from './orderPresentation'
const props = withDefaults(defineProps<{ orderId: string; tab?: OrderDetailTab }>(), { tab: 'procurement' })
const activeTab = ref<OrderDetailTab>(props.tab)
const fulfillmentLocked = ref(false)
const emit = defineEmits<{ back: []; updated: []; tabChange: [tab: OrderDetailTab] }>()
const detail = ref<OrderDetail | null>(null)
useAiPageContext(() => ({ page: 'orders', order_id: props.orderId }), 10)
const error = ref('')
const loading = ref(false)
const locks = reactive(new Set<string>())
const purchaseOpen = ref(false)
const batchPurchaseOpen = ref(false)
const chatOpen = ref(false)
const infoOpen = ref(false)
const receiving = ref<ReceivingWarehouseView | null>(null)
const receivingPanel = ref<InstanceType<typeof OrderReceivingWarehouse> | null>(null)
const purchaseRecords = ref<InstanceType<typeof OrderPurchaseRecords> | null>(null)
const procurementLines = ref<InstanceType<typeof OrderProcurementLine>[]>([])
const productsElement = ref<HTMLElement | null>(null)
const fulfillmentFooterId = `order-fulfillment-footer-${useId()}`
const fulfillmentTask = ref<{ title: string; note: string; tone: string } | null>(null)
const isYandexFbs = computed(() => detail.value?.order.platform === 'yandex' && (detail.value.order.delivery?.fulfillment_model || detail.value.order.fulfillment).toUpperCase() === 'FBS')
const task = computed(() => detail.value ? procurementTask(detail.value) : null)
const currentTask = computed(() => {
  if (!detail.value || activeTab.value === 'procurement') return task.value
  const progress = orderProcessingProgress(detail.value.order)
  return fulfillmentTask.value || { title: progress.label, note: progress.note, tone: progress.tone }
})
const confirmedSources = computed(() => detail.value?.lines.filter(item => item.selection.status === 'confirmed').length || 0)
const canRefreshPurchases = computed(() => detail.value?.purchases.some(record => record.status !== 'cancelled' && record.source_platform === '1688'))
const primaryPurchaseLabel = computed(() => {
  if (isYandexFbs.value && (!receiving.value?.warehouse || receiving.value.binding_stale)) return '核对收货安排'
  if (confirmedSources.value < (detail.value?.lines.length || 0)) return '确认采购来源'
  return isYandexFbs.value ? '1688 批量采购' : '登记采购单'
})
async function showSources() {
  procurementLines.value.forEach(line => line.showSource())
  await nextTick()
  productsElement.value?.scrollIntoView?.({ block: 'start', behavior: 'smooth' })
}
function startPurchase() {
  if (locked.value) return
  if (primaryPurchaseLabel.value === '核对收货安排') receivingPanel.value?.open()
  else if (primaryPurchaseLabel.value === '确认采购来源') void showSources()
  else if (isYandexFbs.value) batchPurchaseOpen.value = true
  else purchaseOpen.value = true
}

const productGroups = computed(() => {
  const groups = new Map<string, NonNullable<typeof detail.value>['lines']>()
  for (const [index, line] of (detail.value?.lines || []).entries()) {
    const key = line.product_id || `line:${index}`
    groups.set(key, [...(groups.get(key) || []), line])
  }
  return [...groups.entries()].map(([id, lines]) => ({ id, lines, title: lines[0]!.line.title }))
})
const totals = computed(() => ({
  ordered: detail.value?.lines.reduce((sum, line) => sum + line.line.quantity, 0) || 0,
  purchased: detail.value?.lines.reduce((sum, line) => sum + line.purchased_quantity, 0) || 0,
}))
const locked = computed(() => chatOpen.value || infoOpen.value || batchPurchaseOpen.value || locks.size > 0 || purchaseOpen.value || fulfillmentLocked.value)
watch(() => props.tab, tab => { if (!locked.value) activeTab.value = tab })
function selectTab(tab: OrderDetailTab) {
  if (locked.value) return
  activeTab.value = tab
  emit('tabChange', tab)
}
function navigateTabs(event: KeyboardEvent) {
  if (locked.value || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const tab = event.key === 'Home' ? 'procurement' : event.key === 'End' ? 'fulfillment' : activeTab.value === 'procurement' ? 'fulfillment' : 'procurement'
  selectTab(tab)
  void nextTick(() => document.getElementById(`order-${tab}-tab`)?.focus())
}
const shipment = computed(() => deadline(detail.value?.order.shipment_deadline, Date.now()))
let poll: ReturnType<typeof setTimeout> | undefined
let disposed = false
let reading = false
let generation = 0
async function load(quiet = false) {
  if (reading || disposed) return
  const current = ++generation
  reading = true
  loading.value = !quiet
  try {
    const result = await fetchOrderDetail(props.orderId)
    if (disposed || current !== generation || (quiet && locked.value)) return
    detail.value = result
    error.value = ''
  } catch (exc) {
    if (!disposed && current === generation && !quiet) error.value = exc instanceof Error ? exc.message : '订单详情读取失败'
  } finally {
    reading = false
    loading.value = false
  }
}
function update(value: OrderDetail) {
  generation++
  detail.value = value
  emit('updated')
}
function setLock(key: string, value: boolean) {
  if (value) locks.add(key)
  else locks.delete(key)
}
async function refreshLocal() {
  if (disposed) return
  if (!locked.value) await load(true)
  if (!disposed) poll = setTimeout(refreshLocal, 5000)
}
onMounted(() => { void load(); poll = setTimeout(refreshLocal, 5000) })
onBeforeUnmount(() => { disposed = true; generation++; clearTimeout(poll) })
</script>
<template>
  <WorkspaceDialog
    :open="true"
    title="订单详情"
    :modal="false"
    variant="drawer"
    width="1120px"
    class="order-ui order-detail"
    :close-disabled="locked"
    @close="$emit('back')"
  >
    <template #header="{ titleId }">
      <div class="detail-header">
        <div class="detail-identity">
          <span :id="titleId" class="detail-heading-label">
            订单详情
          </span>
          <h2>{{ detail?.order.order_id || orderId }}</h2>
          <span v-if="detail" class="order-muted">
            {{ orderPlatformNames[detail.order.platform] }} · {{ detail.order.shop_name || detail.order.account_id }} · {{ detail.order.delivery?.fulfillment_model || detail.order.fulfillment || '履约未提供' }}
          </span>
          <span
            v-if="detail"
            class="order-badge"
            :data-tone="stateTone(detail.order.state)"
            :title="`${detail.order.status} ${detail.order.shipping_status}`"
          >
            {{ platformStatusLabel(detail.order) }}
          </span>
        </div>
        <div v-if="detail" class="detail-header-actions">
          <div class="detail-deadline" :data-urgent="detail.order.state === 'pending_shipment' && shipment.urgent" :title="[shipment.note, shipment.text].filter(Boolean).join(' · ')">
            <span class="order-muted">
              发货期限
            </span><strong>{{ shipment.text }}</strong>
          </div>
          <button class="order-link" :disabled="locked" @click="infoOpen = true">
            订单资料
          </button>
          <details v-if="detail.order.platform === 'yandex'" class="order-more">
            <summary class="order-link" aria-label="订单更多操作">
              <PhDotsThree :size="22" aria-hidden="true" />
            </summary><div class="order-menu">
              <button class="order-link" :disabled="locked" @click="chatOpen = true">
                联系买家
              </button>
            </div>
          </details>
        </div>
      </div>
    </template>
    <template #close>
      <PhX :size="22" aria-hidden="true" />
    </template>
    <template #toolbar>
      <nav
        class="detail-tabs"
        aria-label="订单详情内容"
        role="tablist"
        @keydown="navigateTabs"
      >
        <button
          id="order-procurement-tab"
          class="order-tab"
          :class="{ 'order-primary': activeTab === 'procurement' }"
          role="tab"
          :tabindex="activeTab === 'procurement' ? 0 : -1"
          :aria-selected="activeTab === 'procurement'"
          aria-controls="order-procurement-content"
          :disabled="locked"
          @click="selectTab('procurement')"
        >
          商品与采购
        </button>
        <button
          id="order-fulfillment-tab"
          class="order-tab"
          :class="{ 'order-primary': activeTab === 'fulfillment' }"
          role="tab"
          :tabindex="activeTab === 'fulfillment' ? 0 : -1"
          :aria-selected="activeTab === 'fulfillment'"
          aria-controls="order-fulfillment-content"
          :disabled="locked"
          @click="selectTab('fulfillment')"
        >
          跨境履约
        </button>
      </nav>
      <div
        v-if="currentTask"
        class="detail-task"
        :data-tone="currentTask.tone"
        aria-live="polite"
      >
        <PhPackage :size="26" aria-hidden="true" /><strong>{{ currentTask.title }}</strong><span class="order-muted">
          {{ currentTask.note }}
        </span>
      </div>
    </template>
    <p v-if="error" role="alert" class="order-error">
      {{ error }} <button class="order-link" :disabled="loading" @click="load()">
        重试
      </button>
    </p>
    <p v-if="loading" role="status" class="order-muted">
      正在读取订单…
    </p>
    <template v-if="detail">
      <div
        v-if="activeTab === 'procurement'"
        id="order-procurement-content"
        class="procurement-layout"
        role="tabpanel"
        aria-labelledby="order-procurement-tab"
      >
        <div class="procurement-main">
          <section ref="productsElement" class="detail-card order-product-list" aria-label="本订单商品">
            <div class="order-row product-heading">
              <h3>
                本订单商品 <span class="order-muted">
                  {{ productGroups.length }} 商品 · {{ detail.lines.length }} SKU · {{ totals.ordered }} 件
                </span>
              </h3><details v-if="detail.order.state === 'pending_shipment'" class="order-more">
                <summary class="order-button">
                  {{ task?.action === 'purchase' ? '采购操作' : '补购 / 登记' }}<PhCaretDown :size="14" aria-hidden="true" />
                </summary><div class="order-menu">
                  <button
                    v-if="isYandexFbs"
                    class="order-link"
                    :disabled="locked"
                    @click="batchPurchaseOpen = true"
                  >
                    1688 批量采购
                  </button><button class="order-link" :disabled="locked" @click="purchaseOpen = true">
                    登记采购单
                  </button>
                </div>
              </details>
            </div>
            <details
              v-for="group in productGroups"
              :key="group.id"
              class="procurement-product"
              open
              aria-label="订单商品"
            >
              <summary class="product-group-heading">
                <OrderThumbnail :src="group.lines[0]?.line.image_url" :title="group.title || '商品'" large /><div>
                  <h4>{{ group.title || '暂无商品标题' }}</h4><p class="order-muted">
                    {{ group.lines.length }} 个规格<span v-if="group.lines.some(line => line.remaining_quantity > 0)">
                      · 待采购 {{ group.lines.reduce((sum, line) => sum + line.remaining_quantity, 0) }} 件
                    </span>
                  </p>
                </div><PhCaretDown :size="18" aria-hidden="true" />
              </summary>
              <div class="sku-table" role="table" :aria-label="`${group.title} 规格与采购`">
                <div class="sku-columns" role="row">
                  <span role="columnheader">
                    规格
                  </span><span role="columnheader">
                    需求
                  </span><span role="columnheader">
                    已登记
                  </span><span role="columnheader">
                    采购来源
                  </span><span role="columnheader">
                    操作
                  </span>
                </div>
                <OrderProcurementLine
                  v-for="(line, index) in group.lines"
                  :key="`${line.selection.line_key}:${index}`"
                  ref="procurementLines"
                  :item="line"
                  :order="detail.order"
                  @updated="update"
                  @lock="setLock(`${group.id}:${index}`, $event)"
                />
              </div>
            </details>
            <p v-if="!detail.lines.length" class="order-muted py-8">
              平台尚未提供商品明细，请同步订单后重试。
            </p>
          </section>
          <OrderPurchaseRecords
            ref="purchaseRecords"
            :order-id="orderId"
            :records="detail.purchases"
            :busy="locked"
            @updated="update"
            @lock="setLock('purchases', $event)"
          />
        </div>
        <aside class="procurement-sidebar detail-card" aria-label="收货与交货">
          <h3>收货与交货</h3>
          <OrderReceivingWarehouse
            v-if="isYandexFbs"
            ref="receivingPanel"
            compact
            :order-id="detail.order.id"
            :line-key="detail.lines[0]?.selection.line_key || ''"
            :purchase-state="JSON.stringify(detail.purchases.map(record => [record.id, record.status]))"
            @loaded="receiving = $event"
            @lock="setLock('receiving-warehouse', $event)"
            @updated="load(); emit('updated')"
          />
          <OrderHandoverPanel
            v-if="isYandexFbs"
            compact
            :order="detail.order"
            @lock="setLock('address-note', $event)"
          />
          <section class="sidebar-fact">
            <h4>采购来源</h4><p>{{ confirmedSources }} / {{ detail.lines.length }} SKU 已确认</p><button class="order-link" @click="showSources">
              查看来源
            </button>
          </section>
          <section class="sidebar-fact">
            <h4>订单金额</h4><p>{{ money(detail.order.amount) }} {{ detail.order.currency }}</p><details>
              <summary class="order-link">
                金额明细
              </summary><p class="order-muted">
                {{ amountLabel(detail.order, detail.order.platform) }}
              </p><OrderAmountDetails :value="detail.order" :platform="detail.order.platform" />
            </details>
          </section>
        </aside>
      </div>
      <OrderFulfillmentPanel
        v-else
        id="order-fulfillment-content"
        role="tabpanel"
        aria-labelledby="order-fulfillment-tab"
        :order="detail"
        :footer-target="`#${fulfillmentFooterId}`"
        embedded
        @progress="fulfillmentTask = $event"
        @purchase-updated="update"
        @updated="load(); emit('updated')"
        @lock="fulfillmentLocked = $event"
      />
    </template>
    <template #footer>
      <div v-if="detail && activeTab === 'procurement'" class="detail-task-footer">
        <div class="footer-task-copy">
          <PhCheckCircle
            v-if="task?.tone === 'green'"
            :size="32"
            weight="fill"
            aria-hidden="true"
          /><PhPackage v-else :size="28" aria-hidden="true" /><div>
            <strong>{{ task?.footer }}</strong><p class="order-muted">
              {{ task?.next }}
            </p>
          </div>
        </div>
        <div class="order-actions">
          <button
            v-if="canRefreshPurchases && task?.action !== 'refresh'"
            class="order-button"
            :disabled="locked"
            @click="purchaseRecords?.refreshAll()"
          >
            刷新采购进度
          </button>
          <button
            v-if="task?.action === 'purchase'"
            class="order-button"
            :disabled="locked"
            @click="purchaseOpen = true"
          >
            登记采购单
          </button>
          <button
            v-if="task?.action === 'purchase'"
            class="order-button order-primary"
            :disabled="locked"
            @click="startPurchase"
          >
            {{ primaryPurchaseLabel }}
          </button>
          <button
            v-else-if="task?.action === 'refresh'"
            class="order-button order-primary"
            :disabled="locked || !canRefreshPurchases"
            :title="canRefreshPurchases ? '' : '当前采购单不支持在线刷新，请展开采购单核对'"
            @click="purchaseRecords?.refreshAll()"
          >
            刷新采购进度
          </button>
          <button
            v-else
            class="order-button order-primary"
            :disabled="locked"
            @click="selectTab('fulfillment')"
          >
            去跨境履约
          </button>
        </div>
      </div>
      <div v-else :id="fulfillmentFooterId" class="fulfillment-footer-target" />
    </template>
  </WorkspaceDialog>
  <WorkspaceDialog
    v-if="infoOpen && detail"
    :open="true"
    title="订单资料"
    width="640px"
    class="order-ui"
    :close-disabled="locks.has('info-address-note')"
    @close="infoOpen = false"
  >
    <dl class="order-info">
      <div><dt>平台订单号</dt><dd>{{ detail.order.order_id }}</dd></div><div>
        <dt>平台状态</dt><dd>
          {{ platformStatusLabel(detail.order) }}<p v-if="platformStatusNote(detail.order)" class="order-muted">
            {{ platformStatusNote(detail.order) }}
          </p><p class="order-muted">
            {{ detail.order.status }} / {{ detail.order.shipping_status }}
          </p>
        </dd>
      </div><div><dt>发货期限</dt><dd>{{ shipment.text }} {{ shipment.note }}</dd></div><div><dt>采购登记数量</dt><dd>{{ totals.purchased }} / {{ totals.ordered }} 件</dd></div>
    </dl>
    <details class="mt-4">
      <summary class="order-link">
        金额明细
      </summary><OrderAmountDetails :value="detail.order" :platform="detail.order.platform" />
    </details>
    <OrderHandoverPanel v-if="isYandexFbs" :order="detail.order" @lock="setLock('info-address-note', $event)" />
  </WorkspaceDialog>
  <OrderChatDialog
    v-if="chatOpen && detail"
    :key="orderId"
    :order-id="orderId"
    :order-number="detail.order.order_id"
    @close="chatOpen = false"
  />
  <AlibabaBatchPurchaseDialog
    v-if="batchPurchaseOpen"
    :order-id="orderId"
    @close="batchPurchaseOpen = false"
    @updated="load(); emit('updated')"
  />
  <OrderPurchaseDialog
    v-if="purchaseOpen && detail"
    :order="detail"
    @updated="update"
    @close="purchaseOpen = false"
  />
</template>
<style scoped>
.detail-header { display: flex; align-items: center; justify-content: space-between; gap: 20px; flex: 1; min-width: 0; }
.detail-identity { display: flex; align-items: center; flex-wrap: wrap; gap: 10px 14px; min-width: 0; }
.detail-heading-label { font-size: 14px; font-weight: 600; white-space: nowrap; }
h2 { font-size: 20px; font-weight: 700; overflow-wrap: anywhere; }
.detail-header-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.detail-deadline { display: flex; align-items: center; gap: 8px; padding-right: 14px; border-right: 1px solid var(--order-border); }
.detail-deadline strong { font-size: 14px; font-weight: 500; }
.detail-deadline[data-urgent='true'] strong { color: var(--order-warning); }
.detail-tabs { display: flex; align-items: stretch; gap: 24px; padding: 0 24px; border-bottom: 1px solid var(--order-border); }
.order-tab { padding: 10px 12px 8px; border-bottom: 3px solid transparent; color: var(--order-muted); font-size: 14px; background: transparent; }
.order-tab.order-primary { border-color: var(--order-positive); color: var(--order-text); font-weight: 600; background: transparent; }
.order-tab.order-primary:hover { background: transparent; }
.detail-task { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 16px; min-height: 48px; padding: 10px 20px; border-bottom: 1px solid var(--order-border); background: var(--order-soft); }
.detail-task > svg { color: var(--order-positive); flex-shrink: 0; }
.detail-task[data-tone='amber'] > svg { color: var(--order-warning); }
.detail-task[data-tone='red'] > svg { color: var(--order-negative); }
.detail-task strong { font-size: 16px; font-weight: 600; }
.procurement-layout { display: grid; grid-template-columns: minmax(0, 1fr) 296px; gap: 16px; align-items: start; }
.procurement-main { display: grid; gap: 12px; min-width: 0; }
.product-heading { margin-bottom: 10px; }
h3 { font-size: 16px; font-weight: 600; }
.product-heading h3 .order-muted { margin-left: 12px; font-weight: 400; white-space: nowrap; }
.procurement-product + .procurement-product { border-top: 1px solid var(--order-border); padding-top: 12px; margin-top: 12px; }
.product-group-heading { position: relative; display: flex; gap: 12px; align-items: center; min-height: 22px; padding-left: 88px; list-style: none; cursor: pointer; }
.product-group-heading > .order-thumbnail { position: absolute; top: 0; left: 0; }
.procurement-product:not([open]) > .product-group-heading { min-height: 72px; }
.product-group-heading::-webkit-details-marker { display: none; }
.product-group-heading > div:not(.order-thumbnail) { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 12px; flex: 1; min-width: 0; }
h4 { font-weight: 600; overflow-wrap: anywhere; }
.product-group-heading > svg { flex-shrink: 0; transition: transform .15s; }
.procurement-product:not([open]) > summary > svg { transform: rotate(-90deg); }
.sku-table { margin-top: 4px; margin-left: 88px; }
.sku-columns { display: grid; grid-template-columns: minmax(0, 1fr) 40px 64px 104px 120px; gap: 8px; padding: 2px 0; font-size: 12px; color: var(--order-muted); }
.sku-columns > span:nth-child(2), .sku-columns > span:nth-child(3) { text-align: center; }
.sku-columns > span:last-child { text-align: right; padding-right: 16px; }
.procurement-sidebar > h3 { padding-bottom: 8px; margin-bottom: 8px; border-bottom: 1px solid var(--order-border); }
.sidebar-fact { padding-top: 12px; margin-top: 12px; border-top: 1px solid var(--order-border); }
.sidebar-fact p { margin-top: 6px; }
.sidebar-fact .order-link { display: inline-block; padding: 4px 0 0; }
.detail-task-footer { display: flex; justify-content: space-between; align-items: center; gap: 16px; min-height: 44px; }
.footer-task-copy { display: flex; align-items: center; gap: 12px; min-width: 0; }
.footer-task-copy > svg { color: var(--order-positive); flex-shrink: 0; }
.footer-task-copy strong { font-size: 14px; font-weight: 600; }
.order-info { display: grid; gap: 16px; }
.order-info > div { display: grid; grid-template-columns: 100px minmax(0, 1fr); gap: 12px; }
.order-info dt { color: var(--order-muted); }
.order-info dd { overflow-wrap: anywhere; }
@media (max-width: 1023px) {
  .procurement-layout { grid-template-columns: minmax(0, 1fr); }
  .detail-header { flex-wrap: wrap; gap: 12px; }
  .procurement-sidebar { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
  .procurement-sidebar > h3 { grid-column: 1 / -1; margin: 0; }
  .procurement-sidebar .sidebar-fact { margin: 0; }
}
@media (max-width: 600px) {
  .detail-header-actions { flex-wrap: wrap; gap: 4px; }
  .detail-identity { gap: 8px 12px; }
  .detail-deadline { display: flex; align-items: center; gap: 8px; }
  .detail-tabs { gap: 16px; padding: 0 16px; }
  .detail-task { padding: 12px 16px; }
  .detail-task .order-muted { flex-basis: 100%; }
  .product-heading { align-items: flex-start; }
  .product-heading h3 .order-muted { display: block; margin: 4px 0 0; }
  .sku-columns { display: none; }
  .product-group-heading { padding-left: 0; min-height: 72px; }
  .product-group-heading > .order-thumbnail { position: static; }
  .sku-table { margin-left: 0; }
  .procurement-sidebar { grid-template-columns: minmax(0, 1fr); }
  .detail-task-footer { flex-wrap: wrap; }
  .detail-task-footer > .order-actions { width: 100%; justify-content: flex-end; }
  .footer-task-copy p { display: none; }
}
</style>
