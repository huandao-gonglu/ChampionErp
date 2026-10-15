<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { fetchReceivingWarehouse } from '@/api/receivingWarehouse'
import type { ReceivingWarehouseView } from '@/api/receivingWarehouse'
import ReceivingWarehouseDialog from './ReceivingWarehouseDialog.vue'
const props = defineProps<{ orderId: string; lineKey: string; purchaseState: string }>()
const emit = defineEmits<{ lock: [boolean]; updated: [] }>()
const value = ref<ReceivingWarehouseView | null>(null)
const error = ref(''), opened = ref(false), loading = ref(false)
let disposed = false
watch(opened, v => emit('lock', v), { flush: 'sync' })
async function load() {
  loading.value = true; error.value = ''
  try { const result = await fetchReceivingWarehouse(props.orderId); if (!disposed) value.value = result }
  catch (exc) { if (!disposed) error.value = exc instanceof Error ? exc.message : '收货仓读取失败' }
  finally { if (!disposed) loading.value = false }
}
function saved(result: ReceivingWarehouseView) { value.value = result; opened.value = false; emit('updated') }
watch(() => props.purchaseState, () => { if (!opened.value) void load() })
onMounted(load)
onBeforeUnmount(() => { disposed = true; emit('lock', false) })
</script>
<template>
  <section class="mt-5 rounded-lg border p-4" aria-label="采购收货仓" style="border-color: var(--order-border); background: var(--order-soft)">
    <div class="order-row"><h3 class="font-semibold">采购收货仓</h3><button class="order-link" :disabled="loading" @click="load">刷新</button></div>
    <p v-if="error" role="alert" class="order-error mt-3">{{ error }}</p>
    <p v-if="loading" role="status" class="order-muted mt-3">正在读取收货安排…</p>
    <template v-if="value">
      <p class="mt-3">跨境物流商：{{ value.delivery_name || '待确认' }}</p>
      <template v-if="value.warehouse">
        <p v-if="value.binding_stale" class="order-error mt-3">原收货绑定已失效，以下为上次确认资料。</p>
        <p class="mt-3">{{ value.warehouse.warehouse_name }}<template v-if="value.warehouse.delivery_key === value.delivery_key"> · {{ value.warehouse.transport_service }}</template></p>
        <p class="mt-2">{{ value.warehouse.address.fullName }} · {{ value.warehouse.address.mobile || value.warehouse.address.phone }}</p>
        <p class="mt-2 break-words">{{ [value.warehouse.address.provinceText, value.warehouse.address.cityText, value.warehouse.address.areaText, value.warehouse.address.townText, value.warehouse.address.address].filter(Boolean).join(' ') }}</p>
      </template>
      <p v-if="value.blocked_reason" class="order-muted mt-3">{{ value.blocked_reason }}</p>
      <p v-else class="order-muted mt-3">采购使用此收货地址，跨境报单沿用此仓库。</p>
      <button v-if="value.can_confirm && (!value.locked || value.blocked_reason)" class="order-button mt-3" @click="opened = true">{{ value.binding_stale ? '重新设置报单绑定' : value.warehouse ? '核对收货安排' : value.has_purchases ? '补录实际收货仓' : '选择收货仓' }}</button>
      <p v-else-if="value.locked" class="order-muted mt-3">{{ value.reserved ? '采购正在提交或结果待核验，收货安排已锁定。' : '收货安排已锁定。' }}</p>
    </template>
  </section>
  <ReceivingWarehouseDialog v-if="opened && value" :value="value" :line-key="lineKey" @close="opened = false" @saved="saved" />
</template>
