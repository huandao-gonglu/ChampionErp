<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import WorkspaceHint from '@/components/shared/WorkspaceHint.vue'
import type { PricingInput, PricingTargetInput } from '@/types/workflow'
import { marketName, pricingCommonErrors, pricingTargetErrors, shippingInputCny } from '@/utils/pricingWorkspace'
import { selectedPricingTargets } from '@/types/pricing'
import { fetchYandexWarehouses, type YandexPricingOption } from '@/api/yandexWarehouses'

const props = withDefaults(defineProps<{ input: PricingInput; disabled?: boolean; usdCnyRate?: number }>(), { usdCnyRate: 0 })
const emit = defineEmits<{ shippingAmount: [targetKey: string, amount: number | ''] }>()
const targets = computed(() => selectedPricingTargets(props.input))
const activeMarket = ref(props.input.targets[0]?.targetKey || '')
const target = computed(() => targets.value.find(target => target.targetKey === activeMarket.value) || targets.value[0])
const shippingOptions = ref<YandexPricingOption[]>([])
const shippingLoading = ref(false)
const shippingError = ref('')
function optionKey(option: YandexPricingOption) { return JSON.stringify([option.publish_warehouse_id, option.route_id]) }
const shippingValue = computed(() => target.value?.shippingSelection
  ? JSON.stringify([target.value.shippingSelection.warehouse_id, target.value.shippingSelection.route_id])
  : target.value?.shippingQuoteMode || 'auto')
watch(() => targets.value, items => { for (const item of items) if (item.platform === 'ozon') { item.shippingQuoteMode = 'auto'; item.shippingSelection = null } }, { immediate: true })
const missingSelection = computed(() => !!target.value?.shippingSelection && !shippingOptions.value.some(option => optionKey(option) === shippingValue.value))
async function loadShippingOptions() {
  if (shippingLoading.value) return
  shippingLoading.value = true
  shippingError.value = ''
  try {
    // 只读取已同步的仓库配置；物流和佣金计算仍由用户手动触发。
    const settings = await fetchYandexWarehouses()
    shippingOptions.value = settings.pricing_options || []
    shippingError.value = settings.error
  } catch (error) {
    shippingError.value = error instanceof Error ? error.message : '读取可用物流方式失败'
  } finally { shippingLoading.value = false }
}
watch(() => targets.value.some(item => item.platform === 'yandex'), enabled => { if (enabled) void loadShippingOptions() }, { immediate: true })
function selectShipping(event: Event) {
  if (!target.value) return
  const value = (event.target as HTMLSelectElement).value
  if (value === 'auto' || value === 'manual') {
    target.value.shippingQuoteMode = value
    target.value.shippingSelection = null
    return
  }
  const option = shippingOptions.value.find(item => optionKey(item) === value)
  if (option) {
    target.value.shippingQuoteMode = 'auto'
    target.value.shippingSelection = { warehouse_id: option.publish_warehouse_id, route_id: option.route_id }
  }
}
const fixedFields = [
  { key: 'domesticFreightCny', label: '国内物流' },
  { key: 'packagingCostCny', label: '包装耗材' },
  { key: 'otherCostCny', label: '其他成本' },
] as const
const modes = [{ key: 'margin', label: '目标利润率' }, { key: 'markup', label: '成本加价' }, { key: 'manual', label: '手动' }] as const
watch(targets, targets => { if (!targets.some(item => item.targetKey === activeMarket.value)) activeMarket.value = targets[0]?.targetKey || '' })
function updatePrice(target: PricingTargetInput, event: Event) {
  target.manualPrice = { amount: (event.target as HTMLInputElement).value, currency: target.listingCurrency }
}
</script>

