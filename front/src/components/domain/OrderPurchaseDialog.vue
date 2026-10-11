<script setup lang="ts">
import { onMounted, ref, useId } from 'vue'
import { fetchReceivingWarehouse } from '@/api/receivingWarehouse'
import type { ReceivingWarehouseView } from '@/api/receivingWarehouse'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { procurementCommand } from '@/api/orders'
import type { OrderDetail, ProcurementLine } from '@/types/orders'
const props = defineProps<{ item: ProcurementLine; orderId: string }>()
const emit = defineEmits<{ close: []; updated: [detail: OrderDetail] }>()
const formId = useId()
const quantity = ref(Math.max(1, props.item.remaining_quantity))
const purchaseNumber = ref('')
const busy = ref(false)
const error = ref('')
const receiving = ref<ReceivingWarehouseView | null>(null)
const receivingConfirmed = ref(false)
const loading = ref(true)
onMounted(async () => {
  try { receiving.value = await fetchReceivingWarehouse(props.orderId) }
  catch (exc) { error.value = exc instanceof Error ? exc.message : '收货仓读取失败' }
  finally { loading.value = false }
})
let lastPurchase = { fingerprint: '', id: '' }
async function save() {
  if (busy.value || loading.value || (receiving.value?.warehouse && !receivingConfirmed.value)) return
  const body = {
    order_id: props.orderId,
    line_key: props.item.selection.line_key,
    revision: props.item.selection.revision,
    quantity: quantity.value,
    purchase_order_number: purchaseNumber.value.trim(),
    receiving_revision: receiving.value?.revision || 0,
    receiving_confirmed: receivingConfirmed.value,
  }
  if (
    !body.purchase_order_number ||
    !Number.isInteger(body.quantity) ||
    body.quantity < 1 ||
    body.quantity > props.item.remaining_quantity
  ) {
    error.value = '请填写有效的采购数量和采购单号'
    return
  }
  const fingerprint = JSON.stringify(body)
  if (lastPurchase.fingerprint !== fingerprint)
    lastPurchase = { fingerprint, id: crypto.randomUUID() }
  busy.value = true
  error.value = ''
  try {
    emit(
      'updated',
      await procurementCommand('record-purchase', {
        ...body,
        request_id: lastPurchase.id,
      })
    )
    emit('close')
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '采购记录保存失败'
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <WorkspaceDialog
    :open="true"
    title="记录采购"
    width="520px"
    class="order-ui"
    :close-disabled="busy"
    @close="$emit('close')"
  >
    <h4 class="font-semibold">{{ item.line.title }}</h4>
    <p class="order-muted mt-2 mb-7">
      {{ item.selection.source?.specification }} · 还需采购 {{ item.remaining_quantity }} 件
    </p>
    <form :id="formId" class="order-form" @submit.prevent="save">
      <template v-if="receiving?.warehouse">
        <p>收货仓：{{ receiving.warehouse.warehouse_name }}</p>
        <p>{{ receiving.warehouse.address.fullName }} · {{ receiving.warehouse.address.mobile || receiving.warehouse.address.phone }}</p>
        <p>{{ [receiving.warehouse.address.provinceText, receiving.warehouse.address.cityText, receiving.warehouse.address.areaText, receiving.warehouse.address.address].join(' ') }}</p>
        <label class="order-check"><input v-model="receivingConfirmed" type="checkbox" />本次采购使用了上述收货仓和地址</label>
      </template>
      <p v-else class="order-muted">登记实际采购后，请补录并核对实际收货仓，再进行跨境报单。</p>
      <label>已采购数量<input
        v-model.number="quantity"
        class="order-input"
        type="number"
        min="1"
        step="1"
        :max="item.remaining_quantity"
        required
      /></label>
      <label>采购单号<input v-model="purchaseNumber" class="order-input" required maxlength="200" /></label>
      <p class="order-muted">保存采购记录后自动刷新一次 1688 采购进度；能够确认归属的运单会自动保存到国内包裹。</p>
      <p v-if="error" class="order-error" role="alert">{{ error }}</p>
    </form>
    <template #footer>
      <div class="order-footer">
        <button class="order-button" :disabled="busy" @click="$emit('close')">取消</button><button class="order-button order-primary" :form="formId" type="submit" :disabled="busy || loading || (!!receiving?.warehouse && !receivingConfirmed)">
          {{ busy ? '保存中…' : '保存记录' }}
        </button>
      </div>
    </template>
  </WorkspaceDialog>
</template>
