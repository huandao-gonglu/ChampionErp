<script setup lang="ts">
import { computed } from 'vue'
import type { DraftIndexItem, DraftProductContext, Marketplace, MarketplaceOption, PricingInput, PricingTargetInput, PricingTargetResult, PricingResult } from '@/types/workflow'
import { useCurrency } from '@/composables/useCurrency'

const props = defineProps<{
  input: PricingInput
  result: PricingResult | null
  draftItems: DraftIndexItem[]
  draftId: string
  draftTitle: string
  productContext: DraftProductContext
  draftPrice: string
  platformOptions: MarketplaceOption[]
  loading: boolean
}>()

const emit = defineEmits<{
  calculate: []
  selectDraft: [draftId: string]
  refreshDrafts: []
  editDraft: []
}>()

const { formatMoney, formatPercent } = useCurrency()
const resultByKey = computed(() => new Map((props.result?.results || []).map((item) => [item.targetKey, item])))

function platformOption(platform: Marketplace) {
  return props.platformOptions.find((option) => option.key === platform)
}

function siteLabel(target: PricingTargetInput) {
  const option = platformOption(target.platform)
  const site = option?.sites.find((item) => item.code.toLowerCase() === target.site.toLowerCase())
  return `${option?.label || target.platform} · ${site?.label || target.site}`
}

function siteMeta(target: PricingTargetInput) {
  const option = platformOption(target.platform)
  const site = option?.sites.find((item) => item.code.toLowerCase() === target.site.toLowerCase())
  return [target.site, site?.language, target.currency].filter(Boolean).join(' / ')
}

function resultFor(target: PricingTargetInput): PricingTargetResult | undefined {
  return resultByKey.value.get(target.targetKey)
}

function targetPrice(target: PricingTargetInput) {
  const result = resultFor(target)
  if (!result) return target.appliedPrice > 0 ? formatMoney(target.appliedPrice, target.currency) : '-'
  return formatMoney(result.appliedPrice || result.suggestedPrice, result.currency)
}

function suggestedPrice(target: PricingTargetInput) {
  const result = resultFor(target)
  return result ? formatMoney(result.suggestedPrice, result.currency) : '-'
}

function profitText(target: PricingTargetInput) {
  const result = resultFor(target)
  return result ? formatMoney(result.profitCny, 'CNY') : '-'
}

function marginText(target: PricingTargetInput) {
  const result = resultFor(target)
  return result ? formatPercent(result.marginPercent) : '-'
}

function targetStatus(target: PricingTargetInput) {
  const result = resultFor(target)
  if (!result) return target.appliedPrice > 0 ? '已填价' : '待核价'
  if (result.errors.length) return '需处理'
  return result.isLoss ? '亏损' : '已应用'
}

function targetStatusClass(target: PricingTargetInput) {
  const status = targetStatus(target)
  if (status === '已应用') return 'badge-success'
  if (status === '亏损' || status === '需处理') return 'badge-danger'
  return 'badge-muted'
}

function resultErrors(target: PricingTargetInput) {
  const result = resultFor(target)
  if (!result?.errors.length) return ''
  return result.errors.map((item) => typeof item === 'string' ? item : String(item.message || item.field || '')).filter(Boolean).join('；')
}