<template>
  <fieldset :disabled="disabled" class="pricing-fees-fields">
    <section class="pricing-fee-section">
      <div class="pricing-section-heading"><h4 class="inline-flex items-center gap-1">每件固定成本<WorkspaceHint label="每件固定成本说明" content="每件商品的固定费用，请确认已计入国内物流和包装。" /></h4><span class="pricing-subtle">CNY</span></div>
      <label v-for="field in fixedFields" :key="field.key" class="pricing-field-row">
        <span>{{ field.label }}</span>
        <span class="pricing-affix-input"><span>¥</span><input v-model.number="input[field.key]" :aria-label="`${field.label}（CNY）`" type="number" min="0" step="0.01" /></span>
      </label>
    </section>
    <section class="pricing-fee-section" data-testid="pricing-market-defaults">
      <h4 class="inline-flex items-center gap-1">各市场费用<WorkspaceHint label="各市场费用说明" content="官方佣金按类目和售价查询，结果见核价明细。" /></h4>
      <div class="pricing-segments mt-3" aria-label="市场费用">
        <button v-for="item in targets" :key="item.targetKey" type="button" :class="{ active: target?.targetKey === item.targetKey }" :aria-pressed="target?.targetKey === item.targetKey" @click="activeMarket = item.targetKey">{{ marketName(item) }}</button>
      </div>
      <template v-if="target">
        <label class="pricing-field-row"><span>支付 / 结算费</span><span class="pricing-affix-input pricing-affix-end"><input v-model.number="target.paymentFeePercent" :aria-label="`${marketName(target)} 支付 / 结算费 %`" type="number" min="0" max="99.99" step="0.01" /><span>%</span></span></label>
        <label class="pricing-field-row"><span>其他平台费</span><span class="pricing-affix-input pricing-affix-end"><input v-model.number="target.otherFeePercent" :aria-label="`${marketName(target)} 其他平台费 %`" type="number" min="0" max="99.99" step="0.01" /><span>%</span></span></label>
      </template>
      <p v-else class="pricing-subtle mt-3 text-xs">请先选择核价市场。</p>
    </section>
    <section v-if="target" class="pricing-fee-section">
      <h4 class="inline-flex items-center gap-1">定价方式<WorkspaceHint label="定价方式说明" :content="target.pricingMode === 'margin' ? '利润率 = 利润 ÷ 售价，利润折算为人民币。' : target.pricingMode === 'markup' ? '加价率 = 利润 ÷ 成本，100% 表示利润等于成本。' : `按发布币种 ${target.listingCurrency || '（核价后确认）'} 填写，单个 SKU 可覆盖。`" /></h4>
      <div class="pricing-segments mt-3" aria-label="定价方式">
        <button v-for="mode in modes" :key="mode.key" type="button" :class="{ active: target.pricingMode === mode.key }" :aria-pressed="target.pricingMode === mode.key" @click="target.pricingMode = mode.key">{{ mode.label }}</button>
      </div>
      <label v-if="target.pricingMode === 'margin'" class="pricing-field-row"><span>目标利润率</span><span class="pricing-affix-input pricing-affix-end"><input v-model.number="target.targetMarginPercent" aria-label="目标利润率 %" type="number" min="0" max="99.99" step="0.01" /><span>%</span></span></label>
      <label v-else-if="target.pricingMode === 'markup'" class="pricing-field-row"><span>成本加价率</span><span class="pricing-affix-input pricing-affix-end"><input v-model.number="target.markupPercent" aria-label="成本加价率 %" type="number" min="0" step="0.01" /><span>%</span></span></label>
      <label v-else class="pricing-field-row"><span>共用售价</span><span class="pricing-affix-input pricing-affix-end"><input :value="target.manualPrice?.amount || ''" aria-label="共用手动售价" type="number" min="0" step="0.01" @input="updatePrice(target, $event)" /><span>{{ target.listingCurrency || '—' }}</span></span></label>
    </section>
    <section v-if="target" class="pricing-fee-section">
      <div class="pricing-section-heading"><h4 class="inline-flex items-center gap-1">国际运费<WorkspaceHint label="国际运费说明" :content="target.shippingQuoteMode === 'auto' ? '按 SKU 包装和平台物流配置试算，折合人民币展示，原币报价见明细。' + (target.platform === 'yandex' ? '物流选项来自已启用仓库，计算时校验是否适合当前 SKU。' : '') : '共用运费作为默认值，单个 SKU 可在明细中覆盖。'" /></h4><button v-if="target.platform === 'yandex'" type="button" class="pricing-text-button" :disabled="shippingLoading" @click="loadShippingOptions">重新读取</button></div>
      <label class="mt-3 block"><span class="sr-only">国际运费报价方式</span><select :value="shippingValue" aria-label="国际运费报价方式" class="pricing-input w-full" @change="selectShipping"><option value="auto">{{ target.platform === 'ozon' ? 'OGL 官方物流' : '在可用物流方案中比价' }}</option><template v-if="target.platform === 'yandex'"><option v-for="option in shippingOptions" :key="optionKey(option)" :value="optionKey(option)">{{ option.publish_warehouse_name }} · {{ option.route_name }}</option><option v-if="missingSelection" :value="shippingValue" disabled>{{ shippingLoading ? '正在读取所选物流方式…' : '原物流方式不可用，请重新选择' }}</option></template><option v-else-if="target.platform !== 'ozon'" value="manual">共用手动运费</option></select></label>
      <p v-if="target.platform === 'ozon'" class="pricing-subtle mt-2 text-xs">按单件单包预估，使用包装后的重量和尺寸。电子服务费单独计入，请勿重复填写。国内快递、代收打包及送仓费用请计入固定成本。</p>
      <p v-if="target.platform === 'yandex' && (shippingLoading || shippingError || !shippingOptions.length)" class="pricing-subtle mt-2 text-xs" role="status">{{ shippingLoading ? '正在读取可用物流方式…' : shippingError || '暂无物流方式，请在系统设置中同步并启用发布仓库。' }}</p>
      <label v-if="target.shippingQuoteMode === 'manual'" class="pricing-field-row"><span>每件运费</span><span class="pricing-affix-input pricing-affix-end"><input :value="shippingInputCny(target.shippingAmount, target.shippingCurrency, usdCnyRate)" :disabled="target.shippingCurrency !== 'CNY' && !(usdCnyRate > 0)" aria-label="共用每件国际运费（人民币）" type="number" min="0" step="0.01" @input="emit('shippingAmount', target.targetKey, ($event.target as HTMLInputElement).value === '' ? '' : Number(($event.target as HTMLInputElement).value))" /><span>元</span></span></label>
      <p v-if="target.shippingQuoteMode === 'manual' && target.shippingCurrency !== 'CNY' && !(usdCnyRate > 0)" class="pricing-subtle mt-2 text-xs">已有外币运费需要 USD/CNY 汇率，请等待加载或填写汇率。</p>
    </section>
    <section v-if="target" class="pricing-fee-section" aria-label="货损预估">
      <h4 class="inline-flex items-center gap-1">货损预估<WorkspaceHint label="货损预估说明" :content="`选填，适用于 ${marketName(target)}。退货率 = 退货件数 ÷ 发出件数。发货运费和包装按不退、佣金按全退估算；其他成本不自动计入。货损分摊至成交商品，单个 SKU 可覆盖。`" /></h4>
      <label class="pricing-field-row"><span>预计退货率</span><span class="pricing-affix-input pricing-affix-end"><input v-model.number="target.returnRatePercent" aria-label="预计退货率 %" type="number" min="0" max="99.99" step="0.01" placeholder="不计提" /><span>%</span></span></label>
      <template v-if="Number(target.returnRatePercent) > 0">
        <label class="pricing-field-row"><span class="inline-flex items-center gap-1">处理方式<WorkspaceHint label="处理方式说明" :content="target.returnDisposition === 'resell' ? '退回再售按商品完好、采购成本全部回收估算。' : '弃货计入全部采购成本损失。'" /></span><select :value="target.returnDisposition || 'discard'" aria-label="退货处理方式" class="pricing-input" @change="target.returnDisposition = ($event.target as HTMLSelectElement).value as 'discard' | 'resell'"><option value="discard">弃货</option><option value="resell">退回再售</option></select></label>
        <label class="pricing-field-row"><span class="inline-flex items-center gap-1">每次退货额外费用<WorkspaceHint label="每次退货额外费用说明" content="填写退回运费、处理费及其他不退费用。" /></span><span class="pricing-affix-input pricing-affix-end"><input v-model.number="target.returnHandlingCostCny" aria-label="每次退货额外费用（人民币）" type="number" min="0" step="0.01" placeholder="0" /><span>元/件</span></span></label>
      </template>
    </section>
    <p v-if="pricingCommonErrors(input).length || (target && pricingTargetErrors(target).length)" class="pricing-inline-warning" role="alert">{{ [...pricingCommonErrors(input), ...(target ? pricingTargetErrors(target) : [])].join('；') }}</p>
  </fieldset>
</template>
