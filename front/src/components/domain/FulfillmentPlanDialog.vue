<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
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
const initialReceivingRevision = props.value.receiving_warehouse?.revision || 0
const plan = ref<FulfillmentPlan>({ section_id: props.value.plan?.section_id || 0, warehouse_id: props.value.plan?.warehouse_id || 0, service_ids: [...(props.value.plan?.service_ids || [])] })
const remark = ref(props.value.remark || '')
const sections = ref<BusSection[]>([])
const loading = ref(true)
const busy = ref(false)
const ready = ref(false)
const error = ref('')
const warehouseLocked = computed(() => yandex.value)
const receivingChanged = computed(() => yandex.value && (!initialReceivingRevision || initialReceivingRevision !== (props.value.receiving_warehouse?.revision || 0)))
let disposed = false
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
  if (busy.value || noteOpen.value || loading.value || !ready.value || receivingChanged.value) return
  busy.value = true
  error.value = ''
  try {
    emit('saved', await fulfillmentCommand('plan', props.value.erp_order_id, props.value.revision, {
      plan: plan.value,
      remark: remark.value,
      ...(yandex.value ? { receiving_revision: initialReceivingRevision } : {}),
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
      <section v-if="yandex && value.receiving_warehouse" class="handover-card">
        <h3>采购收货仓：{{ value.receiving_warehouse.warehouse_name }}</h3>
        <p>{{ value.receiving_warehouse.transport_service }}</p>
        <p>{{ value.receiving_warehouse.address.fullName }} · {{ value.receiving_warehouse.address.mobile || value.receiving_warehouse.address.phone }}</p>
        <p class="address">{{ [value.receiving_warehouse.address.provinceText, value.receiving_warehouse.address.cityText, value.receiving_warehouse.address.areaText, value.receiving_warehouse.address.townText, value.receiving_warehouse.address.address].filter(Boolean).join(' ') }}</p>
      </section>
      <p v-if="receivingChanged" role="alert" class="order-error">采购收货仓尚未确定或已变化，请在商品与采购中核对后重新打开。</p>
      <section v-if="yandex" class="handover-card">
        <div class="order-row">
          <h3>{{ target?.shipment_type === 'WITHDRAW' ? 'Yandex 揽收仓库' : 'Yandex 交货仓库' }}</h3>
          <OrderAddressNote v-if="noteShipment && target && !busy" :key="`${order.id}:${noteShipment.shipment_id}:${target.address}`" :order-id="order.id" :shipment-id="noteShipment.shipment_id" :address="target.address" @lock="noteOpen = $event" />
        </div>
        <p>{{ target?.name || '尚未提供' }}<span v-if="target?.warehouse_id" class="order-muted"> · ID {{ target.warehouse_id }}</span></p>
        <p class="address">{{ target?.address || target?.reason }}</p>
        <p class="order-muted">平台交货信息，仅供核对。</p>
      </section>
      <p v-if="loading" role="status" class="order-muted">正在读取最新合作仓库…</p>
      <template v-else-if="!yandex || value.receiving_warehouse">
        <p v-if="!sections.length" class="order-muted">当前账号尚未返回可用的合作仓库。</p>
        <template v-else>
          <p v-if="warehouseLocked" class="order-muted">报单沿用采购收货仓，请选择本次仓库服务。</p>
          <FulfillmentPlanFields v-model="plan" :sections="sections" :disabled="busy || receivingChanged" :section-locked="warehouseLocked || (!yandex && !!value.rule)" :warehouse-locked="warehouseLocked" :compatible-warehouse-ids="yandex ? undefined : value.rule?.compatible_warehouse_ids" @ready="ready = $event" />
        </template>
      </template>
      <label for="fulfillment-remark">报单备注（选填）</label>
      <textarea id="fulfillment-remark" v-model="remark" class="order-input" rows="3" maxlength="1000" :disabled="busy || noteOpen || receivingChanged" placeholder="填写需要告知仓库的要求" />
      <p class="order-muted">留空则不发送备注。</p>
      <p class="order-muted">基础服务选一项，附加服务按需选择。编辑期间暂停本单自动预报。</p>
    </form>
    <template #footer><div class="order-footer"><button type="button" class="order-button" :disabled="busy || noteOpen" @click="emit('close')">取消</button><button form="fulfillment-plan" class="order-button order-primary" :disabled="busy || noteOpen || loading || !ready || receivingChanged">{{ busy ? '正在保存…' : '保存报单方案' }}</button></div></template>
  </WorkspaceDialog>
</template>
<style scoped>
.handover-card { padding: 14px; border: 1px solid var(--order-border); border-radius: 6px; background: var(--order-soft); }
h3 { font-weight: 600; }
p + p, .order-row + p { margin-top: 8px; }
.address { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
