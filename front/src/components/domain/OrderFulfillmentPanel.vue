<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useAppStore } from '@/stores/app'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import OrderPurchaseTracking from './OrderPurchaseTracking.vue'
import FulfillmentParcelDialog from './FulfillmentParcelDialog.vue'
import FulfillmentPlanFields from './FulfillmentPlanFields.vue'
import { fetchBusSettings, fetchFulfillment, fulfillmentCommand, uploadFulfillmentLabel } from '@/api/fulfillment'
import type { FulfillmentAction } from '@/api/fulfillment'
import type { BusSettings, FulfillmentDetail, FulfillmentPlan } from '@/types/fulfillment'
import { fulfillmentStatusNames } from '@/types/fulfillment'
import type { OrderDetail } from '@/types/orders'
import { orderProcessingProgress, procurementProgress } from './orderPresentation'
const props = defineProps<{ order: OrderDetail }>()
const emit = defineEmits<{ updated: []; lock: [boolean]; purchaseUpdated: [OrderDetail] }>()
const purchaseRecords = computed(() => props.order.lines.flatMap(line => line.records).filter(record => record.status === 'purchased'))
const needsAssignment = computed(() => purchaseRecords.value.some(record => ['pending_assignment', 'conflict'].includes(record.progress?.state || '')))
function purchaseUpdated(detail: OrderDetail) { emit('purchaseUpdated', detail); void load() }
const value = ref<FulfillmentDetail | null>(null)
const app = useAppStore()
watch(() => value.value?.label_error, (message, previous) => {
  if (message && message !== previous) app.pushToast(`${message} 请点击“重新获取面单”重试。`, 'error')
})
const error = ref('')
const pending = ref(false)
const refreshing = ref(false)
const modal = ref<'' | 'label' | 'parcels' | 'plan' | 'cancel' | 'error'>('')
const purchaseLocks = reactive(new Set<string>())
function purchaseLock(id: string, busy: boolean) {
  if (busy) purchaseLocks.add(id)
  else purchaseLocks.delete(id)
}
watch(() => pending.value || !!modal.value || purchaseLocks.size > 0, busy => emit('lock', busy), { flush: 'sync' })
const labelUrl = ref('')
const trackingNumber = ref('')
const country = ref('')
const settings = ref<BusSettings | null>(null)
const plan = ref<FulfillmentPlan>({ section_id: 0, warehouse_id: 0, service_ids: [] })
const servicesReady = ref(false)
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
const purchased = computed(() => props.order.lines.reduce((sum, line) => sum + line.purchased_quantity, 0))
const ordered = computed(() => props.order.lines.reduce((sum, line) => sum + line.line.quantity, 0))
const title = computed(() => value.value?.busy && value.value.operation === 'create' ? '正在提交预报' : value.value?.busy && value.value.operation === 'fetch-label' ? '正在获取平台面单' : value.value?.create_unknown ? '创建结果待核实，暂停重复提交' : value.value?.cancel_requested && value.value.fulfillment_status === 'SHIPPED' ? '仓库已发货，取消未成功' : value.value?.cancel_rejected && value.value.fulfillment_status !== 'CANCELLED' ? '仓库拒绝取消，请处理后重试' : value.value?.cancel_requested && value.value.fulfillment_status !== 'CANCELLED' ? '已请求取消，等待仓库确认' : value.value?.error_message ? '履约操作需要处理' : status.value)
const notice = computed(() => value.value?.error_message || (value.value?.crossborderbus_order_id ? value.value.fulfillment_status === 'SHIPPED' ? '仓库已发货，ERP 履约状态已同步为已发货。' : '仓库进度随同步订单更新，也可点击同步按钮单独刷新。' : value.value?.blocked_reason || (value.value?.editing ? '修改本单方案期间已暂停自动预报。' : progress.value.note)))
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
  if (which === 'plan') {
    if (!await command('pause')) return
    try {
      settings.value = await fetchBusSettings()
      plan.value = { ...value.value!.plan!, service_ids: [...(value.value!.plan?.service_ids || [])] }
      keepEditing = setInterval(() => { if (modal.value === 'plan' && !pending.value) void command('pause') }, 60_000)
    } catch (exc) { error.value = exc instanceof Error ? exc.message : '合作仓库读取失败'; await command('resume'); return }
  }
  if (which === 'label') { labelUrl.value = value.value.platform_label; trackingNumber.value = value.value.platform_tracking_number; country.value = value.value.country }
  modal.value = which
}
async function close() {
  if (pending.value) return
  clearInterval(keepEditing)
  if (modal.value === 'plan' && !await command('resume')) return
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
async function savePlan() { if (await command('plan', { plan: plan.value })) { clearInterval(keepEditing); modal.value = '' } }
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
  if (!pending.value && !refreshing.value && !modal.value) await load(true)
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
    <p v-if="error" class="order-error" role="alert">{{ error }} <button class="order-link" :disabled="pending" @click="load()">刷新</button></p>
    <p v-if="!value" class="order-muted py-8">正在读取履约资料…</p>
    <template v-if="value">
      <div class="order-row mb-5"><p class="order-muted">ERP 处理进度</p><span class="order-badge" :data-tone="tone">{{ status }}</span></div>
      <section class="fulfillment-notice" :data-tone="tone" aria-live="polite"><div class="order-row"><h3>{{ title }}</h3><button v-if="value.error_message" class="order-button" @click="open('error')">查看详情</button></div><p>{{ notice }}</p><RouterLink v-if="!value.rule && !value.crossborderbus_order_id" class="order-link" :to="settingsRoute">配置默认履约方案</RouterLink></section>
      <section class="fulfillment-facts" aria-label="已记录资料与仓库回执">
        <div><span>采购登记</span><strong>{{ purchased }} / {{ ordered }} 件</strong></div>
        <div><span>国内快递单号</span><strong>{{ value.parcels.length ? `已录入 ${value.parcels.length} 条记录` : '未录入' }}</strong></div>
        <div><span>国际面单</span><strong>{{ value.platform_label ? '已保存面单' : '待补充' }}</strong></div>
        <div><span>跨境预报</span><strong>{{ value.crossborderbus_order_id ? `已创建 · ${value.crossborderbus_order_id}` : value.create_unknown ? '创建结果待确认' : '尚未创建' }}</strong></div>
      </section>
      <section class="plan-card">
        <div class="order-row"><h3>平台配送：{{ value.delivery.method_name || value.delivery.carrier || '尚未提供' }}</h3><button v-if="value.plan_editable && value.rule && value.plan" class="order-button" :disabled="pending" @click="open('plan')">修改</button><span v-else class="order-muted">{{ value.plan_editable ? '待配置' : '已锁定' }}</span></div>
        <p>合作仓库：{{ value.warehouse_name || '尚未配置' }}<span v-if="value.section_name"> · {{ value.section_name }}</span></p>
        <p class="order-muted">{{ value.plan ? value.override ? '本单履约方案' : '默认履约方案' : '尚未配置履约方案' }} · 目的国：{{ value.country || '待填写' }}<template v-if="value.plan"> · {{ value.plan.service_ids.length }} 项增值服务</template></p>
      </section>
      <h3 class="mt-6 mb-4">履约资料</h3>
      <section class="order-section">
        <div class="order-row"><h4>国际面单</h4><div class="flex flex-wrap gap-2"><button v-if="value.editable && value.label_fetch_supported" class="order-button order-primary" :disabled="pending" @click="command('fetch-label')">{{ pending ? '处理中…' : value.platform_label || value.label_error ? '重新获取面单' : '获取平台面单' }}</button><button v-if="value.editable" class="order-button" :disabled="pending" @click="open('label')">{{ value.platform_label ? '人工更新' : '人工补充' }}</button></div></div>
        <p class="mt-3"><a v-if="value.platform_label" :href="value.platform_label" target="_blank" rel="noopener noreferrer" class="order-link">查看平台面单</a><span v-else class="order-muted">尚未获取平台面单</span><span v-if="value.platform_tracking_number" class="order-muted"> · {{ value.platform_tracking_number }}</span></p>
        <p v-if="value.label_error" class="order-error mt-3" role="alert">{{ value.label_error }}<span v-if="value.platform_label"> 当前保留上次保存的面单。</span></p>
        <p v-else-if="value.busy && value.operation === 'fetch-label'" class="order-muted mt-3" role="status">正在从平台下载面单并保存，请稍候…</p>
        <p v-else-if="!value.platform_label" class="order-muted mt-3">{{ value.label_fetch_supported ? '默认自动预报开启时，方案和国内包裹齐备后会自动获取面单；也可点击上方按钮立即获取。' : value.label_fetch_reason }}</p>
      </section>
      <section class="order-section">
        <div class="order-row"><h4>国内包裹</h4><button v-if="value.editable" class="order-button" :disabled="pending" @click="open('parcels')">{{ needsAssignment ? '确认包裹分配' : value.parcels.length ? '管理包裹' : '录入包裹' }}</button></div>
        <p v-if="!value.parcels.length" class="order-muted mt-3">尚未录入国内快递单号。供应商提供单号后，关联采购记录并填写数量。</p>
        <OrderPurchaseTracking v-for="record in purchaseRecords" :key="record.id" :order-id="order.order.id" :record="record" @updated="purchaseUpdated" @lock="purchaseLock(record.id, $event)" />
        <p v-for="parcel in value.parcels" :key="parcel.id" class="mt-3">{{ parcel.carrier }} {{ parcel.tracking_number }} · {{ parcel.quantity }} 件</p>
      </section>
      <div class="order-row mt-6"><p class="order-muted">最近成功同步仓库状态：{{ at(value.last_synced_at) }}</p><button class="order-button" :disabled="pending || refreshing" @click="syncLatest">{{ pending ? '处理中…' : value.create_unknown ? '核实创建结果' : value.cancel_requested && value.fulfillment_status !== 'CANCELLED' ? '核实取消结果' : '同步' }}</button></div>
      <div class="order-row mt-8 fulfillment-actions">
        <button v-if="!['CANCELLED', 'COMPLETED'].includes(value.fulfillment_status) && !value.cancel_requested" class="order-button order-danger" :disabled="pending || value.busy" @click="open('cancel')">取消履约</button>
        <button v-if="value.cancel_rejected && value.fulfillment_status !== 'SHIPPED' && value.fulfillment_status !== 'CANCELLED'" class="order-button order-primary" :disabled="pending || value.busy" @click="command('retry')">重新请求取消</button>
        <button v-else-if="!value.crossborderbus_order_id && !value.create_unknown && !value.cancel_requested && value.fulfillment_status !== 'CANCELLED'" class="order-button order-primary" :disabled="pending || value.busy || !!value.blocked_reason || value.editing" @click="command(value.error_message ? 'retry' : 'submit')">{{ value.busy ? '正在提交…' : value.error_message ? '重新提交' : '立即提交' }}</button>
      </div>
    </template>
    <FulfillmentParcelDialog v-if="modal === 'parcels' && value" :value="value" :order="order" @close="close" @saved="parcelsSaved" />
    <WorkspaceDialog v-if="modal === 'label' && value" :open="true" title="更新国际面单" width="720px" class="order-ui" :close-disabled="pending" @close="close"><form id="fulfillment-label" @submit.prevent="saveLabel"><fieldset :disabled="pending" class="order-form"><p v-if="error" class="order-error" role="alert">{{ error }}</p><p class="order-muted">请使用平台生成的面单，上传 PDF 或填写仓库可下载的 HTTPS 地址。</p><label>上传平台面单 PDF<input type="file" accept=".pdf,application/pdf" class="order-input" @change="uploadLabel" /><span class="order-muted">上限 8 MB，使用授权配置中的默认 S3 托管。</span></label><label>国际面单地址<input v-model.trim="labelUrl" class="order-input" type="url" placeholder="https://…/label.pdf" required /></label><label>面单号 / 国际跟踪号<input v-model.trim="trackingNumber" class="order-input" required /></label><label>目的国代码<input v-model="country" class="order-input" pattern="[A-Za-z]{2}" maxlength="2" required :readonly="!!value.delivery.country" /></label></fieldset></form><template #footer><div class="order-footer"><button class="order-button" :disabled="pending" @click="close">取消</button><button form="fulfillment-label" class="order-button order-primary" :disabled="pending">{{ pending ? '处理中…' : '保存面单' }}</button></div></template></WorkspaceDialog>
    <WorkspaceDialog v-if="modal === 'plan' && value && settings" :open="true" title="修改本单履约方案" width="720px" class="order-ui" :close-disabled="pending" @close="close"><form id="fulfillment-plan" class="order-form" @submit.prevent="savePlan"><p v-if="error" class="order-error" role="alert">{{ error }}</p><p class="edit-notice">编辑期间已暂停本单自动预报。</p><div class="plan-card"><h3>订单配送方式（来自平台）</h3><p>{{ value.delivery.method_name || value.delivery.carrier }}</p><p class="order-muted">仅选择已确认可承接此配送方式的合作仓库和服务。</p></div><FulfillmentPlanFields v-model="plan" :disabled="pending" :sections="settings.catalog.sections || []" section-locked :compatible-warehouse-ids="value.rule?.compatible_warehouse_ids" @ready="servicesReady = $event" /><p class="order-muted">保存后恢复自动预报；全局默认方案保持不变。</p></form><template #footer><div class="order-footer"><button class="order-button" :disabled="pending" @click="close">取消</button><button form="fulfillment-plan" class="order-button order-primary" :disabled="pending || !servicesReady">保存本单方案</button></div></template></WorkspaceDialog>
    <WorkspaceDialog v-if="modal === 'cancel'" :open="true" title="取消履约" width="600px" class="order-ui" :close-disabled="pending" @close="close"><p v-if="error" class="order-error" role="alert">{{ error }}</p><p>取消后停止本单预报，并请求跨境巴士停止履约。仓库确认前仍显示“取消待确认”。</p><p class="order-muted mt-4">已入库包裹需要联系仓库处理；已发货订单可能无法取消。此操作不会修改平台订单状态。</p><template #footer><div class="order-footer"><button class="order-button" :disabled="pending" @click="close">返回</button><button class="order-button order-danger" :disabled="pending" @click="cancel">确认取消履约</button></div></template></WorkspaceDialog>
    <WorkspaceDialog v-if="modal === 'error' && value" :open="true" title="履约异常详情" width="640px" class="order-ui" @close="close"><p class="order-error">{{ value.error_message }}</p><p>最近确认状态：{{ fulfillmentStatusNames[value.fulfillment_status] }}</p><p class="order-muted mt-4">最近成功同步：{{ at(value.last_synced_at) }}</p><p class="order-muted mt-3">本次尝试：{{ at(value.last_attempt_at) }}</p><p class="order-muted mt-3">{{ value.create_unknown ? '创建结果未确认期间禁止重复预报。' : '状态同步失败时保留最近确认的仓库进度。' }}</p></WorkspaceDialog>
  </div>
</template>
<style scoped>
h3 { font-size: 14px; font-weight: 600; }
h4 { font-size: 14px; }
.fulfillment-notice, .edit-notice { padding: 14px; border-radius: 6px; background: #fffbeb; color: #a16207; }
.fulfillment-notice[data-tone='red'] { background: #fef2f2; color: #b91c1c; }
.fulfillment-notice[data-tone='blue'], .fulfillment-notice[data-tone='neutral'] { background: var(--order-soft); color: var(--order-text); }
.fulfillment-notice[data-tone='green'] { background: #f0fdf4; color: #15803d; }
.fulfillment-notice p { margin-top: 9px; }
.fulfillment-facts { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 20px; }
.fulfillment-facts > div { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.fulfillment-facts span { color: var(--order-muted); font-size: 12px; }
.fulfillment-facts strong { font-size: 14px; font-weight: 500; overflow-wrap: anywhere; }
@media (max-width: 480px) { .fulfillment-facts { grid-template-columns: 1fr; } }
.plan-card { padding: 16px; border-radius: 6px; background: var(--order-soft); }
.plan-card:not(.mt-6) { margin-top: 20px; }
.plan-card p + p, .plan-card h3 + p { margin-top: 12px; }
.fulfillment-actions > :last-child { margin-left: auto; }
</style>
