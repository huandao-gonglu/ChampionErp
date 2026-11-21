<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { PhArrowRight, PhInfo } from '@phosphor-icons/vue'
import ProductSkuEditor from './ProductSkuEditor.vue'
import ProductAttributesEditor from './ProductAttributesEditor.vue'
import type { Product } from '@/types/workflow'

export type ProductEditorSection = 'all' | 'basic' | 'skus' | 'attributes'

const props = withDefaults(defineProps<{
  product: Product
  loading: boolean
  focusSkuId?: string
  activeSection?: ProductEditorSection
  embedded?: boolean
}>(), {
  focusSkuId: '',
  activeSection: 'all',
  embedded: false,
})

const emit = defineEmits<{
  save: []
  assignUpc: []
  validityChange: [valid: boolean]
}>()

function listModel(getter: () => string[], setter: (value: string[]) => void) {
  return computed({
    get: () => getter().join('\n'),
    set: (value: string) => setter(value.split(/\n|,/).map((item) => item.trim()).filter(Boolean)),
  })
}

const legacyCost = computed(() => String((props.product.raw.source as { legacy_product_cost_cny?: string } | undefined)?.legacy_product_cost_cny ?? ''))
const packageIncludesText = listModel(() => props.product.packageIncludes, (value) => { props.product.packageIncludes = value })
const materialsText = listModel(() => props.product.materials, (value) => { props.product.materials = value })
const sourceAttributesValid = ref(true)
const profileAttributesValid = ref(true)
const attributesValid = computed(() => sourceAttributesValid.value && profileAttributesValid.value)
const syncHint = ref('')

const showBasic = computed(() => props.activeSection === 'all' || props.activeSection === 'basic')
const showSkus = computed(() => props.activeSection === 'all' || props.activeSection === 'skus')
const showAttributes = computed(() => props.activeSection === 'all' || props.activeSection === 'attributes')

const hasReferenceDimensions = computed(() => {
  const dims = props.product.source.dimensions
  return Boolean(
    String(dims.lengthCm || '').trim()
    || String(dims.widthCm || '').trim()
    || String(dims.heightCm || '').trim()
    || String(props.product.source.weightKg || '').trim()
  )
})

function fillBlankSkusFromReference() {
  if (props.loading || !hasReferenceDimensions.value) return
  const dims = props.product.source.dimensions
  const mapping: Array<['length_cm' | 'width_cm' | 'height_cm' | 'weight_kg', string]> = [
    ['length_cm', String(dims.lengthCm || '').trim()],
    ['width_cm', String(dims.widthCm || '').trim()],
    ['height_cm', String(dims.heightCm || '').trim()],
    ['weight_kg', String(props.product.source.weightKg || '').trim()],
  ]
  let touched = 0
  for (const sku of props.product.skuItems) {
    if (!sku.active) continue
    if (!sku.package_dimensions) sku.package_dimensions = {}
    let changed = false
    for (const [key, value] of mapping) {
      if (!value) continue
      const current = String(sku.package_dimensions?.[key] ?? '').trim()
      if (!current || Number(current) <= 0) {
        sku.package_dimensions[key] = value
        changed = true
      }
    }
    if (changed) touched++
  }
  syncHint.value = touched ? `已将来源参考尺寸同步到 ${touched} 个空白 SKU，记得点击「保存商品」。` : '所有启用 SKU 均已填写对应尺寸与重量，无需覆盖。'
}

watch(attributesValid, (valid) => {
  emit('validityChange', valid)
}, { immediate: true })

watch(() => props.product.productId, () => {
  sourceAttributesValid.value = true
  profileAttributesValid.value = true
  syncHint.value = ''
})
</script>

