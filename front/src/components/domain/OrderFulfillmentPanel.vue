<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { PhCopy, PhCaretDown, PhFileText, PhPackage, PhCheckCircle } from '@phosphor-icons/vue'
import { useAppStore } from '@/stores/app'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import OrderReceivingWarehouse from './OrderReceivingWarehouse.vue'
import FulfillmentParcelDialog from './FulfillmentParcelDialog.vue'
import FulfillmentPlanDialog from './FulfillmentPlanDialog.vue'
import { fetchFulfillment, fulfillmentCommand, uploadFulfillmentLabel } from '@/api/fulfillment'
import type { FulfillmentAction } from '@/api/fulfillment'
import type { DomesticParcel, FulfillmentDetail } from '@/types/fulfillment'
import { fulfillmentStatusNames } from '@/types/fulfillment'
import type { OrderDetail } from '@/types/orders'
import { orderProcessingProgress, procurementProgress } from './orderPresentation'
import { fulfillmentReadiness } from './orderDetailPresentation'
import { domesticWaybillKey, parcelError } from './purchaseLogistics'
const props = defineProps<{ order: OrderDetail; footerTarget?: string; embedded?: boolean }>()
const emit = defineEmits<{ updated: []; lock: [boolean]; purchaseUpdated: [OrderDetail]; progress: [{ title: string; note: string; tone: string }] }>()
const value = ref<FulfillmentDetail | null>(null)
const app = useAppStore()
watch(() => value.value?.label_error, (message, previous) => {
  if (message && message !== previous) app.pushToast(`${message} 请点击“重新获取面单”重试。`, 'error')
})
const error = ref('')
const pending = ref(false)
const refreshing = ref(false)
const modal = ref<'' | 'label' | 'parcels' | 'plan' | 'cancel' | 'error' | 'tracking' | 'history' | 'plan-details'>('')
const purchaseLocks = reactive(new Set<string>())
function purchaseLock(id: string, busy: boolean) {
  if (busy) purchaseLocks.add(id)
  else purchaseLocks.delete(id)
}
watch(() => pending.value || refreshing.value || !!modal.value || purchaseLocks.size > 0, busy => emit('lock', busy), { flush: 'sync' })
const labelUrl = ref('')
const trackingNumber = ref('')
const country = ref('')
let poll: ReturnType<typeof setTimeout> | undefined
let loadGeneration = 0
let keepEditing: ReturnType<typeof setInterval> | undefined
let disposed = false
const settingsRoute = { path: '/', query: { tab: 'auth', auth_section: 'crossborderbus' } }
const progress = computed(() => orderProcessingProgress({
  ...props.order.order,
  procurement_status: procurementProgress(props.order.lines),
}, value.value))
const status = computed(() => value.value ? progress.value.label : '')
const tone = computed(() => progress.value.tone)
const title = computed(() => value.value?.busy && value.value.operation === 'create' ? '正在提交预报' : value.value?.busy && value.value.operation === 'fetch-label' ? '正在获取平台面单' : value.value?.create_unknown ? '创建结果待核实，暂停重复提交' : value.value?.cancel_requested && value.value.fulfillment_status === 'SHIPPED' ? '仓库已发货，取消未成功' : value.value?.cancel_rejected && value.value.fulfillment_status !== 'CANCELLED' ? '仓库拒绝取消，请处理后重试' : value.value?.cancel_requested && value.value.fulfillment_status !== 'CANCELLED' ? '已请求取消，等待仓库确认' : value.value?.error_message ? '履约操作需要处理' : status.value)
const parcelGroups = computed(() => (value.value?.parcels || []).filter(p => p.included))
const parcelCount = computed(() => parcelGroups.value.length)
const focusedParcelId = ref('')
const trackingParcel = ref<DomesticParcel | null>(null)
const readinessPanel = ref<HTMLElement | null>(null)
const copyNotice = ref('')
const readiness = computed(() => value.value ? fulfillmentReadiness(value.value, props.order.order.platform === 'yandex') : [])
const missing = computed(() => readiness.value.filter(item => !item.ready))
const remoteActive = computed(() => !!value.value?.crossborderbus_order_id && value.value.fulfillment_status !== 'CANCELLED')
const canSubmit = computed(() => value.value && !pending.value && !value.value.busy && !value.value.editing &&
  !value.value.blocked_reason && !missing.value.length && !value.value.create_unknown && !value.value.cancel_requested)
