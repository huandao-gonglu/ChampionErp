<script setup lang="ts">
import type { Marketplace, PricingInput, PricingResult, ProductIndexItem } from '@/types/workflow'
import { useCurrency } from '@/composables/useCurrency'

const props = defineProps<{
  input: PricingInput
  result: PricingResult | null
  productItems: ProductIndexItem[]
  productId: string
  productTitle: string
  sourcePlatform: string
  draftPrice: string
  loading: boolean
}>()

const emit = defineEmits<{
  calculate: []
  selectProduct: [productId: string]
  refreshProducts: []
  editProduct: []
}>()

const { formatMoney, formatPercent } = useCurrency()
const platformOptions: Array<{ key: Marketplace; label: string; site: string }> = [
  { key: 'mercadolibre', label: 'Mercado Libre', site: 'MLM' },
  { key: 'wildberries', label: 'Wildberries', site: 'RU' },
  { key: 'ozon', label: 'Ozon', site: 'RU' },
]

const platformCurrencyByMarketplace: Record<Marketplace, 'MXN' | 'RUB'> = {
  mercadolibre: 'MXN',
  wildberries: 'RUB',
  ozon: 'RUB',
}

const platformLabelByMarketplace: Record<Marketplace, string> = {
  mercadolibre: '美客多',
  wildberries: 'Wildberries',
  ozon: 'Ozon',
}

function currentPlatformCurrency() {
  return platformCurrencyByMarketplace[props.input.platform] || 'MXN'
}

function platformSuggestedPrice(result: PricingResult) {
  if ((props.input.platform === 'wildberries' || props.input.platform === 'ozon') && result.wbPriceRub > 0) {
    return result.wbPriceRub
  }
  return result.suggestedPriceMxn
}

function platformSuggestedPriceCny(result: PricingResult) {
  const currency = currentPlatformCurrency()
  if (currency === 'RUB' && result.rubCnyRate > 0) {
    return Math.round((platformSuggestedPrice(result) / result.rubCnyRate) * 100) / 100
  }
  return result.suggestedPriceCny
}

function resultPrice(result: PricingResult) {
  if (props.input.displayCurrencyMode === 'cny') return formatMoney(platformSuggestedPriceCny(result), 'CNY')
  return formatMoney(platformSuggestedPrice(result), currentPlatformCurrency())
}

function resultPriceHint(result: PricingResult) {
  const platformCurrency = currentPlatformCurrency()
  const platformPrice = formatMoney(platformSuggestedPrice(result), platformCurrency)
  const cnyPrice = formatMoney(platformSuggestedPriceCny(result), 'CNY')
  return props.input.displayCurrencyMode === 'cny'
    ? `${platformPrice} / ${formatMoney(result.suggestedPriceUsd, 'USD')}`
    : `${formatMoney(result.suggestedPriceUsd, 'USD')} / ${cnyPrice}`
}

function displayMoney(cnyValue: number, platformValue: number, platformCurrency: 'USD' | 'MXN' | 'RUB') {
  if (props.input.displayCurrencyMode === 'cny') return formatMoney(cnyValue, 'CNY')
  return formatMoney(platformValue, platformCurrency)
}

function priceTitle() {
  return props.input.displayCurrencyMode === 'cny'
    ? `${platformLabelByMarketplace[props.input.platform] || '平台'}建议售价（人民币）`
    : `${platformLabelByMarketplace[props.input.platform] || '平台'}建议售价`
}

