<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { busCommand, fulfillmentCommand } from '@/api/fulfillment'
import type { BusSection, FulfillmentDetail, FulfillmentPlan } from '@/types/fulfillment'
import type { OrderSnapshot } from '@/types/orders'
import FulfillmentPlanFields from './FulfillmentPlanFields.vue'
import OrderAddressNote from './OrderAddressNote.vue'
const props = defineProps<{ value: FulfillmentDetail; order: OrderSnapshot }>()
const emit = defineEmits<{ close: []; saved: [FulfillmentDetail] }>()
const yandex = computed(() => props.order.platform === 'yandex')
const target = computed(() => props.value.handover_target)
const noteShipment = computed(() => {
  if (!target.value?.key || props.order.id !== props.value.erp_order_id) return
  // 同一地址的多个批次共享备注，只取与当前展示地址一致的有效批次。
  return props.order.handover?.shipments.find(shipment => {
    if (shipment.status === 'ERROR' || shipment.shipment_type !== target.value?.shipment_type) return false
    const point = shipment.shipment_type === 'IMPORT' ? shipment.destination : shipment.shipment_type === 'WITHDRAW' ? shipment.origin : null
    return point?.id === target.value.warehouse_id && point?.address === target.value.address
  })
})
const noteOpen = ref(false)
const initialKey = props.value.handover_target?.key || ''
const initialLinkRevision = props.value.warehouse_link?.revision || 0
const plan = ref<FulfillmentPlan>({ section_id: props.value.plan?.section_id || 0, warehouse_id: props.value.plan?.warehouse_id || 0, service_ids: [...(props.value.plan?.service_ids || [])] })
const remark = ref(props.value.remark || '')
const sections = ref<BusSection[]>([])
const loading = ref(true)
const busy = ref(false)
const ready = ref(false)
const confirmed = ref(false)
const remap = ref(false)
const error = ref('')
const needsConfirmation = computed(() => yandex.value && (!props.value.warehouse_link || remap.value))
const warehouseLocked = computed(() => yandex.value && !!props.value.warehouse_link && !remap.value)
const addressChanged = computed(() => initialKey !== (target.value?.key || '') || initialLinkRevision !== (props.value.warehouse_link?.revision || 0))
let disposed = false
watch(() => [plan.value.section_id, plan.value.warehouse_id], () => { confirmed.value = false })
async function load() {
  loading.value = true
  error.value = ''
  try {
    const settings = await busCommand('catalog')
    if (disposed) return
    sections.value = (settings.catalog.sections || []).filter(s => !yandex.value || ['yandex', 'yandex market', 'яндекс', 'яндекс маркет'].includes(s.section_name.trim().toLowerCase()))
    if (!plan.value.section_id && sections.value.length === 1) plan.value.section_id = sections.value[0]!.section_id
  } catch (exc) { if (!disposed) error.value = exc instanceof Error ? exc.message : '合作仓库读取失败' }
  finally { if (!disposed) loading.value = false }
}
async function save() {
  if (busy.value || noteOpen.value || loading.value || !ready.value || addressChanged.value || (needsConfirmation.value && !confirmed.value)) return
  busy.value = true
  error.value = ''
  try {
    emit('saved', await fulfillmentCommand('plan', props.value.erp_order_id, props.value.revision, {
      plan: plan.value,
      remark: remark.value,
      ...(yandex.value ? { handover_key: initialKey, warehouse_link_revision: initialLinkRevision, confirm_warehouse: needsConfirmation.value && confirmed.value } : {}),
    }))
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '报单方案保存失败' }
  finally { busy.value = false }
}
onMounted(load)
onBeforeUnmount(() => { disposed = true })
</script>
<template>
  <WorkspaceDialog :open="true" title="设置报单方案" width="740px" class="order-ui" :close-disabled="busy || noteOpen" @close="emit('close')">
    <form id="fulfillment-plan" class="order-form" @submit.prevent="save">
      <p v-if="error" class="order-error" role="alert">{{ error }} <button type="button" class="order-link" :disabled="busy || loading" @click="load">重新读取仓库</button></p>
      <section v-if="yandex" class="handover-card">
        <div class="order-row">
          <h3>{{ target?.shipment_type === 'WITHDRAW' ? 'Yandex 揽收仓库' : 'Yandex 交货仓库' }}</h3>
          <OrderAddressNote v-if="noteShipment && target && !busy" :key="`${order.id}:${noteShipment.shipment_id}:${target.address}`" :order-id="order.id" :shipment-id="noteShipment.shipment_id" :address="target.address" @lock="noteOpen = $event" />
        </div>
        <p>{{ target?.name || '尚未提供' }}<span v-if="target?.warehouse_id" class="order-muted"> · ID {{ target.warehouse_id }}</span></p>
        <p class="address">{{ target?.address || target?.reason }}</p>
        <p v-if="addressChanged" role="alert" class="order-error">交货信息或仓库对应关系已变化，请关闭后重新选择。</p>
      </section>
      <p v-if="loading" role="status" class="order-muted">正在读取最新合作仓库…</p>
      <template v-else-if="!yandex || target?.key">
        <p v-if="!sections.length" class="order-muted">当前账号尚未返回可用的合作仓库。</p>
        <template v-else>
          <p v-if="warehouseLocked" class="order-muted">已按确认过的交货地址对应到仓库。<button type="button" class="order-link" :disabled="busy" @click="remap = true">重新对应</button></p>
          <FulfillmentPlanFields v-model="plan" :sections="sections" :disabled="busy || addressChanged" :section-locked="warehouseLocked || (!yandex && !!value.rule)" :warehouse-locked="warehouseLocked" :compatible-warehouse-ids="yandex ? undefined : value.rule?.compatible_warehouse_ids" @ready="ready = $event" />
          <label v-if="needsConfirmation" class="order-check"><input v-model="confirmed" type="checkbox" :disabled="busy || !plan.warehouse_id" />我已核对地址，所选合作仓库就是上述交货仓库；保存后用于相同交货地址的订单。</label>
        </template>
      </template>
      <label for="fulfillment-remark">报单备注（选填）</label>
      <textarea id="fulfillment-remark" v-model="remark" class="order-input" rows="3" maxlength="1000" :disabled="busy || noteOpen || addressChanged" placeholder="填写需要告知仓库的要求" />
      <p class="order-muted">留空则不发送备注。</p>
      <p class="order-muted">基础服务选一项，附加服务按需选择。编辑期间暂停本单自动预报。</p>
    </form>
    <template #footer><div class="order-footer"><button type="button" class="order-button" :disabled="busy || noteOpen" @click="emit('close')">取消</button><button form="fulfillment-plan" class="order-button order-primary" :disabled="busy || noteOpen || loading || !ready || addressChanged || (needsConfirmation && !confirmed)">{{ busy ? '正在保存…' : '保存报单方案' }}</button></div></template>
  </WorkspaceDialog>
</template>
<style scoped>
.handover-card { padding: 14px; border: 1px solid var(--order-border); border-radius: 6px; background: var(--order-soft); }
h3 { font-weight: 600; }
p + p, .order-row + p { margin-top: 8px; }
.address { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