const planAllowed = computed(() => !!value.value?.plan_editable && (props.order.order.platform === 'yandex' || !!value.value.rule))
const queryAllowed = computed(() => !pending.value && !refreshing.value && !value.value?.busy && (!!value.value?.crossborderbus_order_id || !!value.value?.create_unknown))
const queryLabel = computed(() => value.value?.create_unknown ? '核实创建结果' : value.value?.cancel_requested && value.value.fulfillment_status !== 'CANCELLED' ? '核实取消结果' : '查询仓库状态')
function parcelLogistics(parcel: DomesticParcel) {
  const bill = domesticWaybillKey(parcel.tracking_number)
  return props.order.purchases.flatMap(record => (record.progress?.data?.logistics || [])
    .filter(logistics => domesticWaybillKey(logistics.tracking_number) === bill)
    .map(logistics => ({ ...logistics, checkedAt: record.progress?.data?.logistics_checked_at || record.progress?.data?.checked_at || '', warning: record.progress?.data?.logistics_warning || record.progress?.error || '' })))
    .sort((a, b) => b.checkedAt.localeCompare(a.checkedAt))[0]
}
const tracking = computed(() => trackingParcel.value ? parcelLogistics(trackingParcel.value) : undefined)
async function setIncluded(id: string | null, included: boolean) {
  if (!value.value?.editable || pending.value) return
  await command('parcels', { parcels: value.value.parcels.map(parcel => ({ ...parcel, included: id === null || parcel.id === id ? included : parcel.included })), confirm_parcels: false })
}
async function copyWaybill(parcel: DomesticParcel) {
  try { await navigator.clipboard.writeText(parcel.tracking_number); copyNotice.value = `运单 ${parcel.tracking_number} 已复制` }
  catch { copyNotice.value = '复制失败，请手动复制运单号。' }
}
function editParcel(id = '') { focusedParcelId.value = id; void open('parcels') }
function viewTracking(parcel: DomesticParcel) { trackingParcel.value = parcel; void open('tracking') }
function showReadiness() { readinessPanel.value?.scrollIntoView?.({ block: 'start', behavior: 'smooth' }); readinessPanel.value?.focus() }

const notice = computed(() => {
  const current = value.value
  if (current?.error_message) return current.error_message
  if (current?.fulfillment_status === 'CANCELLED') {
    if (props.order.order.state !== 'pending_shipment') return '原履约已取消，当前平台订单不支持重新报单。'
    return current.receiving_binding_stale
      ? '原履约已取消，引用的仓库配置也已变化。请重新确认收货绑定，再设置报单方案并提交。'
      : '原履约已取消，收货仓绑定保留。需要重报时，请设置本次报单方案，再核对面单、国内包裹并提交。'
  }
  if (current?.awaiting_resubmission) return current.blocked_reason || '收货仓和报单服务已重新确认，请核对保留的面单、国内包裹后点击“提交至合作仓”。'
  return current?.crossborderbus_order_id ? current.fulfillment_status === 'SHIPPED' ? '仓库已发货，ERP 履约状态已同步为已发货。' : '仓库进度随同步订单更新，也可查询仓库状态单独刷新。' : current?.blocked_reason || (current?.editing ? '修改本单方案期间已暂停自动预报。' : progress.value.note)
})
watch([title, () => props.order.order.purchase_tracking, () => props.order.purchases, () => parcelCount.value], () => {
  const logistics = [...new Set(parcelGroups.value.map(parcel => parcelLogistics(parcel)?.status_label).filter(Boolean))].join('、')
  emit('progress', { title: status.value === '待报单' ? '准备跨境报单' : title.value,
    note: [logistics ? `国内${logistics}` : '暂无国内物流状态', value.value?.crossborderbus_order_id ? `跨境预报 ${value.value.fulfillment_status === 'CANCELLED' ? '已作废' : '已创建'}` : '跨境预报尚未创建'].join(' · '), tone: tone.value })
})

