<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  PhArrowCounterClockwise,
  PhBarcode,
  PhCaretDown,
  PhImage,
  PhPlus,
  PhProhibit,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import ProductAttributesEditor from './ProductAttributesEditor.vue'
import SkuImagePicker from './SkuImagePicker.vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { imageUrl } from '@/utils/draftImages'
import type { ImageAsset, ProductSku } from '@/types/workflow'

const rows = defineModel<ProductSku[]>({ required: true })
const props = defineProps<{
  disabled?: boolean
  images?: ImageAsset[]
  focusSkuId?: string
  showAssignUpc?: boolean
  assignUpcDisabled?: boolean
}>()
const emit = defineEmits<{ assignUpc: [] }>()

const expanded = ref('')
const skuFilter = ref<'all' | 'missing' | 'inactive'>('all')
const preview = ref<{ url: string; name: string } | null>(null)
const failedImages = ref(new Set<string>())
const skuImageUrls = computed(() => new Map((props.images || []).map(image => [image.id, imageUrl(image)])))
function skuImageUrl(row: ProductSku) { return skuImageUrls.value.get(row.image_asset_id) || '' }

const selected = ref<string[]>([])
const batchField = ref('cost_cny')
const batchValue = ref('')
const batchFields = [
  ['cost_cny', '采购成本 CNY'],
  ['length_cm', '包装长 cm'],
  ['width_cm', '包装宽 cm'],
  ['height_cm', '包装高 cm'],
  ['weight_kg', '包装重量 kg'],
  ['supplier_stock', '供应商库存'],
  ['battery', '含电池'],
  ['liquid', '含液体'],
]
const fields = [
  ['length_cm', '包装长 cm'],
  ['width_cm', '包装宽 cm'],
  ['height_cm', '包装高 cm'],
  ['weight_kg', '包装重 kg'],
] as const

const batchBoolean = computed(() => ['battery', 'liquid'].includes(batchField.value))
const batchValid = computed(() => batchValue.value !== '' && (batchBoolean.value
  ? ['true', 'false'].includes(batchValue.value)
  : Number.isFinite(Number(batchValue.value)) && Number(batchValue.value) >= 0
    && (!['length_cm', 'width_cm', 'height_cm', 'weight_kg'].includes(batchField.value) || Number(batchValue.value) > 0)
    && (batchField.value !== 'supplier_stock' || Number.isInteger(Number(batchValue.value)))))

function isPositiveNumber(value: unknown) {
  const text = String(value ?? '').trim()
  if (!text) return false
  const num = Number(text)
  return Number.isFinite(num) && num > 0
}

function skuMissingFields(row: ProductSku): string[] {
  if (!row.active) return []
  const missing: string[] = []
  if (!isPositiveNumber(row.cost_cny)) missing.push('采购成本')
  for (const [key, label] of fields) {
    if (!isPositiveNumber(row.package_dimensions?.[key])) missing.push(label)
  }
  return missing
}

const missingCount = computed(() => rows.value.filter(row => skuMissingFields(row).length > 0).length)
const inactiveCount = computed(() => rows.value.filter(row => !row.active).length)
const missingBarcodeCount = computed(() => rows.value.filter(row => row.active && !row.barcode).length)

const visibleRows = computed(() => {
  if (skuFilter.value === 'missing') return rows.value.filter(row => skuMissingFields(row).length > 0)
  if (skuFilter.value === 'inactive') return rows.value.filter(row => !row.active)
  return rows.value
})

const selectedVisibleCount = computed(() => visibleRows.value.filter(row => selected.value.includes(row.id)).length)
const allVisibleSelected = computed(() => visibleRows.value.length > 0 && selectedVisibleCount.value === visibleRows.value.length)

function toggleSelectAll(checked: boolean) {
  const visibleIds = visibleRows.value.map(row => row.id)
  if (checked) {
    selected.value = Array.from(new Set([...selected.value, ...visibleIds]))
  } else {
    selected.value = selected.value.filter(id => !visibleIds.includes(id))
  }
}

watch(batchField, () => { batchValue.value = '' })
watch(() => props.focusSkuId, async id => {
  if (!id) return
  skuFilter.value = 'all'
  expanded.value = id
  selected.value = [id]
  await nextTick()
  document.getElementById(`product-sku-${id}`)?.scrollIntoView?.({ block: 'center' })
}, { immediate: true })

