<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { syncPurchase } from '@/api/orders'
import type { OrderDetail, PurchaseRecord } from '@/types/orders'
import OrderThumbnail from './OrderThumbnail.vue'
import { dateTime } from './orderPresentation'

const props = defineProps<{ orderId: string; record: PurchaseRecord }>()
const emit = defineEmits<{ updated: [detail: OrderDetail]; lock: [boolean] }>()
const supported = computed(() => props.record.status !== 'cancelled' && props.record.source_platform === '1688')
const pending = ref('')
const error = ref('')
const result = computed(() => props.record.progress?.data)
let controller: AbortController | null = null
function reset() {
  controller?.abort()
  controller = null
  pending.value = ''
  error.value = ''
  emit('lock', false)
}
watch(() => [props.orderId, props.record.id, props.record.purchase_order_number, props.record.status], reset)
onBeforeUnmount(reset)
async function query() {
  if (pending.value || !supported.value) return false
  const current = new AbortController()
  controller = current
  pending.value = 'sync'
  emit('lock', true)
  error.value = ''
  try {
    const detail = await syncPurchase(props.orderId, props.record.id, current.signal)
    if (controller !== current) return
    emit('updated', detail)
    return true
  } catch (exc) {
    if (controller === current) error.value = exc instanceof Error ? exc.message : '1688 查询失败，请稍后重试'
    return false
  } finally {
    if (controller === current) { controller = null; pending.value = ''; emit('lock', false) }
  }
}
defineExpose({ refresh: query })
</script>

<template>
  <section v-if="supported || result" class="purchase-tracking" aria-label="1688 采购查询" :aria-busy="Boolean(pending)">
    <div v-if="supported" class="order-actions">
      <button class="order-link" :disabled="Boolean(pending)" @click="query">{{ pending ? '正在刷新采购进度…' : '刷新采购进度' }}</button>
    </div>
    <p v-if="error || record.progress?.error" class="query-error" role="alert">本次刷新失败：{{ error || record.progress?.error }}<span v-if="result">；下方保留最近获取的结果。</span></p>
    <p v-if="record.progress?.message" class="order-muted mt-2" role="status">{{ record.progress.message }}</p>
    <div v-if="result?.order" class="query-result" aria-live="polite">
      <p>1688 订单：<strong>{{ result.order.status_label }}</strong></p>
      <p class="order-muted">更新时间：{{ dateTime(result.checked_at) }}</p>
      <div v-for="(product, index) in result.products" :key="product.item_id || index" class="purchase-product mt-2">
        <a v-if="product.image_url" :href="product.image_url" target="_blank" rel="noopener noreferrer" aria-label="查看1688采购商品原图"><OrderThumbnail :src="product.image_url" :title="product.name || '采购商品'" large /></a>
        <p>{{ product.name }} · {{ product.specification }} · {{ product.quantity ?? '数量待确认' }} 件</p>
      </div>
    </div>
    <div v-if="result" class="query-result" aria-live="polite">
      <p class="font-semibold">采购订单物流（整单包裹）</p>
      <p v-if="result.logistics !== null" class="order-muted">物流更新时间：{{ dateTime(result.logistics_checked_at ?? result.checked_at) }}</p>
      <p v-if="result.logistics_warning" role="status" class="query-error">{{ result.logistics_warning }}</p>
      <p v-if="result.logistics === null">本次未获取到物流信息，可稍后刷新。</p>
      <p v-else-if="!result.logistics.length">1688 暂未提供物流信息。</p>
      <p v-if="result.logistics_warning && result.logistics !== null" class="order-muted">以下为最近获取的物流结果，请留意更新时间。</p>
      <article v-for="(parcel, index) in result.logistics" :key="`${parcel.logistics_id}-${index}`" class="parcel">
        <p><strong>{{ parcel.company || '承运商未提供' }}</strong> · {{ parcel.status_label }}</p>
        <p>运单号：<span class="tracking-number">{{ parcel.tracking_number || '未提供' }}</span></p>
        <p v-if="parcel.steps.length" class="order-muted mt-2">{{ parcel.steps[0]?.time }} · {{ parcel.steps[0]?.description }}</p>
        <details v-if="parcel.steps.length">
          <summary>物流轨迹（{{ parcel.steps.length }} 条）</summary>
          <ol>
            <li v-for="(step, stepIndex) in parcel.steps" :key="stepIndex"><time>{{ step.time }}</time><span>{{ step.description }}</span></li>
          </ol>
        </details>
        <p v-else class="order-muted">暂无可展示的物流轨迹。</p>
      </article>
    </div>
  </section>
</template>

<style scoped>
.purchase-tracking { margin-top: 12px; padding-top: 12px; border-top: 1px dashed var(--order-border); font-size: 14px; }
.purchase-product { display: flex; align-items: center; gap: 12px; }
.purchase-product > div { min-width: 0; overflow-wrap: anywhere; }
.purchase-product > a { flex-shrink: 0; }
.query-result, .query-error { margin-top: 8px; }
.query-error { color: var(--color-danger, #dc2626); overflow-wrap: anywhere; }
.parcel { margin-top: 10px; padding: 10px; border: 1px solid var(--order-border); border-radius: 6px; overflow-wrap: anywhere; }
.tracking-number { user-select: all; }
summary { cursor: pointer; margin-top: 8px; }
ol { max-height: 260px; overflow: auto; margin-top: 8px; padding-left: 18px; }
li { margin-bottom: 10px; }
time { display: block; color: var(--order-muted); margin-bottom: 3px; }
</style>