async function load(quiet = false) {
  const generation = ++loadGeneration
  try {
    const result = await fetchFulfillment(props.order.order.id)
    if (!disposed && generation === loadGeneration) { value.value = result; if (!quiet) error.value = '' }
  } catch (exc) { if (!disposed && generation === loadGeneration && (!quiet || !error.value)) error.value = exc instanceof Error ? exc.message : '履约详情读取失败' }
}
async function command(action: FulfillmentAction, body: Record<string, unknown> = {}) {
  if (!value.value || pending.value) return false
  loadGeneration++
  pending.value = true; error.value = ''
  try {
    const result = await fulfillmentCommand(action, value.value.erp_order_id, value.value.revision, body)
    if (!disposed) { value.value = result; emit('updated') }
    return true
  } catch (exc) { const message = exc instanceof Error ? exc.message : '履约操作失败'; await load(); error.value = message; return false }
  finally { pending.value = false }
}
async function open(which: typeof modal.value) {
  if (!value.value) return
  error.value = ''
  if (which === 'plan' && value.value.fulfillment_status !== 'CANCELLED') {
    if (!await command('pause')) return
    keepEditing = setInterval(() => { if (modal.value === 'plan' && !pending.value) void command('pause') }, 60_000)
  }
  if (which === 'label') { labelUrl.value = value.value.platform_label; trackingNumber.value = value.value.platform_tracking_number; country.value = value.value.country }
  modal.value = which
}
async function close() {
  if (pending.value) return
  clearInterval(keepEditing)
  if (modal.value === 'plan' && value.value?.fulfillment_status !== 'CANCELLED' && !await command('resume')) return
  modal.value = ''
}
async function uploadLabel(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !value.value || pending.value) return
  pending.value = true
  error.value = ''
  try {
    labelUrl.value = await uploadFulfillmentLabel(value.value.erp_order_id, value.value.revision, file)
    await load()
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '面单上传失败' }
  finally { pending.value = false; input.value = '' }
}
async function saveLabel() { if (await command('label', { label: { url: labelUrl.value, tracking_number: trackingNumber.value }, country: country.value.trim().toUpperCase() })) void close() }
function planSaved(result: FulfillmentDetail) { clearInterval(keepEditing); loadGeneration++; value.value = result; modal.value = ''; emit('updated') }
function parcelsSaved(result: FulfillmentDetail) { loadGeneration++; value.value = result; modal.value = ''; emit('updated') }
async function cancel() { if (await command('cancel')) void close() }
function at(date: string) { return date ? new Date(date).toLocaleString('zh-CN') : '尚未同步' }
async function syncLatest() {
  if (pending.value || refreshing.value) return
  refreshing.value = true
  try {
    await load()
    if (!disposed && !error.value && !modal.value && value.value && !value.value.busy && (value.value.crossborderbus_order_id || value.value.create_unknown)) await command('sync')
  } finally { refreshing.value = false }
}
async function refreshLocal() {
  if (disposed) return
  if (!pending.value && !refreshing.value && !modal.value && !purchaseLocks.size) await load(true)
  if (!disposed) poll = setTimeout(refreshLocal, 5000)
}
onMounted(() => {
  void load()
  poll = setTimeout(refreshLocal, 5000)
})
onBeforeUnmount(() => { disposed = true; clearTimeout(poll); clearInterval(keepEditing); emit('lock', false) })
</script>
<template>
  <div class="fulfillment-panel">
    <p v-if="error" class="order-error" role="alert">
      {{ error }} <button class="order-link" :disabled="pending" @click="load()">
        刷新
      </button>
    </p>
    <p v-if="!value" class="order-muted py-8" role="status">
      正在读取履约资料…
    </p>
    <template v-if="value">
      <div v-if="!embedded" class="order-row fulfillment-context">
        <h3>{{ status === '待报单' ? '准备跨境报单' : title }}</h3><span class="order-badge" :data-tone="tone">
          {{ status }}
        </span>
      </div>
      <section
        v-if="value.error_message || value.fulfillment_status === 'CANCELLED' || value.cancel_requested || value.awaiting_resubmission"
        class="fulfillment-notice"
        :data-tone="tone"
        aria-live="polite"
      >
        <div class="order-row">
          <h3>{{ title }}</h3><button v-if="value.error_message" class="order-link" @click="open('error')">
            查看详情
          </button>
        </div><p>{{ notice }}</p>
      </section>
      <div class="fulfillment-layout">
        <section class="detail-card fulfillment-main" aria-label="包裹与仓库动态">
          <div class="order-row">
            <h3>
              本次报单包裹 <span class="order-muted">
                {{ value.parcels.length }} 个包裹
              </span>
            </h3><button
              v-if="value.editable"
              class="order-button"
              :disabled="pending"
              @click="editParcel()"
            >
              {{ value.parcels.length ? '管理包裹' : '录入包裹' }}
            </button>
          </div>
          <p class="parcel-selection" :data-confirmed="value.parcels_confirmed" aria-live="polite">
            已选 {{ parcelCount }} 个 · {{ value.parcels_confirmed ? '清单已确认' : '清单待确认' }}
          </p>
          <div class="parcel-table-wrap">
            <table class="parcel-table" aria-label="本次报单包裹">
              <thead>
                <tr>
                  <th>
                    <label class="parcel-check">
                      <input
                        type="checkbox"
                        aria-label="全部包裹参与本次报单"
                        :checked="!!value.parcels.length && parcelCount === value.parcels.length"
                        :indeterminate="parcelCount > 0 && parcelCount < value.parcels.length"
                        :disabled="!value.editable || pending || !value.parcels.length"
                        @change="setIncluded(null, ($event.target as HTMLInputElement).checked)"
                      />本次
                    </label>
                  </th><th>国内运单 / 物流公司</th><th>包裹件数</th><th>国内物流</th><th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="parcel in value.parcels" :key="parcel.id" :data-included="parcel.included">
                  <td>
                    <input
                      type="checkbox"
                      :aria-label="`运单 ${parcel.tracking_number} 参与本次报单`"
                      :checked="parcel.included"
                      :disabled="!value.editable || pending"
                      @change="setIncluded(parcel.id, ($event.target as HTMLInputElement).checked)"
                    />
                  </td><td>
                    <div class="parcel-waybill">
                      <strong>{{ parcel.tracking_number }}</strong><button class="order-link" :aria-label="`复制运单 ${parcel.tracking_number}`" @click="copyWaybill(parcel)">
                        <PhCopy :size="16" aria-hidden="true" />
                      </button>
                    </div><p class="order-muted parcel-description" :title="parcel.description">
                      {{ parcel.carrier || '物流公司未填写' }}<span v-if="parcel.description"> · {{ parcel.description }}</span>
                    </p>
                  </td><td>{{ parcel.quantity ?? '待完善' }}</td><td>
                    <span :title="parcelLogistics(parcel)?.checkedAt ? `最近查询：${at(parcelLogistics(parcel)!.checkedAt)}` : ''">
                      {{ parcelLogistics(parcel)?.status_label || '暂无物流信息' }}
                    </span><span v-if="parcelLogistics(parcel)?.warning" class="order-muted">
                      · 更新异常
                    </span>
                  </td><td>
                    <div class="parcel-row-actions">
                      <button
                        v-if="value.editable"
                        class="order-link"
                        :disabled="pending"
                        @click="editParcel(parcel.id)"
                      >
                        编辑
                      </button><button class="order-link" @click="viewTracking(parcel)">
                        轨迹
                      </button>
                    </div>
                  </td>
                </tr><tr v-if="!value.parcels.length">
                  <td colspan="5" class="empty-parcels">
                    尚未登记国内包裹。可直接录入运单，件数未知时先保存待完善。
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-if="copyNotice" class="order-muted mt-2" role="status">
            {{ copyNotice }}
          </p>
          <section class="fulfillment-destination">
            <div class="order-row">
              <h3>报单去向</h3><button class="order-link" @click="open('plan-details')">
                方案详情
              </button>
            </div>
            <dl class="destination-facts">
              <div>
                <dt>{{ value.receiving_binding_stale ? remoteActive ? '原合作仓（配置已变化）' : '原合作仓（待重绑）' : '合作仓' }}</dt><dd>
                  {{ value.warehouse_name || value.receiving_warehouse?.warehouse_name || '尚未配置' }}<span v-if="value.section_name">
                    · {{ value.section_name }}
                  </span>
                </dd>
              </div><div><dt>{{ value.handover_target?.shipment_type === 'WITHDRAW' ? '平台揽收点' : '平台交货点' }}</dt><dd>{{ value.handover_target?.name || '尚未提供' }}</dd></div><div><dt>平台配送方式</dt><dd>{{ value.delivery.method_name || value.delivery.carrier || '尚未提供' }}</dd></div>
            </dl>
          </section>
          <section class="warehouse-activity">
            <div class="order-row">
              <h3>合作仓动态</h3><span class="order-badge" :data-tone="tone">
                {{ value.crossborderbus_order_id ? fulfillmentStatusNames[value.fulfillment_status] : value.create_unknown ? '创建结果待确认' : '尚未创建' }}
              </span>
            </div>
            <p v-if="value.crossborderbus_order_id" class="mt-2">
              {{ value.fulfillment_status === 'CANCELLED' ? '已作废' : '已创建' }} · {{ value.crossborderbus_order_id }}
            </p>
            <div class="order-row warehouse-query">
              <p class="order-muted">
                最近成功同步：{{ at(value.last_synced_at) }}
              </p><div class="order-actions">
                <button
                  class="order-button"
                  :disabled="!queryAllowed"
                  :title="queryAllowed ? '' : '报单后可查询仓库状态'"
                  @click="syncLatest"
                >
                  {{ pending || refreshing ? '处理中…' : queryLabel }}
                </button><button v-if="value.attempt_history?.length" class="order-link" @click="open('history')">
                  历史记录
                </button>
              </div>
            </div>
          </section>
          <section v-if="order.order.platform === 'yandex' && value.crossborderbus_order_id" class="platform-association">
            <div class="order-row">
              <h4>Yandex 店铺订单关联</h4><button
                v-if="value.editable && !value.cancel_requested && !['linked', 'unknown'].includes(value.platform_link_state || '')"
                class="order-button"
                :disabled="pending || value.busy"
                @click="command('associate')"
              >
                关联平台订单
              </button>
            </div>
            <p class="order-muted mt-3">
              {{ value.platform_link_state === 'linked' ? '跨境巴士已返回关联成功，店铺和订单明细请在跨境巴士查看。' : value.platform_link_state === 'unknown' ? '关联结果待确认，请到跨境巴士核实，暂不能重复发送。' : value.platform_link_state === 'pending' ? '已创建报单，等待关联 Yandex 店铺订单。' : '关联后由跨境巴士读取店铺、物流、平台状态和订单时间。' }}
            </p><p v-if="value.platform_link_error" class="order-error mt-3" role="alert">
              {{ value.platform_link_error }}
            </p>
          </section>
        </section>
        <aside
          ref="readinessPanel"
          class="detail-card readiness-panel"
          tabindex="-1"
          aria-label="报单准备"
        >
          <div class="order-row readiness-heading">
            <h3>{{ remoteActive ? '履约资料' : '报单准备' }}</h3><span class="readiness-count" :data-ready="!missing.length">
              {{ remoteActive ? '已报单' : missing.length ? `${missing.length} 项待处理` : '资料已齐备' }}
            </span>
          </div>
          <section
            v-for="item in readiness"
            :key="item.id"
            class="readiness-item"
            :data-ready="item.ready"
            :data-item="item.id"
          >
            <div class="readiness-item-heading">
              <PhCheckCircle v-if="item.ready" :size="22" aria-hidden="true" /><PhFileText v-else-if="item.id === 'label' || item.id === 'parcels'" :size="22" aria-hidden="true" /><PhPackage v-else :size="22" aria-hidden="true" /><h4>{{ item.title }}</h4><span class="order-badge" :data-tone="item.ready ? 'green' : 'amber'">
                {{ item.ready ? '已就绪' : item.id === 'label' ? '待补充' : '待确认' }}
              </span>
            </div>
            <p :class="item.id === 'label' && value.label_error ? 'order-error' : 'order-muted'" :role="item.id === 'label' && value.label_error ? 'alert' : undefined">
              {{ item.note }}
            </p>
            <template v-if="item.id === 'parcels'">
              <button
                v-if="value.editable && !item.ready"
                class="order-button"
                :class="{ 'order-primary': !parcelError(value.parcels, true) }"
                :disabled="pending"
                @click="editParcel()"
              >
                {{ item.action }}
              </button>
            </template>
            <template v-else-if="item.id === 'services'">
              <button
                v-if="planAllowed"
                class="order-button"
                :disabled="pending || value.receiving_binding_stale"
                @click="open('plan')"
              >
                {{ item.ready ? '设置报单方案' : item.action }}
              </button><RouterLink v-else-if="value.plan_editable && order.order.platform !== 'yandex'" class="order-link" :to="settingsRoute">
                配置默认履约方案
              </RouterLink><p v-else class="order-muted">
                方案已锁定
              </p>
            </template>
            <OrderReceivingWarehouse
              v-else-if="item.id === 'receiving' && order.order.state === 'pending_shipment'"
              :order-id="order.order.id"
              line-key=""
              :purchase-state="String(value.revision)"
              @lock="purchaseLock('receiving', $event)"
              @updated="load(); emit('updated')"
            />
            <template v-else-if="item.id === 'label'">
              <a
                v-if="value.platform_label"
                :href="value.platform_label"
                target="_blank"
                rel="noopener noreferrer"
                class="order-link"
              >
                查看平台面单
              </a>
              <div class="label-actions">
                <button
                  v-if="value.editable && value.label_fetch_supported"
                  class="order-button"
                  :disabled="pending"
                  @click="command('fetch-label')"
                >
                  {{ pending ? '处理中…' : item.action }}
                </button><button
                  v-if="value.editable"
                  class="order-link"
                  :disabled="pending"
                  @click="open('label')"
                >
                  {{ value.platform_label ? '人工更新' : '人工补充' }}
                </button>
              </div>
              <p v-if="value.busy && value.operation === 'fetch-label'" class="order-muted mt-2" role="status">
                正在从平台下载面单并保存，请稍候…
              </p><p v-else-if="!value.platform_label && !value.label_fetch_supported" class="order-muted mt-2">
                {{ value.label_fetch_reason }}
              </p>
            </template>
          </section>
          <button class="order-link readiness-details" @click="open('plan-details')">
            方案备注与校验详情<PhCaretDown :size="16" aria-hidden="true" />
          </button>
        </aside>
      </div>
      <Teleport :to="footerTarget || 'body'" :disabled="!footerTarget" defer>
        <div class="fulfillment-task-footer">
          <details class="order-more">
            <summary class="order-button">
              更多操作<PhCaretDown :size="14" aria-hidden="true" />
            </summary><div class="order-menu">
              <button
                v-if="!['CANCELLED', 'COMPLETED'].includes(value.fulfillment_status) && !value.cancel_requested"
                class="order-link order-danger"
                :disabled="pending || value.busy"
                @click="open('cancel')"
              >
                取消履约
              </button><button class="order-link" :disabled="pending" @click="open('plan-details')">
                查看方案与校验
              </button><button v-if="value.attempt_history?.length" class="order-link" @click="open('history')">
                历史履约记录
              </button>
            </div>
          </details>
          <div class="fulfillment-submit-area">
            <template v-if="value.cancel_rejected && value.fulfillment_status !== 'SHIPPED' && value.fulfillment_status !== 'CANCELLED'">
              <button class="order-button order-primary" :disabled="pending || value.busy" @click="command('retry')">
                重新请求取消
              </button>
            </template>
            <template v-else-if="!value.crossborderbus_order_id && !value.create_unknown && !value.cancel_requested && value.fulfillment_status !== 'CANCELLED'">
              <span class="readiness-count">
                {{ value.busy && value.operation === 'create' ? '正在提交…' : value.busy && value.operation === 'fetch-label' ? '正在获取面单…' : missing.length ? `尚有 ${missing.length} 项待处理` : value.editing ? '正在编辑方案' : '资料已齐备' }}
              </span><button v-if="missing.length" class="order-link" @click="showReadiness">
                查看未完成项
              </button><button
                class="order-button order-primary"
                :disabled="!canSubmit"
                :title="value.blocked_reason || missing[0]?.note"
                @click="command(value.error_message ? 'retry' : 'submit')"
              >
                {{ value.busy && value.operation === 'create' ? '正在提交…' : value.error_message ? '重新提交' : '提交至合作仓' }}
              </button>
            </template>
            <button
              v-else-if="value.crossborderbus_order_id || value.create_unknown"
              class="order-button order-primary"
              :disabled="!queryAllowed"
              @click="syncLatest"
            >
              {{ pending || refreshing ? '处理中…' : queryLabel }}
            </button>
            <span v-else class="order-muted">
              {{ title }}
            </span>
          </div>
        </div>
      </Teleport>
    </template>

    <FulfillmentParcelDialog
      v-if="modal === 'parcels' && value"
      :value="value"
      :order="order"
      :focus-parcel-id="focusedParcelId"
      @close="close"
      @saved="parcelsSaved"
    />
    <WorkspaceDialog
      v-if="modal === 'label' && value"
      :open="true"
      title="更新国际面单"
      width="720px"
      class="order-ui"
      :close-disabled="pending"
      @close="close"
    >
      <form id="fulfillment-label" @submit.prevent="saveLabel">
        <fieldset :disabled="pending" class="order-form">
          <p v-if="error" class="order-error" role="alert">
            {{ error }}
          </p><p class="order-muted">
            请使用平台生成的面单，上传 PDF 或填写仓库可下载的 HTTPS 地址。
          </p><label>上传平台面单 PDF<input
            type="file"
            accept=".pdf,application/pdf"
            class="order-input"
            @change="uploadLabel"
          /><span class="order-muted">
            上限 8 MB，使用授权配置中的默认 S3 托管。
          </span></label><label>国际面单地址<input
            v-model.trim="labelUrl"
            class="order-input"
            type="url"
            placeholder="https://…/label.pdf"
            required
          /></label><label>面单号 / 国际跟踪号<input v-model.trim="trackingNumber" class="order-input" required /></label><label>目的国代码（选填）<input
            v-model="country"
            class="order-input"
            pattern="[A-Za-z]{2}"
            maxlength="2"
            :readonly="!!value.delivery.country"
          /></label>
        </fieldset>
      </form><template #footer>
        <div class="order-footer">
          <button class="order-button" :disabled="pending" @click="close">
            取消
          </button><button form="fulfillment-label" class="order-button order-primary" :disabled="pending">
            {{ pending ? '处理中…' : '保存面单' }}
          </button>
        </div>
      </template>
    </WorkspaceDialog>
    <FulfillmentPlanDialog
      v-if="modal === 'plan' && value"
      :value="value"
      :order="order.order"
      @close="close"
      @saved="planSaved"
    />
    <WorkspaceDialog
      v-if="modal === 'cancel'"
      :open="true"
      title="取消履约"
      width="600px"
      class="order-ui"
      :close-disabled="pending"
      @close="close"
    >
      <p v-if="error" class="order-error" role="alert">
        {{ error }}
      </p><p>取消后停止本单预报，并请求跨境巴士停止履约。仓库确认前仍显示“取消待确认”。</p><p class="order-muted mt-4">
        已入库包裹需要联系仓库处理；已发货订单可能无法取消。此操作不会修改平台订单状态。
      </p><template #footer>
        <div class="order-footer">
          <button class="order-button" :disabled="pending" @click="close">
            返回
          </button><button class="order-button order-danger" :disabled="pending" @click="cancel">
            确认取消履约
          </button>
        </div>
      </template>
    </WorkspaceDialog>
    <WorkspaceDialog
      v-if="modal === 'error' && value"
      :open="true"
      title="履约异常详情"
      width="640px"
      class="order-ui"
      @close="close"
    >
      <p class="order-error">
        {{ value.error_message }}
      </p><p>最近确认状态：{{ fulfillmentStatusNames[value.fulfillment_status] }}</p><p class="order-muted mt-4">
        最近成功同步：{{ at(value.last_synced_at) }}
      </p><p class="order-muted mt-3">
        本次尝试：{{ at(value.last_attempt_at) }}
      </p><p class="order-muted mt-3">
        {{ value.create_unknown ? '创建结果未确认期间禁止重复预报。' : '状态同步失败时保留最近确认的仓库进度。' }}
      </p>
    </WorkspaceDialog>
    <WorkspaceDialog
      v-if="modal === 'plan-details' && value"
      :open="true"
      title="报单方案与校验"
      width="640px"
      class="order-ui"
      @close="close"
    >
      <dl class="destination-facts">
        <div><dt>合作仓</dt><dd>{{ value.warehouse_name || '尚未配置' }} · {{ value.section_name }}</dd></div><div><dt>已选服务</dt><dd>{{ value.selected_services?.map(service => service.name).join('、') || '尚未确认' }}</dd></div><div>
          <dt>报单备注</dt><dd class="preserve-lines">
            {{ value.remark || '未填写' }}
          </dd>
        </div><div>
          <dt>平台交货资料</dt><dd>
            {{ value.handover_target?.name }}<p class="preserve-lines">
              {{ value.handover_target?.address || value.handover_target?.reason || '尚未提供' }}
            </p>
          </dd>
        </div><div><dt>最近提交校验</dt><dd>{{ value.blocked_reason || '资料校验通过，提交时将再次核对。' }}</dd></div>
      </dl><p v-if="value.editing" class="order-muted mt-3">
        修改本单方案期间已暂停自动预报。
      </p><p v-if="value.awaiting_resubmission" class="order-muted mt-3">
        {{ notice }}
      </p><p v-if="value.label_error && value.platform_label" class="order-muted mt-3">
        当前保留上次保存的面单。
      </p><template #footer>
        <button
          v-if="planAllowed"
          class="order-button order-primary"
          :disabled="pending || value.receiving_binding_stale"
          @click="open('plan')"
        >
          设置报单方案
        </button>
      </template>
    </WorkspaceDialog>
    <WorkspaceDialog
      v-if="modal === 'tracking' && trackingParcel"
      :open="true"
      title="国内包裹轨迹"
      width="640px"
      class="order-ui"
      @close="close"
    >
      <h3>{{ trackingParcel.tracking_number }}</h3><p class="order-muted mt-2">
        {{ tracking?.company || trackingParcel.carrier || '物流公司未提供' }} · {{ tracking?.status_label || '暂无物流状态' }}
      </p><p v-if="tracking" class="order-muted mt-2">
        最近查询：{{ at(tracking.checkedAt) }}
      </p><p v-if="tracking?.warning" class="order-error" role="alert">
        {{ tracking.warning }}；保留最近获取的轨迹。
      </p><ol v-if="tracking?.steps.length" class="tracking-timeline">
        <li v-for="(step, index) in tracking.steps" :key="index">
          <time class="order-muted">
            {{ step.time }}
          </time><p>{{ step.description }}</p>
        </li>
      </ol><p v-else class="order-muted mt-4">
        暂无可展示的轨迹，可在商品与采购中刷新对应采购单的进度。
      </p>
    </WorkspaceDialog>
    <WorkspaceDialog
      v-if="modal === 'history' && value"
      :open="true"
      title="历史履约记录"
      width="640px"
      class="order-ui"
      @close="close"
    >
      <article v-for="(attempt, index) in value.attempt_history" :key="index" class="order-section">
        <h3>{{ attempt.crossborderbus_order_id ? `已作废报单 · ${attempt.crossborderbus_order_id}` : '报单前取消' }}</h3><p class="order-muted mt-2">
          {{ at(attempt.archived_at) }}
        </p><p class="mt-2">
          原收货仓：{{ attempt.receiving_warehouse?.warehouse_name || '未记录' }}
        </p><p class="mt-2">
          原服务：{{ attempt.selected_services.map(service => service.name).join('、') || '未记录' }}
        </p>
      </article>
    </WorkspaceDialog>
  </div>