function applyBatch() {
  if (props.disabled || !batchValid.value || !selected.value.length) return
  for (const row of rows.value.filter(row => selected.value.includes(row.id))) {
    if (['length_cm', 'width_cm', 'height_cm', 'weight_kg'].includes(batchField.value)) {
      row.package_dimensions[batchField.value] = String(batchValue.value)
    } else {
      row[batchField.value] = batchBoolean.value ? batchValue.value === 'true' : String(batchValue.value)
    }
  }
}

function add() {
  const id = crypto.randomUUID()
  skuFilter.value = 'all'
  rows.value.push({
    id,
    source_sku_id: '',
    name: '',
    options: {},
    cost_cny: '',
    supplier_stock: '',
    image_asset_id: '',
    barcode: '',
    package_dimensions: {},
    battery: false,
    liquid: false,
    active: true,
    source_snapshot: {},
  })
  expanded.value = id
}
</script>

<template>
  <section class="rounded-2xl border border-accent-200/90 bg-white p-4 shadow-sm dark:border-dark-700 dark:bg-dark-800/70">
    <!-- 顶部栏：标题 + 状态筛选 + 添加/分配 UPC -->
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="flex flex-wrap items-center gap-2">
        <h3 class="text-sm font-bold text-accent-950 dark:text-white">
          规格与 SKU
          <span class="ml-1 rounded-full bg-accent-100 px-2 py-0.5 text-xs font-semibold tabular-nums text-accent-600 dark:bg-dark-700 dark:text-accent-200">{{ rows.length }}</span>
        </h3>
        <div class="flex items-center gap-1 text-xs" role="group" aria-label="按 SKU 完整度筛选">
          <button type="button" class="sku-filter-pill" :aria-pressed="skuFilter === 'all'" @click="skuFilter = 'all'">
            全部 {{ rows.length }}
          </button>
          <button
            type="button"
            class="sku-filter-pill"
            :class="missingCount ? 'sku-filter-pill--warn' : ''"
            :aria-pressed="skuFilter === 'missing'"
            @click="skuFilter = 'missing'"
          >
            缺资料 {{ missingCount }}
          </button>
          <button
            v-if="inactiveCount"
            type="button"
            class="sku-filter-pill"
            :aria-pressed="skuFilter === 'inactive'"
            @click="skuFilter = 'inactive'"
          >
            已停用 {{ inactiveCount }}
          </button>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <button
          v-if="showAssignUpc"
          type="button"
          class="btn btn-outline !min-h-8 !rounded-xl !px-3 !py-1 text-xs"
          :disabled="disabled || assignUpcDisabled || !missingBarcodeCount"
          @click="emit('assignUpc')"
        >
          <PhBarcode :size="14" aria-hidden="true" />为缺条码 SKU 分配 UPC<span v-if="missingBarcodeCount" class="tabular-nums">（{{ missingBarcodeCount }}）</span>
        </button>
        <button
          type="button"
          class="btn btn-outline !min-h-8 !rounded-xl !px-3 !py-1 text-xs"
          :disabled="disabled"
          @click="add"
        >
          <PhPlus :size="14" weight="bold" aria-hidden="true" />添加 SKU
        </button>
      </div>
    </div>

    <p class="mt-1.5 text-xs text-accent-500 dark:text-accent-400">
      每个规格维护独立采购成本与包装尺寸，保存后同步给所有引用草稿；成本或包装变更后需重新核价。
    </p>

    <!-- 批量编辑工具栏 -->
    <fieldset
      :disabled="disabled"
      class="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-accent-200/80 bg-accent-50/70 px-3 py-2 text-xs dark:border-dark-700 dark:bg-dark-900/50"
    >
      <label class="inline-flex cursor-pointer items-center gap-1.5 font-medium text-accent-700 dark:text-accent-200">
        <input
          type="checkbox"
          class="size-4 cursor-pointer accent-primary-600"
          aria-label="全选批量编辑 SKU"
          :checked="allVisibleSelected"
          :indeterminate="selectedVisibleCount > 0 && !allVisibleSelected"
          @change="toggleSelectAll(($event.target as HTMLInputElement).checked)"
        />
        全选
      </label>
      <span class="h-4 w-px bg-accent-200 dark:bg-dark-700" aria-hidden="true" />
      <select v-model="batchField" class="input h-8 !w-auto !rounded-lg !py-0 text-xs" aria-label="批量修改字段">
        <option v-for="[key, label] in batchFields" :key="key" :value="key">{{ label }}</option>
      </select>
      <select v-if="batchBoolean" v-model="batchValue" class="input h-8 !w-auto !rounded-lg !py-0 text-xs" aria-label="批量设置值">
        <option value="">请选择</option>
        <option value="true">是</option>
        <option value="false">否</option>
      </select>
      <input
        v-else
        v-model="batchValue"
        type="number"
        min="0"
        step="any"
        placeholder="输入数值"
        class="input h-8 !w-28 !rounded-lg !py-0 text-xs tabular-nums"
        aria-label="批量设置值"
      />
      <button
        type="button"
        class="btn btn-outline !min-h-8 !rounded-lg !px-2.5 !py-1 text-xs"
        :disabled="!selected.length || !batchValid"
        @click="applyBatch"
      >
        应用到选中 {{ selected.length }} 个 SKU
      </button>
      <span class="ml-auto text-[11px] text-accent-500 dark:text-accent-400">修改后点击顶部「保存商品」生效</span>
    </fieldset>

    <!-- SKU 表格 -->
    <p v-if="!rows.length" class="py-8 text-center text-sm text-accent-500 dark:text-accent-400">暂无 SKU，请采集商品或添加一个规格。</p>
    <p v-else-if="!visibleRows.length" class="py-8 text-center text-sm text-accent-500 dark:text-accent-400">当前筛选下没有匹配的 SKU。</p>
    <div v-else class="mt-3 overflow-x-auto rounded-xl border border-accent-200/90 dark:border-dark-700">
      <table class="w-full min-w-[880px] text-left text-xs">
        <thead class="border-b border-accent-200/80 bg-accent-50/90 text-xs font-semibold text-accent-600 dark:border-dark-700 dark:bg-dark-900/80 dark:text-accent-300">
          <tr>
            <th class="w-9 px-2 py-2.5 text-center">选择</th>
            <th class="w-12 px-2 py-2.5 text-center">启用</th>
            <th class="w-20 px-2 py-2.5">图片</th>
            <th class="min-w-48 px-2 py-2.5">名称 / 规格</th>
            <th class="w-28 px-1.5 py-2.5">采购成本 CNY</th>
            <th class="w-24 px-1.5 py-2.5">供应商库存</th>
            <th v-for="field in fields" :key="field[0]" class="w-24 px-1.5 py-2.5">{{ field[1] }}</th>
            <th class="w-20 px-2 py-2.5 text-right">详情</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-accent-200/70 dark:divide-dark-700/70">
          <template v-for="row in visibleRows" :key="row.id">
            <tr
              :id="`product-sku-${row.id}`"
              class="align-middle transition-colors hover:bg-accent-50/70 dark:hover:bg-white/[0.03]"
              :class="!row.active ? 'bg-accent-50/70 opacity-60 dark:bg-dark-900/60' : selected.includes(row.id) ? 'bg-primary-50/40 dark:bg-primary-500/10' : ''"
            >
              <td class="px-2 py-2 text-center">
                <input
                  v-model="selected"
                  type="checkbox"
                  class="size-4 cursor-pointer accent-primary-600"
                  :value="row.id"
                  :aria-label="`批量选择 ${row.name}`"
                  :disabled="disabled"
                />
              </td>
              <td class="px-2 py-2 text-center">
                <input
                  v-model="row.active"
                  type="checkbox"
                  class="size-4 cursor-pointer accent-primary-600"
                  :disabled="disabled"
                  :aria-label="`启用 ${row.name}`"
                />
              </td>
              <td class="w-20 px-2 py-2">
                <button
                  v-if="skuImageUrl(row) && !failedImages.has(skuImageUrl(row))"
                  type="button"
                  class="block size-14 overflow-hidden rounded-xl border border-accent-200 bg-white transition hover:border-primary-400 focus-visible:ring-2 focus-visible:ring-primary-500 dark:border-dark-600 dark:bg-dark-900"
                  :aria-label="`预览 ${row.name} 的图片`"
                  @click="preview = { url: skuImageUrl(row), name: row.name }"
                >
                  <img :key="skuImageUrl(row)" :src="skuImageUrl(row)" :alt="`${row.name} 的图片`" loading="lazy" class="size-full object-contain" @error="failedImages.add(skuImageUrl(row))" />
                </button>
                <span
                  v-else
                  class="flex size-14 flex-col items-center justify-center gap-0.5 rounded-xl border border-dashed border-accent-300 bg-accent-50/70 p-1 text-center text-[10px] leading-tight text-accent-500 dark:border-dark-600 dark:bg-dark-900/50 dark:text-accent-400"
                >
                  <PhImage :size="14" aria-hidden="true" />
                  <span>{{ row.image_asset_id ? '图片不可用' : '未设置图片' }}</span>
                </span>
              </td>
              <td class="min-w-48 px-2 py-2">
                <input v-model="row.name" class="input h-8 !rounded-lg !py-0 text-xs font-medium" aria-label="SKU 名称" :disabled="disabled" />
                <div class="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-accent-500 dark:text-accent-400">
                  <span v-if="Object.values(row.options || {}).length" class="truncate">{{ Object.values(row.options || {}).join(' / ') }}</span>
                  <span v-if="!row.active" class="rounded bg-accent-200 px-1.5 py-0.5 text-[10px] font-semibold text-accent-700 dark:bg-dark-700 dark:text-accent-300">已停用</span>
                  <span
                    v-else-if="skuMissingFields(row).length"
                    class="inline-flex items-center gap-0.5 rounded bg-amber-100/90 px-1.5 py-0.5 text-[10px] font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-300"
                    :title="`缺失：${skuMissingFields(row).join('、')}`"
                  >
                    <PhWarningCircle :size="11" aria-hidden="true" />缺 {{ skuMissingFields(row).length }} 项
                  </span>
                </div>
              </td>
              <td class="w-28 px-1.5 py-2">
                <input
                  v-model="row.cost_cny"
                  class="input h-8 !rounded-lg !py-0 text-xs tabular-nums"
                  :class="row.active && !isPositiveNumber(row.cost_cny) ? '!border-amber-400 dark:!border-amber-500/60' : ''"
                  type="number"
                  min="0"
                  step="0.01"
                  aria-label="采购成本 CNY"
                  :disabled="disabled"
                />
              </td>
              <td class="w-24 px-1.5 py-2">
                <input v-model="row.supplier_stock" class="input h-8 !rounded-lg !py-0 text-xs tabular-nums" aria-label="供应商库存" :disabled="disabled" />
              </td>
              <td v-for="field in fields" :key="field[0]" class="w-24 px-1.5 py-2">
                <input
                  v-model="row.package_dimensions[field[0]]"
                  class="input h-8 !rounded-lg !py-0 text-xs tabular-nums"
                  :class="row.active && !isPositiveNumber(row.package_dimensions?.[field[0]]) ? '!border-amber-400 dark:!border-amber-500/60' : ''"
                  type="number"
                  min="0"
                  step="any"
                  :aria-label="field[1]"
                  :disabled="disabled"
                />
                <small
                  v-if="row.package_estimates?.[field[0]] && Number(row.package_dimensions[field[0]]) === row.package_estimates[field[0]].suggested"
                  class="mt-0.5 block text-[10px] leading-tight text-accent-500 dark:text-accent-400"
                  :title="row.package_estimates[field[0]].basis"
                >
                  估算 {{ row.package_estimates[field[0]].low }}–{{ row.package_estimates[field[0]].high }}
                </small>
              </td>
              <td class="whitespace-nowrap px-2 py-2 text-right">
                <div class="inline-flex items-center gap-1">
                  <button
                    type="button"
                    class="grid size-7 place-items-center rounded-lg border border-accent-200 bg-white text-accent-600 transition hover:bg-accent-100 hover:text-accent-950 dark:border-dark-600 dark:bg-dark-800 dark:text-accent-300 dark:hover:bg-dark-700"
                    :aria-label="`编辑 ${row.name} 详情`"
                    :aria-expanded="expanded === row.id"
                    title="展开 SKU 详情（图片、条码、电池/液体、规格属性）"
                    @click="expanded = expanded === row.id ? '' : row.id"
                  >
                    <PhCaretDown :size="14" class="transition-transform" :class="expanded === row.id ? 'rotate-180 text-primary-600 dark:text-primary-400' : ''" aria-hidden="true" />
                  </button>
                  <button
                    v-if="!row.source_sku_id"
                    type="button"
                    class="grid size-7 place-items-center rounded-lg border border-accent-200 bg-white transition disabled:opacity-40 dark:border-dark-600 dark:bg-dark-800"
                    :class="row.active ? 'text-amber-600 hover:border-amber-300 hover:bg-amber-50 dark:text-amber-400 dark:hover:bg-amber-500/10' : 'text-primary-600 hover:border-primary-300 hover:bg-primary-50 dark:text-primary-400 dark:hover:bg-primary-500/10'"
                    :aria-label="row.active ? '停用 SKU' : '重新启用 SKU'"
                    :title="row.active ? '停用 SKU' : '重新启用 SKU'"
                    :disabled="disabled"
                    @click="row.active = !row.active"
                  >
                    <PhProhibit v-if="row.active" :size="14" aria-hidden="true" />
                    <PhArrowCounterClockwise v-else :size="14" aria-hidden="true" />
                  </button>
                </div>
              </td>
            </tr>
            <tr v-if="expanded === row.id">
              <td colspan="11" class="bg-accent-50/70 p-4 dark:bg-dark-950/70">
                <div class="grid gap-3 md:grid-cols-3">
                  <SkuImagePicker v-model="row.image_asset_id" :images="images" :disabled="disabled" :label="`${row.name} 的图片`" />
                  <label class="block text-xs font-medium text-accent-700 dark:text-accent-200">
                    统一货号（跨平台共享）
                    <input v-model="row.master_sku" class="input mt-1.5 h-9 !rounded-lg font-mono text-xs" placeholder="保存后自动生成稳定货号" :disabled="disabled" />
                  </label>
                  <label class="block text-xs font-medium text-accent-700 dark:text-accent-200">
                    商品条码（UPC / EAN）
                    <input v-model="row.barcode" class="input mt-1.5 h-9 !rounded-lg font-mono text-xs" placeholder="未分配条码" :disabled="disabled" />
                  </label>
                </div>
                <div class="mt-3 flex flex-wrap items-center gap-5 text-xs text-accent-700 dark:text-accent-200">
                  <label class="inline-flex cursor-pointer items-center gap-1.5">
                    <input v-model="row.battery" type="checkbox" class="size-4 accent-primary-600" :disabled="disabled" />含电池
                  </label>
                  <label class="inline-flex cursor-pointer items-center gap-1.5">
                    <input v-model="row.liquid" type="checkbox" class="size-4 accent-primary-600" :disabled="disabled" />含液体
                  </label>
                  <span v-if="row.source_sku_id" class="ml-auto font-mono text-[11px] text-accent-500 dark:text-accent-400">来源 SKU：{{ row.source_sku_id }}</span>
                </div>
                <ProductAttributesEditor
                  v-model="row.options"
                  empty-message="尚未设置属性"
                  title="规格属性"
                  description="按实际商品填写颜色、尺寸等维度；不会自动生成不存在的组合。"
                  :disabled="disabled"
                />
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </section>

  <WorkspaceDialog :open="Boolean(preview)" title="SKU 图片预览" @close="preview = null">
    <img v-if="preview" :src="preview.url" :alt="`${preview.name} 的图片完整预览`" class="mx-auto max-h-[70dvh] max-w-full object-contain" />
  </WorkspaceDialog>
</template>

<style scoped>
.sku-filter-pill {
  @apply rounded-lg px-2 py-1 font-medium text-accent-600 transition hover:bg-accent-100 hover:text-accent-950 dark:text-accent-300 dark:hover:bg-white/5 dark:hover:text-white;
}
.sku-filter-pill[aria-pressed='true'] {
  @apply bg-primary-50 font-semibold text-primary-700 dark:bg-primary-500/15 dark:text-primary-300;
}
.sku-filter-pill--warn {
  @apply text-amber-700 dark:text-amber-300;
}
</style>
