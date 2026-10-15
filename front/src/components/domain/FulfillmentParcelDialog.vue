<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { fetchFulfillment, fulfillmentCommand } from '@/api/fulfillment'
import type { FulfillmentDetail, DomesticParcel } from '@/types/fulfillment'
import type { OrderDetail } from '@/types/orders'
import { domesticCarriers, domesticWaybillKey, groupDomesticParcels, parcelQuantityError, purchaseLogisticsChoices, seedDomesticParcels, unassignedParcelQuantity } from './purchaseLogistics'
import type { DomesticParcelGroup } from './purchaseLogistics'
const props = defineProps<{ value: FulfillmentDetail; order: OrderDetail }>()
const purchaseLogistics = computed(() => Object.fromEntries(props.order.lines.flatMap(line => line.records)
  .filter(record => record.progress?.data).map(record => [record.id, record.progress!.data!])))
const emit = defineEmits<{ close: []; saved: [FulfillmentDetail] }>()
const parcels = ref<DomesticParcel[]>(seedDomesticParcels(props.order, props.value.parcels, purchaseLogistics.value))
const groups = computed(() => groupDomesticParcels(parcels.value))
const busy = ref(false)
const error = ref('')
const revision = ref(props.value.revision)
const locked = ref(!props.value.editable)
const purchases = computed(() => props.order.lines.flatMap(line => line.records.filter(r => r.status === 'purchased').map(record => ({ lineKey: line.selection.line_key, record, line: line.line }))))
const lines = computed(() => {
  const counts = new Map<string, number>()
  for (const { selection } of props.order.lines)
    counts.set(selection.line_key, (counts.get(selection.line_key) || 0) + 1)
  return props.order.lines.filter(line => line.selection.line_key && counts.get(line.selection.line_key) === 1)
})
const hasQueriedLogistics = computed(() => purchases.value.some(source => purchaseLogisticsChoices(source.record, purchaseLogistics.value).length > 0))
const canAddItem = computed(() => lines.value.some(line => available(line.selection.line_key) > 0))
const reship = ref<{ sourceId: string; quantity: number; tracking: string; carrier: string } | null>(null)
const reshipProductInput = ref<HTMLSelectElement | null>(null)
const reshipSources = computed(() => parcels.value.filter(parcel => domesticWaybillKey(parcel.tracking_number)
  && Number.isInteger(parcel.quantity) && parcel.quantity > 0))