<template>
  <div class="space-y-4">
    <!-- 独立挂载模式下的顶部操作栏（工作台嵌入时由外层顶栏统一提供吸顶保存） -->
    <section v-if="!embedded" class="rounded-2xl border border-accent-200/90 bg-white p-4 shadow-sm dark:border-dark-700 dark:bg-dark-800/70">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="text-base font-bold text-accent-950 dark:text-white">商品编辑</h2>
          <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">维护商品原始资料、供应链字段和内部资料；平台标题、本地化商品描述和价格在草稿箱单独编辑。</p>
        </div>
        <div class="flex flex-wrap gap-2">
          <button
            type="button"
            class="btn btn-outline !min-h-9 !rounded-xl !px-3 !py-1.5 text-sm"
            :disabled="props.loading || !attributesValid || !props.product.skuItems.some(sku => sku.active && !sku.barcode)"
            @click="emit('assignUpc')"
          >
            为缺条码 SKU 分配 UPC
          </button>
          <button
            type="button"
            class="btn btn-primary !min-h-9 !rounded-xl !px-3.5 !py-1.5 text-sm"
            :disabled="props.loading || !attributesValid"
            @click="emit('save')"
          >
            保存商品
          </button>
        </div>
      </div>
    </section>

    <!-- 分区 1：基础资料 -->
    <div v-show="showBasic" class="space-y-4">
      <section class="rounded-2xl border border-accent-200/90 bg-white p-4 shadow-sm dark:border-dark-700 dark:bg-dark-800/70">
        <div class="mb-3">
          <h3 class="text-sm font-bold text-accent-950 dark:text-white">基础信息</h3>
          <p class="mt-0.5 text-xs text-accent-500 dark:text-accent-400">维护商品通用主档与来源信息；各平台标题、描述和售价在草稿箱中按语言单独编辑。</p>
        </div>

        <div class="grid gap-3 md:grid-cols-2">
          <label class="block">
            <span class="text-xs font-semibold text-accent-600 dark:text-accent-300">内部商品名</span>
            <input v-model="props.product.name" class="input mt-1 h-9 !rounded-xl text-sm" placeholder="便于团队内部识别的简称或中文名" :disabled="props.loading" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-accent-600 dark:text-accent-300">来源标题</span>
            <input v-model="props.product.source.title" class="input mt-1 h-9 !rounded-xl text-sm" placeholder="采集到的商品原标题" :disabled="props.loading" />
          </label>
        </div>

        <div class="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <label class="block">
            <span class="text-xs font-semibold text-accent-600 dark:text-accent-300">品牌</span>
            <input v-model="props.product.brand" class="input mt-1 h-9 !rounded-xl text-sm" placeholder="无品牌可留空" :disabled="props.loading" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-accent-600 dark:text-accent-300">型号（Model）</span>
            <input v-model="props.product.model" class="input mt-1 h-9 !rounded-xl text-sm" placeholder="如 M-2026" :disabled="props.loading" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-accent-600 dark:text-accent-300">品类</span>
            <input v-model="props.product.category" class="input mt-1 h-9 !rounded-xl text-sm" placeholder="通用商品品类" :disabled="props.loading" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-accent-600 dark:text-accent-300">来源平台</span>
            <input v-model="props.product.source.sourcePlatform" class="input mt-1 h-9 !rounded-xl text-sm" placeholder="如 1688" :disabled="props.loading" />
          </label>
        </div>

        <div class="mt-4 grid gap-4 xl:grid-cols-2">
          <label class="block">
            <span class="text-xs font-semibold text-accent-600 dark:text-accent-300">包装清单（每行一项）</span>
            <textarea v-model="packageIncludesText" class="input mt-1 min-h-24 !rounded-xl text-xs leading-relaxed" placeholder="例如：主机 x1&#10;充电线 x1&#10;说明书 x1" :disabled="props.loading" />
          </label>
          <label class="block">
            <span class="text-xs font-semibold text-accent-600 dark:text-accent-300">材质（每行一项）</span>
            <textarea v-model="materialsText" class="input mt-1 min-h-24 !rounded-xl text-xs leading-relaxed" placeholder="例如：ABS 塑料&#10;304 不锈钢" :disabled="props.loading" />
          </label>
        </div>
      </section>

      <!-- 采集参考尺寸（与 SKU 实际包装尺寸明确区分，提供一键同步） -->
      <section class="rounded-2xl border border-accent-200/80 bg-accent-50/70 p-4 dark:border-dark-700 dark:bg-dark-900/40">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="flex items-start gap-2">
            <PhInfo :size="16" class="mt-0.5 shrink-0 text-accent-500 dark:text-accent-400" aria-hidden="true" />
            <div>
              <h3 class="text-xs font-bold text-accent-800 dark:text-accent-100">来源参考尺寸与重量（仅作参考）</h3>
              <p class="mt-0.5 text-[11px] text-accent-500 dark:text-accent-400">
                核价与发布以「规格与 SKU」中各 SKU 的独立包装尺寸和重量为准；如所有规格一致，可一键填入空白 SKU。
              </p>
            </div>
          </div>
          <button
            v-if="props.product.skuItems.length"
            type="button"
            class="btn btn-outline !min-h-8 !rounded-xl !px-3 !py-1 text-xs"
            :disabled="props.loading || !hasReferenceDimensions"
            @click="fillBlankSkusFromReference"
          >
            同步到空白 SKU <PhArrowRight :size="13" aria-hidden="true" />
          </button>
        </div>

        <div class="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <label class="block">
            <span class="text-xs font-medium text-accent-600 dark:text-accent-300">来源尺寸长 cm</span>
            <input v-model="props.product.source.dimensions.lengthCm" class="input mt-1 h-8 !rounded-lg text-xs tabular-nums" :disabled="props.loading" />
          </label>
          <label class="block">
            <span class="text-xs font-medium text-accent-600 dark:text-accent-300">来源尺寸宽 cm</span>
            <input v-model="props.product.source.dimensions.widthCm" class="input mt-1 h-8 !rounded-lg text-xs tabular-nums" :disabled="props.loading" />
          </label>
          <label class="block">
            <span class="text-xs font-medium text-accent-600 dark:text-accent-300">来源尺寸高 cm</span>
            <input v-model="props.product.source.dimensions.heightCm" class="input mt-1 h-8 !rounded-lg text-xs tabular-nums" :disabled="props.loading" />
          </label>
          <label class="block">
            <span class="text-xs font-medium text-accent-600 dark:text-accent-300">来源重量 kg</span>
            <input v-model="props.product.source.weightKg" class="input mt-1 h-8 !rounded-lg text-xs tabular-nums" :disabled="props.loading" />
          </label>
        </div>
        <p v-if="syncHint" role="status" class="mt-2 text-xs font-medium text-primary-700 dark:text-primary-300">{{ syncHint }}</p>
      </section>
    </div>

    <!-- 分区 2：规格与 SKU -->
    <div v-show="showSkus" class="space-y-3">
      <p v-if="legacyCost" class="rounded-xl border border-amber-200/80 bg-amber-50/80 px-3.5 py-2 text-xs text-amber-900 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-200">
        旧记录中的商品级采购成本为 <strong>{{ legacyCost }}</strong> 元，仅供核对。确认适用规格后，可用下方批量设置填写 SKU 成本。
      </p>
      <ProductSkuEditor
        v-model="props.product.skuItems"
        :focus-sku-id="props.focusSkuId"
        :images="props.product.source.imagePool"
        :disabled="props.loading"
        :show-assign-upc="embedded"
        :assign-upc-disabled="!attributesValid"
        @assign-upc="emit('assignUpc')"
      />
    </div>

    <!-- 分区 3：商品属性 -->
    <div v-show="showAttributes" class="space-y-4">
      <ProductAttributesEditor
        :key="`${props.product.productId}:profile`"
        v-model="props.product.attributes"
        title="商品补充属性"
        description="你或 AI 添加到商品主档的属性。修改后点击“保存商品”。"
        empty-message="暂无补充属性，可以手动添加，也可以让 AI 为商品补充。"
        :disabled="props.loading"
        @validity-change="profileAttributesValid = $event"
      />

      <ProductAttributesEditor
        :key="`${props.product.productId}:source`"
        v-model="props.product.source.attributes"
        title="来源产品属性"
        description="保留采集到的全部属性，可补充或修正；修改后点击“保存商品”。平台刊登属性在草稿中按类目填写。"
        empty-message="暂无来源属性，可以手动添加或重新采集商品资料。"
        hint="容量范围、多个规格混合值和不明单位请保留原文，核实后再填写精确参数。"
        :disabled="props.loading"
        @validity-change="sourceAttributesValid = $event"
      />
    </div>
  </div>
</template>
