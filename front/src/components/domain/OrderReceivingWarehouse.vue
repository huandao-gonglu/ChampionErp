<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { PhLockSimple } from '@phosphor-icons/vue'
import { fetchReceivingWarehouse } from '@/api/receivingWarehouse'
import type { ReceivingWarehouseView } from '@/api/receivingWarehouse'
import ReceivingWarehouseDialog from './ReceivingWarehouseDialog.vue'
const props = withDefaults(defineProps<{ orderId: string; lineKey: string; purchaseState: string; compact?: boolean }>(), { compact: false })
const emit = defineEmits<{ lock: [boolean]; updated: []; loaded: [ReceivingWarehouseView] }>()
const value = ref<ReceivingWarehouseView | null>(null)
const error = ref(''), opened = ref(false), loading = ref(false), notice = ref('')
const expanded = ref(!props.compact)
const address = computed(() => value.value?.warehouse?.address)
const fullAddress = computed(() => address.value ? [address.value.provinceText, address.value.cityText, address.value.areaText, address.value.townText, address.value.address].filter(Boolean).join(' ') : '')
let disposed = false
let generation = 0
watch(opened, v => emit('lock', v), { flush: 'sync' })
async function load() {
  const current = ++generation
  loading.value = true; error.value = ''
  try {
    const result = await fetchReceivingWarehouse(props.orderId)
    if (!disposed && current === generation) { value.value = result; emit('loaded', result) }
  } catch (exc) { if (!disposed && current === generation) error.value = exc instanceof Error ? exc.message : '收货仓读取失败' }
  finally { if (!disposed && current === generation) loading.value = false }
}
function saved(result: ReceivingWarehouseView) { value.value = result; opened.value = false; emit('loaded', result); emit('updated') }
async function open() {
  if (!value.value && !loading.value) await load()
  if (value.value?.can_confirm && (!value.value.locked || value.value.blocked_reason)) opened.value = true
  else expanded.value = true
}
async function copyAddress() {
  if (!address.value) return
  try {
    await navigator.clipboard.writeText([address.value.fullName, address.value.mobile || address.value.phone, fullAddress.value].filter(Boolean).join('\n'))
    notice.value = '收货地址已复制'
  } catch { error.value = '复制失败，请展开后手动复制地址。' }
}
watch(() => props.purchaseState, () => { if (!opened.value) void load() })
onMounted(load)
onBeforeUnmount(() => { disposed = true; generation++; emit('lock', false) })
defineExpose({ open })
</script>
<template>
  <section class="receiving-card" :class="{ compact }" aria-label="采购收货仓">
    <div class="order-row">
      <h3>采购收货仓</h3><span v-if="value?.locked && !value.binding_stale" class="order-badge" data-tone="amber">
        <PhLockSimple :size="14" aria-hidden="true" />已锁定
      </span><span v-else-if="value?.binding_stale" class="order-badge" data-tone="red">
        待重绑
      </span>
    </div>
    <p v-if="error" role="alert" class="order-error">
      {{ error }}
    </p>
    <p v-if="loading && !value" role="status" class="order-muted mt-3">
      正在读取收货安排…
    </p>
    <template v-if="value">
      <p class="warehouse-name">
        {{ value.warehouse?.warehouse_name || '尚未选择收货仓' }}
      </p>
      <p v-if="value.warehouse" class="order-muted address-summary" :title="fullAddress">
        {{ [address?.provinceText, address?.cityText, value.warehouse.transport_service].filter(Boolean).join(' · ') }}
      </p>
      <p v-if="value.binding_stale" class="order-error">
        原收货绑定已失效，以下为上次确认资料。
      </p>
      <details :open="expanded" @toggle="expanded = ($event.target as HTMLDetailsElement).open">
        <summary v-show="!compact" class="order-link">
          {{ expanded ? '收起收货资料' : '展开收货资料' }}
        </summary>
        <div class="receiving-details">
          <p class="order-muted">
            跨境物流商：{{ value.delivery_name || '待确认' }}
          </p>
          <template v-if="value.warehouse">
            <p>{{ value.warehouse.transport_service }}</p>
            <p>{{ address?.fullName }} · {{ address?.mobile || address?.phone }}</p>
            <p class="full-address">
              {{ fullAddress }}
            </p>
          </template>
          <p class="order-muted">
            {{ value.blocked_reason || '采购使用此收货地址，跨境报单沿用此仓库。' }}
          </p>
          <p v-if="value.locked && !value.blocked_reason" class="order-muted">
            {{ value.reserved ? '采购正在提交或结果待核验，收货安排已锁定。' : '收货安排已锁定。' }}
          </p>
        </div>
      </details>
      <p v-if="!value.warehouse && value.blocked_reason" class="order-muted mt-2">
        {{ value.blocked_reason }}
      </p>
      <div class="order-actions receiving-tools">
        <button v-if="value.warehouse" class="order-link" @click="copyAddress">
          复制地址
        </button>
        <button v-if="compact" class="order-link" :aria-expanded="expanded" @click="expanded = !expanded">
          {{ expanded ? '收起收货资料' : '展开收货资料' }}
        </button>
        <button v-if="value.can_confirm && (!value.locked || value.blocked_reason)" class="order-link" @click="open">
          {{ value.binding_stale ? '重新设置报单绑定' : value.warehouse ? '核对收货安排' : value.has_purchases ? '补录实际收货仓' : '选择收货仓' }}
        </button>
        <button class="order-link" :disabled="loading" @click="load">
          刷新
        </button>
      </div>
    </template>
    <button
      v-else-if="error"
      class="order-link"
      :disabled="loading"
      @click="load"
    >
      重新读取
    </button>
    <p v-if="notice" class="order-muted" role="status">
      {{ notice }}
    </p>
  </section>
  <ReceivingWarehouseDialog
    v-if="opened && value"
    :value="value"
    :line-key="lineKey"
    @close="opened = false"
    @saved="saved"
  />
</template>
<style scoped>
.receiving-card { padding: 20px; border: 1px solid var(--order-border); border-radius: 8px; background: var(--order-soft); }
.receiving-card.compact { padding: 0 0 14px; border: 0; border-bottom: 1px solid var(--order-border); border-radius: 0; background: transparent; }
h3 { font-size: 16px; font-weight: 600; }
.order-badge { display: inline-flex; align-items: center; gap: 4px; min-width: 0; }
.warehouse-name { font-weight: 500; margin-top: 12px; overflow-wrap: anywhere; }
.address-summary { margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
summary.order-link { display: inline-block; padding: 8px 0 0; }
.receiving-details { display: grid; gap: 8px; margin-top: 12px; }
.full-address { overflow-wrap: anywhere; white-space: pre-wrap; }
.receiving-tools { justify-content: space-between; margin-top: 8px; }
.receiving-tools .order-link { padding: 4px 0; }
.compact .warehouse-name { margin-top: 8px; }
.compact .receiving-tools { margin-top: 4px; }
</style>
