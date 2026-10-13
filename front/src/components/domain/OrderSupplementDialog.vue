<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { lookupSupplement, recordSupplement } from '@/api/orders'
import { fetchReceivingWarehouse, type ReceivingWarehouseView } from '@/api/receivingWarehouse'
import type { OrderDetail, OrderSnapshot, ProcurementLine } from '@/types/orders'
import type { BackendSupplementLookupResult } from '@/types/workflow.generated'
const props = defineProps<{ item: ProcurementLine; order: OrderSnapshot }>()
const emit = defineEmits<{ close: []; updated: [OrderDetail] }>()
const number = ref('')
const result = ref<BackendSupplementLookupResult | null>(null)
const selected = ref('')
const confirmed = ref(false)
const busy = ref(false)
const error = ref('')
const warehouse = ref<ReceivingWarehouseView | null>(null)
const requestId = ref(crypto.randomUUID())
const candidate = computed(() => result.value?.candidates.find(c => c.key === selected.value))
const warehouseReady = computed(() => props.order.platform !== 'yandex' || (!!warehouse.value?.warehouse && !warehouse.value.blocked_reason))
const ready = computed(() => !busy.value && warehouseReady.value && confirmed.value && candidate.value && candidate.value.quantity <= props.item.line.quantity)
const address = computed(() => warehouse.value?.warehouse?.address)
watch(number, () => { result.value = null; selected.value = ''; confirmed.value = false; requestId.value = crypto.randomUUID() })
watch(selected, () => { confirmed.value = false; requestId.value = crypto.randomUUID() })
function body() { return { order_id: props.order.id, line_key: props.item.selection.line_key, revision: props.item.selection.revision, purchase_order_number: number.value.trim() } }
async function lookup() {
  if (busy.value || !number.value.trim()) return
  busy.value = true; error.value = ''; result.value = null; confirmed.value = false
  try {
    if (props.order.platform === 'yandex') warehouse.value = await fetchReceivingWarehouse(props.order.id)
    result.value = await lookupSupplement(body())
    const matches = result.value.candidates.filter(c => c.matched)
    selected.value = matches.length === 1 ? matches[0]!.key : result.value.candidates.length === 1 ? result.value.candidates[0]!.key : ''
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '补单读取失败' }
  finally { busy.value = false }
}
async function save() {
  if (!ready.value) return
  busy.value = true; error.value = ''
  try {
    emit('updated', await recordSupplement({ ...body(), candidate_key: selected.value, request_id: requestId.value,
      receiving_revision: warehouse.value?.warehouse?.revision || 0, confirmed: true }))
    emit('close')
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '补单登记失败，请重试' }
  finally { busy.value = false }
}
onMounted(async () => {
  if (props.order.platform !== 'yandex') return
  try { warehouse.value = await fetchReceivingWarehouse(props.order.id) }
  catch (exc) { error.value = exc instanceof Error ? exc.message : '收货仓读取失败' }
})
</script>
<template>
  <WorkspaceDialog :open="true" title="登记补单" width="600px" class="order-ui" :close-disabled="busy" @close="emit('close')">
    <div class="order-form">
      <p>{{ item.line.title }} · {{ item.line.sku }} · 需发 {{ item.line.quantity }} 件</p>
      <p class="order-muted">在 1688 手动采购后填写订单号，系统将跟踪采购状态并获取运单。</p>
      <section v-if="warehouse?.warehouse && address" class="order-muted">
        <p>寄往：{{ warehouse.warehouse.warehouse_name }}</p>
        <p>{{ address.fullName }} · {{ address.mobile || address.phone }}</p>
        <p>{{ [address.provinceText, address.cityText, address.areaText, address.townText, address.address].filter(Boolean).join(' ') }}</p>
      </section>
      <label>1688 新采购单号<input v-model.trim="number" class="order-input" inputmode="numeric" maxlength="30" :disabled="busy" @keyup.enter="lookup" /></label>
      <button class="order-button" :disabled="busy || !number" @click="lookup">{{ busy ? '处理中…' : '读取采购单' }}</button>
      <template v-if="result">
        <p class="order-muted">{{ result.order_status }}</p>
        <label>本 SKU 对应的采购商品<select v-model="selected" class="order-input" :disabled="busy"><option value="" disabled>请选择对应商品</option><option v-for="c in result.candidates" :key="c.key" :value="c.key">{{ c.title }} · {{ c.specification || c.sku_id }} · {{ c.quantity }} 件</option></select></label>
        <p v-if="candidate && candidate.quantity > item.line.quantity" class="order-error">该采购商品数量超过本 SKU 所需数量，请核对采购单。</p>
        <label class="order-check"><input v-model="confirmed" type="checkbox" :disabled="busy || !candidate" />已核对商品和规格，确认寄往本单收货仓</label>
      </template>
      <p v-if="warehouse?.blocked_reason" class="order-error">{{ warehouse.blocked_reason }}</p>
      <p v-if="error" class="order-error" role="alert">{{ error }}</p>
    </div>
    <template #footer><div class="order-footer"><button class="order-button" :disabled="busy" @click="emit('close')">取消</button><button class="order-button order-primary" :disabled="!ready" @click="save">{{ busy ? '处理中…' : '确认补单并跟踪' }}</button></div></template>
  </WorkspaceDialog>
</template>