const reshipSource = computed(() => parcels.value.find(parcel => parcel.id === reship.value?.sourceId))
function productLabel(lineKey: string) {
  const line = lines.value.find(row => row.selection.line_key === lineKey)
  return line ? `${line.line.sku} · ${line.selection.source?.specification || line.line.title}` : lineKey
}
async function startReship(sourceId = '') {
  if (busy.value || locked.value) return
  error.value = ''
  reship.value = { sourceId, quantity: 1, tracking: '', carrier: '' }
  await nextTick()
  reshipProductInput.value?.focus()
}
function applyReship() {
  if (busy.value || locked.value || !reship.value) return
  const source = reshipSource.value
  const { quantity, carrier } = reship.value
  const tracking = domesticWaybillKey(reship.value.tracking)
  if (!source) { error.value = '请选择漏发商品及其原包裹。'; return }
  if (!Number.isInteger(quantity) || quantity < 1 || quantity > source.quantity) {
    error.value = `补发数量须为 1 至 ${source.quantity} 之间的整数。`; return
  }
  if (!tracking || tracking.length > 100) { error.value = '请填写补发快递单号（最多 100 个字符）。'; return }
  if (tracking === domesticWaybillKey(source.tracking_number)) {
    error.value = '补发快递单号不能与原包裹相同。'; return
  }
  // 补寄仍履行原销售及采购数量，只移动商品分配，不新增采购或重复计数。
  const moved = { ...source, id: crypto.randomUUID(), quantity, carrier, tracking_number: tracking }
  parcels.value = parcels.value.flatMap(parcel => parcel.id !== source.id ? [parcel]
    : parcel.quantity === quantity ? [] : [{ ...parcel, quantity: parcel.quantity - quantity }])
  parcels.value.push(moved)
  reship.value = null
  error.value = ''
}
function available(lineKey: string, excludingId = '') {
  return unassignedParcelQuantity(props.order, parcels.value, lineKey, excludingId)
}
function purchaseAvailable(recordId: string, excludingId = '') {
  const purchase = purchases.value.find(source => source.record.id === recordId)
  if (!purchase) return 0
  const assigned = parcels.value.filter(parcel => parcel.purchase_record_id === recordId && parcel.id !== excludingId)
    .reduce((sum, parcel) => sum + (Number(parcel.quantity) || 0), 0)
  return Math.min(available(purchase.lineKey, excludingId), Math.max(0, purchase.record.quantity - assigned))
}
function maximum(parcel: DomesticParcel) {
  return parcel.purchase_record_id ? purchaseAvailable(parcel.purchase_record_id, parcel.id) : available(parcel.line_key, parcel.id)
}
function add(group?: DomesticParcelGroup) {
  const line = lines.value.find(row => available(row.selection.line_key) > 0)
  if (!line) {
    startReship()
    return
  }
  const first = purchases.value.find(source => source.lineKey === line.selection.line_key && purchaseAvailable(source.record.id) > 0)
  const parcel = {
    id: crypto.randomUUID(), line_key: first?.lineKey || line.selection.line_key,
    purchase_record_id: first?.record.id || '', carrier: group?.carrier || '', tracking_number: group?.tracking_number || '', quantity: 1,
  }
  parcels.value.push(parcel)
}
function updateGroup(group: DomesticParcelGroup, field: 'carrier' | 'tracking_number', event: Event) {
  const value = (event.target as HTMLInputElement | HTMLSelectElement).value
  for (const item of group.items) item[field] = field === 'tracking_number' ? domesticWaybillKey(value) : value
}
function removeGroup(group: DomesticParcelGroup) {
  const ids = new Set(group.items.map(item => item.id))
  parcels.value = parcels.value.filter(item => !ids.has(item.id))
}
function removeItem(id: string) {
  parcels.value = parcels.value.filter(item => item.id !== id)
}
function changeProduct(parcel: DomesticParcel, event: Event) {
  const lineKey = (event.target as HTMLSelectElement).value
  if (lineKey === parcel.line_key) return
  parcel.line_key = lineKey
  parcel.purchase_record_id = purchases.value.find(source => source.lineKey === lineKey
    && purchaseAvailable(source.record.id, parcel.id) > 0)?.record.id || ''
}
function choices(parcel: DomesticParcel) {
  const record = purchases.value.find(p => p.record.id === parcel.purchase_record_id)?.record
  return record ? purchaseLogisticsChoices(record, purchaseLogistics.value) : []
}
function useTracking(parcel: DomesticParcel, event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (!value) return
  const choice = choices(parcel)[Number(value)]
  if (choice) { parcel.carrier = choice.carrier; parcel.tracking_number = choice.tracking_number }
}
function selectedTracking(parcel: DomesticParcel) {
  const index = choices(parcel).findIndex(choice => domesticWaybillKey(choice.tracking_number) === domesticWaybillKey(parcel.tracking_number) && choice.carrier === parcel.carrier)
  return index < 0 ? '' : String(index)
}
async function save() {
  if (busy.value || locked.value) return
  if (reship.value) { error.value = '请先确认或取消当前补发包裹。'; return }
  error.value = parcelQuantityError(props.order, parcels.value)
  if (error.value) return
  if (groups.value.some(group => group.carrierConflict)) {
    error.value = '同一运单的快递公司不一致，请核对后选择，或清空为未填写。'
    return
  }
  busy.value = true; error.value = ''
  const normalized = groups.value.flatMap(group => group.items.map(item => ({
    ...item, tracking_number: group.tracking_number, carrier: group.carrier,
  })))
  try { emit('saved', await fulfillmentCommand('parcels', props.value.erp_order_id, revision.value, { parcels: normalized })) }
  catch (exc) {
    const message = exc instanceof Error ? exc.message : '包裹保存失败'
    error.value = message
    // 业务数量校验或网络失败都不是版本冲突，不能换一个版本盲目重提。
    if (!message.startsWith('履约资料已更新')) return
    try {
      const latest = await fetchFulfillment(props.value.erp_order_id)
      revision.value = latest.revision
      locked.value = !latest.editable
      const reason = message.replace(/[。.!！\s]+$/u, '')
      error.value = latest.editable ? `${reason}。已刷新订单版本，请核对包裹后再次保存。` : `${reason}。资料已锁定，请关闭并刷新履约详情。`
    } catch { error.value = message }
  }
  finally { busy.value = false }
}
if (!parcels.value.length) add()
</script>
<template>
  <WorkspaceDialog :open="true" :title="value.crossborderbus_order_id ? '管理国内包裹' : '录入国内包裹'" width="760px" class="order-ui" :close-disabled="busy" @close="emit('close')">
    <form id="fulfillment-parcels" class="order-form" @submit.prevent="save">
      <p class="order-muted">订单 {{ order.order.order_id }} · 按实际收到的商品录入包裹，无需采购单号。漏发后补寄可录入补发包裹。</p>
      <p v-if="hasQueriedLogistics" class="order-muted">已载入查询到的 1688 运单，同一运单合并为一个包裹。请核对包裹内的商品与数量。</p>
      <p v-if="error" class="order-error" role="alert">{{ error }}</p>
      <p v-if="!lines.length" class="order-error">订单缺少可识别的商品，请同步订单后核对。</p>
      <fieldset v-if="reship" class="parcel-card" :disabled="busy || locked" aria-label="录入补发包裹">
        <legend>录入补发包裹</legend>
        <label>漏发商品及原包裹
          <select ref="reshipProductInput" v-model="reship.sourceId" class="order-input" aria-label="漏发商品及原包裹">
            <option value="">请选择漏发商品</option>
            <option v-for="item in reshipSources" :key="item.id" :value="item.id">{{ productLabel(item.line_key) }} · 原单号 {{ domesticWaybillKey(item.tracking_number) }} · {{ item.quantity }} 件</option>
          </select>
        </label>
        <div class="order-form-columns mt-4">
          <label>补发快递（选填）<select v-model="reship.carrier" class="order-input" aria-label="补发快递"><option value="">未填写</option><option v-for="carrier in domesticCarriers" :key="carrier">{{ carrier }}</option></select></label>
          <label>补发快递单号<input v-model="reship.tracking" class="order-input" aria-label="补发快递单号" maxlength="100" placeholder="填写商家提供的补发单号" /></label>
          <label>补发数量<input v-model.number="reship.quantity" class="order-input" aria-label="补发数量" type="number" min="1" :max="reshipSource?.quantity" step="1" /></label>
        </div>
        <p v-if="reshipSource" class="order-muted mt-3">将从原包裹 {{ domesticWaybillKey(reshipSource.tracking_number) }} 的 {{ reshipSource.quantity }} 件中移出补发数量，原包裹中的其他商品保留。若移走全部商品，该空包裹将移除。</p>
        <div class="flex gap-2 mt-4">
          <button type="button" class="order-button order-primary" @click="applyReship">确认补发包裹</button>
          <button type="button" class="order-button" @click="reship = null; error = ''">取消补发</button>
        </div>
      </fieldset>
      <p v-if="groups.length" class="order-muted" role="status">共 {{ groups.length }} 个包裹</p>
      <fieldset v-for="(group, index) in groups" :key="group.id" :disabled="busy || locked || !!reship" class="parcel-card">
        <legend>包裹 {{ index + 1 }}</legend>
        <div class="order-row mb-4">
          <span class="order-muted">{{ group.items.length }} 条商品明细 · 共 {{ group.quantity }} 件</span>
          <button type="button" class="order-link" :disabled="busy || locked" @click="removeGroup(group)">删除包裹</button>
        </div>
        <div class="order-form-columns">
          <label>国内快递（选填）
            <select :value="group.carrierConflict ? '__conflict__' : group.carrier" class="order-input" aria-label="国内快递" @change="updateGroup(group, 'carrier', $event)">
              <option v-if="group.carrierConflict" value="__conflict__" disabled>快递公司不一致，请核对</option>
              <option value="">未填写</option>
              <option v-for="carrier in group.carrier && !domesticCarriers.includes(group.carrier) ? [...domesticCarriers, group.carrier] : domesticCarriers" :key="carrier">{{ carrier }}</option>
            </select>
          </label>
          <label>国内快递单号
            <input :value="group.tracking_number" class="order-input" aria-label="国内快递单号" placeholder="填写国内快递单号" maxlength="100" required @change="updateGroup(group, 'tracking_number', $event)" />
          </label>
        </div>
        <p v-if="group.carrierConflict" class="order-error mt-3">同一运单的快递公司不一致，请核对后选择，或清空为未填写。</p>
        <div v-for="parcel in group.items" :key="parcel.id" class="parcel-item mt-4">
          <div class="order-row mb-3">
            <strong>商品明细</strong>
            <div class="flex gap-3">
              <button type="button" class="order-link" :disabled="busy || locked || !group.tracking_number" @click="startReship(parcel.id)">补发此商品</button>
              <button type="button" class="order-link" :disabled="busy || locked" @click="removeItem(parcel.id)">移除商品</button>
            </div>
          </div>
          <label>包裹商品
            <select :value="parcel.line_key" class="order-input" aria-label="包裹商品" required @change="changeProduct(parcel, $event)">
              <option v-for="line in lines" :key="line.selection.line_key" :value="line.selection.line_key" :disabled="available(line.selection.line_key, parcel.id) < 1">
                {{ productLabel(line.selection.line_key) }} / 可分配 {{ available(line.selection.line_key, parcel.id) }} 件
              </option>
            </select>
          </label>
          <label class="mt-4">商品数量<input v-model.number="parcel.quantity" class="order-input" type="number" min="1" :max="maximum(parcel)" step="1" required /></label>
          <p class="order-muted mt-2">本条最多可分配 {{ maximum(parcel) }} 件（已扣除其他包裹和商品明细）。</p>
          <label v-if="choices(parcel).length" class="mt-4">此商品的 1688 运单<select class="order-input" aria-label="选择已查询运单" :value="selectedTracking(parcel)" @change="useTracking(parcel, $event)"><option value="">使用当前包裹单号</option><option v-for="(choice, choiceIndex) in choices(parcel)" :key="`${choice.carrier}:${choice.tracking_number}`" :value="String(choiceIndex)">{{ choice.carrier || '快递公司未提供' }} · {{ choice.tracking_number }}</option></select></label>
        </div>
        <button type="button" class="order-button mt-4" :disabled="busy || locked || !canAddItem || !group.tracking_number" @click="add(group)">添加同包裹商品</button>
      </fieldset>
      <div class="flex flex-wrap gap-2">
        <button type="button" class="order-button" :disabled="busy || locked || !!reship || !lines.length" @click="add()">直接录入包裹</button>
        <button v-if="reshipSources.length" type="button" class="order-button" :disabled="busy || locked || !!reship" @click="startReship()">录入补发包裹</button>
      </div>
      <p class="order-muted">同一运单只显示一个包裹。漏发后补寄请选择“补发此商品”，填写新单号及补发数量；系统会自动调整原包裹的商品数量。</p>
    </form>
    <template #footer><div class="order-footer"><button class="order-button" :disabled="busy" @click="emit('close')">取消</button><button form="fulfillment-parcels" class="order-button order-primary" :disabled="busy || locked || !!reship || !lines.length">{{ busy ? '保存中…' : '保存包裹' }}</button></div></template>
  </WorkspaceDialog>
</template>
<style scoped>
.parcel-card { padding: 16px; border: 1px solid var(--order-border); border-radius: 6px; }
legend { font-size: 14px; font-weight: 600; }
.parcel-card label { display: grid; gap: 8px; }
.parcel-item { border-top: 1px solid var(--order-border); padding-top: 16px; }
</style>