</template>
<style scoped>
.fulfillment-panel { min-width: 0; }
.fulfillment-context { margin-bottom: 20px; }
h3 { font-size: 16px; font-weight: 600; }
h3 .order-muted { margin-left: 10px; font-weight: 400; }
h4 { font-weight: 600; }
.fulfillment-notice { padding: 14px 16px; margin-bottom: 16px; border: 1px solid var(--order-border); border-radius: 6px; background: var(--order-soft); }
.fulfillment-notice p { margin-top: 6px; }
.fulfillment-layout { display: grid; grid-template-columns: minmax(0, 1fr) 296px; gap: 16px; align-items: start; }
.fulfillment-main { min-width: 0; }
.parcel-selection { margin: 6px 0 10px; color: var(--order-warning); font-size: 14px; }
.parcel-selection[data-confirmed='true'] { color: var(--order-positive); }
.parcel-table-wrap { overflow-x: auto; border: 1px solid var(--order-border); border-radius: 6px; }
.parcel-table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { background: var(--order-soft); font-weight: 500; color: var(--order-muted); text-align: left; white-space: nowrap; }
th, td { padding: 6px 10px; border-bottom: 1px solid var(--order-border); vertical-align: middle; }
tr:last-child td { border-bottom: 0; }
th:first-child { width: 64px; }
.parcel-table input[type='checkbox'] { width: 18px; height: 18px; accent-color: rgb(var(--color-primary-600)); vertical-align: middle; }
.parcel-check { display: flex; align-items: center; gap: 6px; }
.parcel-waybill { display: flex; align-items: center; gap: 4px; }
.parcel-waybill strong { font-weight: 500; overflow-wrap: anywhere; }
.parcel-waybill .order-link { padding: 2px; flex-shrink: 0; }
.parcel-description { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
tr[data-included='false'] td { color: var(--order-muted); background: var(--order-soft); }
.parcel-row-actions { display: flex; gap: 10px; }
.parcel-row-actions .order-link { padding: 4px 0; }
.empty-parcels { padding: 24px 16px; color: var(--order-muted); text-align: center; }
.fulfillment-destination, .warehouse-activity, .platform-association { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--order-border); }
.destination-facts { display: grid; gap: 1px; margin-top: 8px; border: 1px solid var(--order-border); border-radius: 6px; overflow: hidden; }
.destination-facts > div { display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 12px; padding: 4px 12px; background: var(--order-soft); font-size: 14px; }
.destination-facts dt { color: var(--order-muted); }
.destination-facts dd { overflow-wrap: anywhere; }
.warehouse-query { margin-top: 8px; flex-wrap: wrap; }
.readiness-panel:focus { outline: 2px solid var(--order-positive); outline-offset: 2px; }
.readiness-heading { padding-bottom: 12px; }
.readiness-count { font-size: 14px; color: var(--order-warning); }
.readiness-count[data-ready='true'] { color: var(--order-positive); }
.readiness-item { padding: 12px 0; border-top: 1px solid var(--order-border); }
.readiness-item-heading { display: flex; align-items: center; gap: 8px; }
.readiness-item-heading > svg { color: var(--order-warning); flex-shrink: 0; }
.readiness-item[data-ready='true'] .readiness-item-heading > svg { color: var(--order-positive); }
.readiness-item .order-badge { margin-left: auto; }
.readiness-item > p { margin-top: 8px; overflow-wrap: anywhere; }
.readiness-item > .order-button, .label-actions { margin-top: 8px; }
.readiness-item .order-button { padding-left: 12px; padding-right: 12px; }
.label-actions { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.readiness-details { width: 100%; display: flex; align-items: center; justify-content: space-between; border-top: 1px solid var(--order-border); padding: 16px 0 0; }
.fulfillment-task-footer { display: flex; justify-content: space-between; align-items: center; gap: 16px; min-height: 44px; }
.fulfillment-panel > .fulfillment-task-footer { margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--order-border); }
.fulfillment-task-footer .order-menu { top: auto; bottom: calc(100% + 6px); right: auto; left: 0; }
.fulfillment-submit-area { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 10px; }
.preserve-lines { white-space: pre-wrap; overflow-wrap: anywhere; }
.tracking-timeline { display: grid; gap: 16px; margin-top: 20px; border-left: 2px solid var(--order-border); padding-left: 16px; }
@media (max-width: 1023px) {
  .fulfillment-layout { grid-template-columns: minmax(0, 1fr); }
  .readiness-panel { grid-row: 1; }
  .readiness-item { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px 16px; }
  .readiness-item > p { grid-column: 1; margin: 0; }
  .readiness-item > .order-button, .readiness-item > .label-actions { grid-column: 2; grid-row: 1 / 3; margin: 0; align-self: center; }
  .readiness-item > section { grid-column: 1 / -1; }
}
@media (max-width: 600px) {
  .fulfillment-task-footer { align-items: flex-start; flex-wrap: wrap; }
  .fulfillment-submit-area { margin-left: auto; }
  .readiness-item { display: block; }
  .readiness-item > p { margin-top: 8px; }
  .readiness-item > .order-button, .readiness-item > .label-actions { margin-top: 12px; }
  .parcel-table { min-width: 620px; }
  .destination-facts > div { grid-template-columns: 96px minmax(0, 1fr); }
}
</style>
