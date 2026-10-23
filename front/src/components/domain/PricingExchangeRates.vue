<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { PhArrowsClockwise, PhGlobe } from '@phosphor-icons/vue'
import { fetchPricingExchangeRates } from '@/api/pricingWorkspace'
import type { PricingInput } from '@/types/workflow'
import type { ExchangeRateKey, PricingExchangeRates } from '@/types/pricing'

const props = defineProps<{ input: PricingInput; disabled?: boolean }>()
const emit = defineEmits<{ ready: []; refreshStart: []; rates: [rates: PricingExchangeRates['rates']] }>()
const snapshot = ref<PricingExchangeRates | null>(null)
const refreshing = ref(false)
const error = ref('')
const fields: { key: ExchangeRateKey; prefix: string; unit: string; label: string }[] = [
  { key: 'usd_cny_rate', prefix: '1 USD =', unit: 'CNY', label: 'USD/CNY 汇率' },
  { key: 'rub_cny_rate', prefix: '1 CNY =', unit: 'RUB', label: 'CNY/RUB 汇率' },
  { key: 'mxn_usd_rate', prefix: '1 USD =', unit: 'MXN', label: 'USD/MXN 汇率' },
]
const updatedAt = computed(() => {
  if (!snapshot.value?.fetched_at) return '自动获取 API 参考值'
  const time = new Date(snapshot.value.fetched_at)
  return Number.isNaN(time.getTime()) ? '' : `API 更新 · ${time.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}${snapshot.value.cached ? ' · 缓存' : ''}`
})
function overridden(key: ExchangeRateKey) { return Object.prototype.hasOwnProperty.call(props.input.exchangeRateOverrides || {}, key) }
function value(key: ExchangeRateKey) { return overridden(key) ? props.input.exchangeRateOverrides?.[key] : snapshot.value?.rates[key] }
function invalid(key: ExchangeRateKey) { return overridden(key) && (!Number.isFinite(Number(value(key))) || Number(value(key)) <= 0) }
function update(key: ExchangeRateKey, event: Event) {
  const text = (event.target as HTMLInputElement).value
  props.input.exchangeRateOverrides = { ...props.input.exchangeRateOverrides, [key]: text ? Number(text) : 0 }
}
function restore(key: ExchangeRateKey) {
  const overrides = { ...props.input.exchangeRateOverrides }
  delete overrides[key]
  props.input.exchangeRateOverrides = overrides
}
async function refresh(force = true) {
  if (refreshing.value) return
  refreshing.value = true
  if (force) emit('refreshStart')
  error.value = ''
  try {
    snapshot.value = await fetchPricingExchangeRates(force)
    emit('rates', snapshot.value.rates)
    error.value = snapshot.value.error || ''
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '读取实时汇率失败' }
  finally { refreshing.value = false }
}
onMounted(async () => {
  if (props.input.exchangeRateMode === 'manual') {
    props.input.exchangeRateOverrides = { usd_cny_rate: props.input.usdCnyRate, rub_cny_rate: props.input.rubCnyRate, mxn_usd_rate: props.input.mxnUsdRate, ...props.input.exchangeRateOverrides }
    props.input.exchangeRateMode = 'live'
  }
  await refresh(false)
  emit('ready')
})
defineExpose({ refresh })
</script>

<template>
  <section class="pricing-rates pricing-card" aria-label="核价汇率" :aria-busy="refreshing">
    <div class="pricing-rates-heading">
      <h3><PhGlobe :size="18" class="text-primary-600" />实时汇率</h3>
      <p>{{ refreshing ? '正在查询汇率 API…' : updatedAt }}</p>
    </div>
    <div v-for="field in fields" :key="field.key" class="pricing-rate-field">
      <label class="pricing-rate-value">
        <span>{{ field.prefix }}</span>
        <input :value="value(field.key)" :aria-label="field.label" :aria-invalid="invalid(field.key)" :disabled="disabled" :class="{ 'pricing-input-error': invalid(field.key), 'pricing-rate-manual': overridden(field.key) }" class="pricing-input" type="number" min="0" step="0.0001" placeholder="自动填入" @input="update(field.key, $event)" />
        <span class="pricing-subtle">{{ field.unit }}</span>
      </label>
      <p v-if="invalid(field.key)" class="pricing-field-error" role="alert">请填写大于 0 的汇率</p>
      <div v-else-if="overridden(field.key)" class="pricing-rate-hint">
        <span class="text-amber-600 dark:text-amber-300">手动<span v-if="snapshot?.rates[field.key]"> · API {{ snapshot.rates[field.key].toFixed(4) }}</span></span>
        <button type="button" :disabled="disabled || !snapshot?.rates[field.key]" @click="restore(field.key)">恢复实时值</button>
      </div>
      <p v-else class="pricing-rate-hint pricing-subtle">可直接修改</p>
    </div>
    <button type="button" class="pricing-button pricing-rate-refresh" :disabled="refreshing || disabled" @click="refresh()"><PhArrowsClockwise :size="16" :class="{ 'animate-spin': refreshing }" />刷新</button>
    <p v-if="error" class="pricing-rate-error" role="alert">{{ error }}<span v-if="snapshot?.stale"> 当前显示上次获取的参考值。</span></p>
  </section>
</template>