function syncSiteForPlatform() {
  const selected = platformOptions.find((platform) => platform.key === props.input.platform)
  if (selected) props.input.site = selected.site
}
</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="card-title">核价</h2>
        <p class="muted mt-1">{{ props.productTitle || '先选择商品，再基于成本、物流和佣金计算建议售价。' }}</p>
      </div>
      <button class="btn btn-primary" @click="emit('calculate')">计算价格</button>
    </div>

    <div class="mt-5 grid gap-3 border-b border-accent-200 pb-5 dark:border-dark-700 lg:grid-cols-[minmax(0,1fr)_120px_150px_auto]">
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">商品</span>
        <select
          class="input mt-1"
          :disabled="props.loading || !props.productItems.length"
          :value="props.productId"
          @change="emit('selectProduct', ($event.target as HTMLSelectElement).value)"
        >
          <option value="">选择商品</option>
          <option v-for="item in props.productItems" :key="item.productId" :value="item.productId">
            {{ item.title || item.productId }}
          </option>
        </select>
      </label>
      <div>
        <p class="text-xs font-semibold text-accent-500 dark:text-accent-300">来源平台</p>
        <p class="mt-3 text-sm font-semibold text-accent-950 dark:text-white">{{ props.sourcePlatform || '-' }}</p>
      </div>
      <div>
        <p class="text-xs font-semibold text-accent-500 dark:text-accent-300">当前价格</p>
        <p class="mt-3 text-sm font-semibold text-accent-950 dark:text-white">{{ props.draftPrice || '-' }}</p>
      </div>
      <div class="flex items-end gap-2">
        <button class="btn btn-outline py-2" :disabled="props.loading" @click="emit('refreshProducts')">刷新</button>
        <button class="btn btn-secondary py-2" :disabled="props.loading || !props.productId" @click="emit('editProduct')">编辑</button>
      </div>
    </div>

    <div class="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">显示货币</span>
        <select v-model="props.input.displayCurrencyMode" class="input mt-1">
          <option value="platform">默认（按平台货币）</option>
          <option value="cny">人民币</option>
        </select>
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">汇率来源</span>
        <select v-model="props.input.exchangeRateMode" class="input mt-1">
          <option value="live">实时 API</option>
          <option value="manual">手动输入</option>
        </select>
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">平台</span>
        <select v-model="props.input.platform" class="input mt-1" @change="syncSiteForPlatform">
          <option v-for="platform in platformOptions" :key="platform.key" :value="platform.key">{{ platform.label }}</option>
        </select>
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">站点</span>
        <input v-model="props.input.site" class="input mt-1" placeholder="MLM / RU" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">采购成本 CNY</span>
        <input v-model.number="props.input.purchaseCostCny" class="input mt-1" type="number" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">重量 kg</span>
        <input v-model.number="props.input.weightKg" class="input mt-1" type="number" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">佣金 %</span>
        <input v-model.number="props.input.commissionPercent" class="input mt-1" type="number" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">目标利润 %</span>
        <input v-model.number="props.input.targetMarginPercent" class="input mt-1" type="number" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">USD/CNY</span>
        <input v-model.number="props.input.usdCnyRate" class="input mt-1" type="number" step="0.0001" :disabled="props.input.exchangeRateMode === 'live'" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">MXN/USD</span>
        <input v-model.number="props.input.mxnUsdRate" class="input mt-1" type="number" step="0.0001" :disabled="props.input.exchangeRateMode === 'live'" />
      </label>
    </div>

    <div class="mt-3 rounded-xl bg-accent-50 p-3 text-xs leading-relaxed text-accent-600 ring-1 ring-accent-200 dark:bg-dark-800 dark:text-accent-300 dark:ring-dark-700">
      <span v-if="props.result">
        本次汇率：USD/CNY {{ props.result.usdCnyRate || '-' }}，MXN/USD {{ props.result.mxnUsdRate || '-' }}
        <span v-if="currentPlatformCurrency() === 'RUB'">，RUB/CNY {{ props.result.rubCnyRate || '-' }}</span>
        <span v-if="props.result.exchangeRateMode === 'live'" class="break-all"> · 来源：{{ props.result.exchangeRateSource || '-' }}{{ props.result.exchangeRateCached ? ' · 缓存' : '' }}</span>
      </span>
      <span v-else-if="props.input.exchangeRateMode === 'live'">计算时会从平台授权页配置的汇率 API 获取实时汇率；失败会直接提示，不会使用固定常量。</span>
      <span v-else>手动汇率模式会使用上方输入的 USD/CNY 和 MXN/USD。</span>
    </div>

    <div class="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      <div class="min-w-0 rounded-lg bg-brand-50 p-4 ring-1 ring-brand-100 dark:bg-primary-500/10 dark:ring-primary-500/30">
        <p class="text-xs font-semibold text-brand-700 dark:text-primary-200">{{ priceTitle() }}</p>
        <p class="mt-2 break-words text-xl font-bold leading-tight text-brand-700 dark:text-primary-100 2xl:text-2xl">{{ props.result ? resultPrice(props.result) : '-' }}</p>
        <p v-if="props.result" class="mt-2 break-words text-xs font-semibold leading-snug text-brand-700/80 dark:text-primary-200/80">{{ resultPriceHint(props.result) }}</p>
      </div>
      <div class="min-w-0 rounded-lg bg-accent-50 p-4 ring-1 ring-accent-200 dark:bg-dark-950/70 dark:ring-dark-700">
        <p class="text-xs font-semibold text-accent-500 dark:text-accent-300">利润 / 总成本</p>
        <p class="mt-2 break-words text-xl font-bold leading-tight text-accent-950 dark:text-white 2xl:text-2xl">{{ props.result ? formatMoney(props.result.profitCny, 'CNY') : '-' }}</p>
        <p v-if="props.result" class="mt-2 break-words text-xs font-semibold leading-snug text-accent-500 dark:text-accent-400">
          成本 {{ formatMoney(props.result.totalCostCny, 'CNY') }}
        </p>
      </div>
      <div class="min-w-0 rounded-lg bg-accent-50 p-4 ring-1 ring-accent-200 dark:bg-dark-950/70 dark:ring-dark-700">
        <p class="text-xs font-semibold text-accent-500 dark:text-accent-300">运费</p>
        <p class="mt-2 break-words text-xl font-bold leading-tight text-accent-950 dark:text-white 2xl:text-2xl">
          {{ props.result ? displayMoney(props.result.shippingCostCny, props.result.shippingCostUsd, 'USD') : '-' }}
        </p>
        <p v-if="props.result" class="mt-2 break-words text-xs font-semibold leading-snug text-accent-500 dark:text-accent-400">
          {{ props.input.displayCurrencyMode === 'cny' ? formatMoney(props.result.shippingCostUsd, 'USD') : formatMoney(props.result.shippingCostCny, 'CNY') }}
        </p>
      </div>
      <div class="min-w-0 rounded-lg bg-accent-50 p-4 ring-1 ring-accent-200 dark:bg-dark-950/70 dark:ring-dark-700">
        <p class="text-xs font-semibold text-accent-500 dark:text-accent-300">利润率</p>
        <p class="mt-2 break-words text-xl font-bold leading-tight text-accent-950 dark:text-white 2xl:text-2xl">{{ props.result ? formatPercent(props.result.marginPercent) : '-' }}</p>
      </div>
    </div>
  </section>
</template>
