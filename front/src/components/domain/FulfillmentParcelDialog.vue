<script setup lang="ts">
import { computed, ref } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { fetchFulfillment, fulfillmentCommand } from '@/api/fulfillment'
import type { FulfillmentDetail, DomesticParcel } from '@/types/fulfillment'
import type { OrderDetail } from '@/types/orders'
import { domesticCarriers, purchaseLogisticsChoices, seedDomesticParcels } from './purchaseLogistics'
const props = defineProps<{ value: FulfillmentDetail; order: OrderDetail }>()
const purchaseLogistics = computed(() => Object.fromEntries(props.order.lines.flatMap(line => line.records)
  .filter(record => record.progress?.data).map(record => [record.id, record.progress!.data!])))
const emit = defineEmits<{ close: []; saved: [FulfillmentDetail] }>()
const parcels = ref<DomesticParcel[]>(seedDomesticParcels(props.order, props.value.parcels, purchaseLogistics.value))
const busy = ref(false)
const error = ref('')
const revision = ref(props.value.revision)
const locked = ref(!props.value.editable)
const purchases = computed(() => props.order.lines.flatMap(line => line.records.filter(r => r.status === 'purchased').map(record => ({ lineKey: line.selection.line_key, record, line: line.line }))))
const hasQueriedLogistics = computed(() => purchases.value.some(source => purchaseLogisticsChoices(source.record, purchaseLogistics.value).length > 0))
function add() {
  const first = purchases.value[0]
  if (!first) return
  const parcel = { id: crypto.randomUUID(), line_key: first.lineKey, purchase_record_id: first.record.id, carrier: '', tracking_number: '', quantity: 1 }
  select(parcel)
  parcels.value.push(parcel)
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
  const index = choices(parcel).findIndex(choice => choice.tracking_number === parcel.tracking_number && choice.carrier === parcel.carrier)
  return index < 0 ? '' : String(index)
}
function select(parcel: DomesticParcel) {
  const source = purchases.value.find(p => p.record.id === parcel.purchase_record_id)
  if (source) parcel.line_key = source.lineKey
  const known = choices(parcel)
  if (!parcel.tracking_number && known.length === 1) {
    parcel.tracking_number = known[0]!.tracking_number
    if (!parcel.carrier) parcel.carrier = known[0]!.carrier
  }
}
async function save() {
  if (busy.value || locked.value) return
  busy.value = true; error.value = ''
  try { emit('saved', await fulfillmentCommand('parcels', props.value.erp_order_id, revision.value, { parcels: parcels.value })) }
  catch (exc) {
    const message = exc instanceof Error ? exc.message : '包裹保存失败'
    try {
      const latest = await fetchFulfillment(props.value.erp_order_id)
      revision.value = latest.revision
      locked.value = !latest.editable
      error.value = latest.editable ? `${message}。已刷新订单版本，请核对包裹后再次保存。` : `${message}。资料已锁定，请关闭并刷新履约详情。`
    } catch { error.value = message }
  }
  finally { busy.value = false }
}
if (!parcels.value.length) add()
</script>
<template>
  <WorkspaceDialog :open="true" :title="value.crossborderbus_order_id ? '管理国内包裹' : '录入国内包裹'" width="760px" class="order-ui" :close-disabled="busy" @close="emit('close')">
    <form id="fulfillment-parcels" class="order-form" @submit.prevent="save">
      <p class="order-muted">订单 {{ order.order.order_id }} · 包裹逐项关联采购记录，保存时提交完整包裹列表。</p>
      <p v-if="hasQueriedLogistics" class="order-muted">已载入查询到的 1688 运单；请核对采购规格与各包裹数量后保存。已有包裹保持原值，多个运单可逐项选择。</p>
      <p v-if="error" class="order-error" role="alert">{{ error }}</p>
      <p v-if="!purchases.length" class="order-error">尚无有效采购记录，请先在“商品与采购”中登记实际采购。</p>
      <fieldset v-for="(parcel, index) in parcels" :key="parcel.id" :disabled="busy || locked" class="parcel-card">
        <div class="order-row mb-4"><legend>包裹 {{ index + 1 }}</legend><button type="button" class="order-link" :disabled="busy" @click="parcels.splice(index, 1)">删除</button></div>
        <label>关联采购记录<select v-model="parcel.purchase_record_id" class="order-input" required @change="select(parcel)"><option v-for="source in purchases" :key="source.record.id" :value="source.record.id">{{ source.line.sku }} / {{ source.record.source.specification }} · {{ source.record.purchase_order_number }} / {{ source.record.quantity }} 件</option></select></label>
        <label v-if="choices(parcel).length" class="mt-4">1688 已查询运单<select class="order-input" aria-label="选择已查询运单" :value="selectedTracking(parcel)" @change="useTracking(parcel, $event)"><option value="">请选择运单，或保留下方手填值</option><option v-for="(choice, choiceIndex) in choices(parcel)" :key="`${choice.carrier}:${choice.tracking_number}`" :value="String(choiceIndex)">{{ choice.carrier || '快递公司未提供' }} · {{ choice.tracking_number }}</option></select></label>
        <div class="order-form-columns mt-4"><label>国内快递<select v-model="parcel.carrier" class="order-input" required><option value="">请选择快递公司</option><option v-for="carrier in parcel.carrier && !domesticCarriers.includes(parcel.carrier) ? [...domesticCarriers, parcel.carrier] : domesticCarriers" :key="carrier">{{ carrier }}</option></select></label><label>商品数量<input v-model.number="parcel.quantity" class="order-input" type="number" min="1" step="1" required /></label></div>
        <label class="mt-4">国内快递单号<input v-model.trim="parcel.tracking_number" class="order-input" placeholder="填写供应商发货的国内快递单号" maxlength="100" required /></label>
      </fieldset>
      <button type="button" class="order-button justify-self-start" :disabled="busy || locked || !purchases.length" @click="add">添加包裹</button>
      <p class="order-muted">同一快递包含多种商品时，可逐项关联不同采购记录并使用相同快递单号。</p>
    </form>
    <template #footer><div class="order-footer"><button class="order-button" :disabled="busy" @click="emit('close')">取消</button><button form="fulfillment-parcels" class="order-button order-primary" :disabled="busy || locked || !purchases.length">{{ busy ? '保存中…' : '保存包裹' }}</button></div></template>
  </WorkspaceDialog>
</template>
<style scoped>
.parcel-card { padding: 16px; border: 1px solid var(--order-border); border-radius: 6px; }
legend { font-size: 14px; font-weight: 600; }
.parcel-card label { display: grid; gap: 8px; }
</style>