function exchangeRateText() {
  if (!props.result) return ''
  const parts = [
    `USD/CNY ${props.result.usdCnyRate || '-'}`,
    `MXN/USD ${props.result.mxnUsdRate || '-'}`,
  ]
  if (props.result.rubCnyRate) parts.push(`RUB/CNY ${props.result.rubCnyRate}`)
  return parts.join('，')
}
</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="min-w-0">
        <h2 class="card-title">草稿核价</h2>
        <p class="muted mt-1 truncate">{{ props.draftTitle || '从草稿箱选择草稿后核价。' }}</p>
      </div>
      <button class="btn btn-primary" :disabled="props.loading || !props.draftId || !props.input.targets.length" @click="emit('calculate')">计算并应用</button>
    </div>

    <div class="mt-5 grid gap-3 border-b border-accent-200 pb-5 dark:border-dark-700 lg:grid-cols-[minmax(0,1fr)_160px_160px_auto]">
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">草稿</span>
        <select
          class="input mt-1"
          :disabled="props.loading || !props.draftItems.length"
          :value="props.draftId"
          @change="emit('selectDraft', ($event.target as HTMLSelectElement).value)"
        >
          <option value="">选择草稿</option>
          <option v-for="item in props.draftItems" :key="item.draftId" :value="item.draftId">
            {{ item.title || item.productTitle || item.draftId }}
          </option>
        </select>
      </label>
      <div>
        <p class="text-xs font-semibold text-accent-500 dark:text-accent-300">来源商品</p>
        <p class="mt-3 truncate text-sm font-semibold text-accent-950 dark:text-white" :title="props.productContext.sourceTitle || props.productContext.sourceProductId">
          {{ props.productContext.sourceTitle || props.productContext.sourceProductId || '-' }}
        </p>
      </div>
      <div>
        <p class="text-xs font-semibold text-accent-500 dark:text-accent-300">当前主售价</p>
        <p class="mt-3 text-sm font-semibold text-accent-950 dark:text-white">{{ props.draftPrice || '-' }}</p>
      </div>
      <div class="flex items-end gap-2">
        <button class="btn btn-outline py-2" :disabled="props.loading" @click="emit('refreshDrafts')">刷新</button>
        <button class="btn btn-secondary py-2" :disabled="props.loading || !props.draftId" @click="emit('editDraft')">编辑</button>
      </div>
    </div>

    <div class="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">显示货币</span>
        <select v-model="props.input.displayCurrencyMode" class="input mt-1">
          <option value="platform">默认（按市场货币）</option>
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
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">采购成本 CNY</span>
        <input v-model.number="props.input.purchaseCostCny" class="input mt-1" type="number" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">国内运费 CNY</span>
        <input v-model.number="props.input.domesticFreightCny" class="input mt-1" type="number" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">重量 kg</span>
        <input v-model.number="props.input.weightKg" class="input mt-1" type="number" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">长 cm</span>
        <input v-model.number="props.input.lengthCm" class="input mt-1" type="number" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">宽 cm</span>
        <input v-model.number="props.input.widthCm" class="input mt-1" type="number" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">高 cm</span>
        <input v-model.number="props.input.heightCm" class="input mt-1" type="number" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">USD/CNY</span>
        <input v-model.number="props.input.usdCnyRate" class="input mt-1" type="number" step="0.0001" :disabled="props.input.exchangeRateMode === 'live'" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">MXN/USD</span>
        <input v-model.number="props.input.mxnUsdRate" class="input mt-1" type="number" step="0.0001" :disabled="props.input.exchangeRateMode === 'live'" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-accent-500 dark:text-accent-300">RUB/CNY</span>
        <input v-model.number="props.input.rubCnyRate" class="input mt-1" type="number" step="0.0001" :disabled="props.input.exchangeRateMode === 'live'" />
      </label>
    </div>

    <div class="mt-3 rounded-lg bg-accent-50 p-3 text-xs leading-relaxed text-accent-600 ring-1 ring-accent-200 dark:bg-dark-800 dark:text-accent-300 dark:ring-dark-700">
      <span v-if="props.result">
        本次汇率：{{ exchangeRateText() }}
        <span v-if="props.result.exchangeRateMode === 'live'" class="break-all"> · 来源：{{ props.result.exchangeRateSource || '-' }}{{ props.result.exchangeRateCached ? ' · 缓存' : '' }}</span>
      </span>
      <span v-else-if="props.input.exchangeRateMode === 'live'">计算时会从授权页配置的汇率 API 获取实时汇率。</span>
      <span v-else>手动汇率模式会使用上方输入的汇率。</span>
    </div>

    <div class="mt-5 overflow-x-auto rounded-lg border border-accent-200 dark:border-dark-700">
      <table class="w-full min-w-[1040px] table-fixed text-left text-sm">
        <colgroup>
          <col class="w-[210px]" />
          <col class="w-[92px]" />
          <col class="w-[96px]" />
          <col class="w-[96px]" />
          <col class="w-[112px]" />
          <col class="w-[112px]" />
          <col class="w-[130px]" />
          <col class="w-[130px]" />
          <col class="w-[110px]" />
          <col class="w-[92px]" />
        </colgroup>
        <thead class="border-b border-accent-200 bg-accent-50 text-xs text-accent-500 dark:border-dark-700 dark:bg-dark-950/70 dark:text-accent-400">
          <tr>
            <th class="p-3">目标市场</th>
            <th class="p-3">佣金 %</th>
            <th class="p-3">支付 %</th>
            <th class="p-3">利润 %</th>
            <th class="p-3">运费 USD</th>
            <th class="p-3">运费 CNY</th>
            <th class="p-3">建议售价</th>
            <th class="p-3">应用售价</th>
            <th class="p-3">利润</th>
            <th class="p-3">状态</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-accent-100 dark:divide-dark-800">
          <tr v-for="target in props.input.targets" :key="target.targetKey" class="align-top">
            <td class="p-3">
              <p class="truncate font-semibold text-accent-950 dark:text-white" :title="siteLabel(target)">{{ siteLabel(target) }}</p>
              <p class="mt-1 truncate text-xs text-accent-500 dark:text-accent-400">{{ siteMeta(target) }}</p>
              <p v-if="resultErrors(target)" class="mt-1 text-xs font-semibold text-rose-600 dark:text-rose-200">{{ resultErrors(target) }}</p>
            </td>
            <td class="p-3"><input v-model.number="target.commissionPercent" class="input h-9 px-2 py-1 text-xs" type="number" /></td>
            <td class="p-3"><input v-model.number="target.paymentFeePercent" class="input h-9 px-2 py-1 text-xs" type="number" /></td>
            <td class="p-3"><input v-model.number="target.targetMarginPercent" class="input h-9 px-2 py-1 text-xs" type="number" /></td>
            <td class="p-3"><input v-model.number="target.shippingCostUsd" class="input h-9 px-2 py-1 text-xs" type="number" /></td>
            <td class="p-3"><input v-model.number="target.shippingCostCny" class="input h-9 px-2 py-1 text-xs" type="number" /></td>
            <td class="p-3 font-semibold text-accent-950 dark:text-white">{{ suggestedPrice(target) }}</td>
            <td class="p-3">
              <input v-model.number="target.appliedPrice" class="input h-9 px-2 py-1 text-xs" type="number" :placeholder="targetPrice(target)" />
              <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">{{ marginText(target) }}</p>
            </td>
            <td class="p-3 font-semibold text-accent-950 dark:text-white">{{ profitText(target) }}</td>
            <td class="p-3"><span :class="targetStatusClass(target)">{{ targetStatus(target) }}</span></td>
          </tr>
          <tr v-if="!props.input.targets.length">
            <td class="p-6 text-center text-accent-500 dark:text-accent-300" colspan="10">当前草稿没有目标市场。</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
