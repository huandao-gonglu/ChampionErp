<script setup lang="ts">
import type { Marketplace, PricingInput, PricingResult } from '@/types/workflow'
import { useCurrency } from '@/composables/useCurrency'

const props = defineProps<{
  input: PricingInput
  result: PricingResult | null
}>()

const emit = defineEmits<{
  calculate: []
}>()

const { formatMoney, formatPercent } = useCurrency()
const platformOptions: Array<{ key: Marketplace; label: string; site: string }> = [
  { key: 'mercadolibre', label: 'Mercado Libre', site: 'MLM' },
  { key: 'wildberries', label: 'Wildberries', site: 'RU' },
  { key: 'ozon', label: 'Ozon', site: 'RU' },
]
</script>

<template>
  <section class="card">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="card-title">核价</h2>
        <p class="muted mt-1">基于测试成本、物流和佣金计算建议售价。</p>
      </div>
      <button class="btn btn-primary" @click="emit('calculate')">计算价格</button>
    </div>

    <div class="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <label class="block">
        <span class="text-xs font-semibold text-slate-500">平台</span>
        <select v-model="props.input.platform" class="input mt-1">
          <option v-for="platform in platformOptions" :key="platform.key" :value="platform.key">{{ platform.label }}</option>
        </select>
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-slate-500">站点</span>
        <input v-model="props.input.site" class="input mt-1" placeholder="MLM / RU" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-slate-500">采购成本 CNY</span>
        <input v-model.number="props.input.purchaseCostCny" class="input mt-1" type="number" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-slate-500">重量 kg</span>
        <input v-model.number="props.input.weightKg" class="input mt-1" type="number" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-slate-500">佣金 %</span>
        <input v-model.number="props.input.commissionPercent" class="input mt-1" type="number" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-slate-500">目标利润 %</span>
        <input v-model.number="props.input.targetMarginPercent" class="input mt-1" type="number" />
      </label>
    </div>

    <div class="mt-5 grid gap-3 md:grid-cols-3">
      <div class="rounded-2xl bg-brand-50 p-4 ring-1 ring-brand-100">
        <p class="text-xs font-semibold text-brand-700">建议美客多售价</p>
        <p class="mt-2 text-2xl font-bold text-brand-700">{{ props.result ? formatMoney(props.result.suggestedPriceMxn, 'MXN') : '-' }}</p>
      </div>
      <div class="rounded-2xl bg-slate-50 p-4 ring-1 ring-slate-200">
        <p class="text-xs font-semibold text-slate-500">利润</p>
        <p class="mt-2 text-2xl font-bold text-slate-950">{{ props.result ? formatMoney(props.result.profitCny, 'CNY') : '-' }}</p>
      </div>
      <div class="rounded-2xl bg-slate-50 p-4 ring-1 ring-slate-200">
        <p class="text-xs font-semibold text-slate-500">利润率</p>
        <p class="mt-2 text-2xl font-bold text-slate-950">{{ props.result ? formatPercent(props.result.marginPercent) : '-' }}</p>
      </div>
    </div>
  </section>
</template>
