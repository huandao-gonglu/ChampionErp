<script setup lang="ts">
import { vDesktop } from '@/utils/desktopHandoffDirective'
import { modelOperationReason } from '@/utils/desktop'
import { computed, ref, watch } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { imageUrl, type SkuImageAssignment } from '@/utils/draftImages'
import type { DraftDetail, DraftImageRef, ImageAsset } from '@/types/workflow'
import type { ImageSetPlan } from '@/api/draftImages'
import type { DraftImageWorkbenchController } from '@/composables/useDraftImageWorkbench'

const props = defineProps<{
  open: boolean; draft: DraftDetail; images: ImageAsset[]; referenceIds: string[]
  controller: DraftImageWorkbenchController; loading?: boolean; skus?: SkuImageAssignment[]
}>()
const emit = defineEmits<{ close: [] }>()
const scopeOptions = [
  { value: 'all', label: '全部生成' },
  { value: 'common', label: '只生成共用主图' },
  { value: 'sku', label: '只生成 SKU 图' },
] as const
const scope = ref<(typeof scopeOptions)[number]['value']>('all')
const includeCommon = computed(() => scope.value !== 'sku')
const includeSku = computed(() => scope.value !== 'common')
const count = ref(6)
const prompt = ref('')
const sourceIds = ref<string[]>([])
const skuSourceIds = ref<string[]>([])
const plan = ref<ImageSetPlan | null>(null)
const planVersion = ref('')
const planningNew = ref(false)
const chosen = ref<string[]>([])
const selectionTouched = ref(false)
const busy = computed(() => props.loading || props.controller.busy.value)
const batch = computed(() => props.controller.batch.value)
const resultPlan = computed(() => planningNew.value ? null : batch.value?.image_set)
const byId = computed(() => new Map(props.images.map(image => [image.id, image])))
const candidates = computed(() => props.referenceIds.map(id => byId.value.get(id)).filter((image): image is ImageAsset => Boolean(image)))
const skuGroups = computed(() => {
  const groups = new Map<string, SkuImageAssignment[]>()
  for (const sku of props.skus || []) {
    if (!sku.selected || !sku.imageAssetId) continue
    groups.set(sku.imageAssetId, [...(groups.get(sku.imageAssetId) || []), sku])
  }
  return [...groups].map(([assetId, skus]) => ({ assetId, skus, image: byId.value.get(assetId) }))
})
const availableSkuGroups = computed(() => skuGroups.value.filter(group => group.image))
const selectedGroups = computed(() => includeSku.value
  ? availableSkuGroups.value.filter(group => skuSourceIds.value.includes(group.assetId))
  : [])
const selectedSkuIds = computed(() => selectedGroups.value.flatMap(group => group.skus.map(sku => sku.skuId)))
const total = computed(() => (includeCommon.value ? count.value : 0) + selectedGroups.value.length)
const results = computed(() => resultPlan.value?.pages.map(page => {
  const item = batch.value?.items.find(item => item.source_id === page.key)
  const asset = item?.status === 'success' ? byId.value.get(item.result_asset_ids?.[0] || '') : undefined
  return { page, item, asset }
}) || [])
const selectedRefs = computed<DraftImageRef[]>(() => chosen.value.flatMap(key => {
  const result = results.value.find(result => result.page.key === key)
  return result?.asset && result.page.scope !== 'sku' ? [{ assetId: result.asset.id, role: result.page.role, order: 0, label: result.page.title }] : []
}).map((ref, order) => ({ ...ref, order, role: order === 0 ? 'main' : ref.role === 'main' ? 'detail' : ref.role })))
const selectedSkuKeys = computed(() => chosen.value.filter(key => results.value.some(result => result.page.key === key && result.page.scope === 'sku' && result.asset)))
const commonOrder = computed(() => chosen.value.filter(key => results.value.some(result => result.page.key === key && result.page.scope !== 'sku')))
const selectedCount = computed(() => selectedRefs.value.length + selectedSkuKeys.value.length)
function targetLabel(page: ImageSetPlan['pages'][number]) {
  return page.scope === 'sku' ? `SKU 图 · ${(page.sku_ids || []).map(id => props.skus?.find(sku => sku.skuId === id)?.name || id).join('、')}` : '共用图'
}

watch(() => props.open, open => {
  if (open && !plan.value) resetSelection()
}, { immediate: true })
watch(() => props.draft.draftId, () => { plan.value = null; prompt.value = ''; planningNew.value = false; chosen.value = [] })
watch(() => batch.value?.conversation_id, () => {
  if (batch.value?.image_set) { planningNew.value = false; plan.value = null; chosen.value = []; selectionTouched.value = false }
})
watch(results, values => {
  // 完成项按方案顺序加入候选；用户随后可以取消采用或调整顺序。
  chosen.value = selectionTouched.value
    ? chosen.value.filter(key => values.some(value => value.page.key === key && value.asset))
    : values.filter(value => value.asset).map(value => value.page.key)
}, { immediate: true })
const planReason = computed(() => !plan.value && !resultPlan.value && includeCommon.value ? modelOperationReason('image.plan') : '')
const generateReason = computed(() => modelOperationReason('image.translate'))
const canPrepare = computed(() => !busy.value && !planReason.value
  && (!includeCommon.value || (sourceIds.value.length > 0 && sourceIds.value.length <= 12))
  && (!includeSku.value || selectedGroups.value.length > 0))
function resetSelection() {
  sourceIds.value = candidates.value.slice(0, 12).map(image => image.id)
  skuSourceIds.value = availableSkuGroups.value.map(group => group.assetId)
  scope.value = !availableSkuGroups.value.length ? 'common' : candidates.value.length ? 'all' : 'sku'
}
function newPlan() {
  planningNew.value = true; plan.value = null
  resetSelection()
}
async function prepare() {
  if (!canPrepare.value) return
  const result = await props.controller.planSet(includeCommon.value ? [...sourceIds.value] : [],
    includeCommon.value ? count.value : undefined, prompt.value.trim(), selectedSkuIds.value)
  if (result) { plan.value = result.plan; planVersion.value = result.updated_at }
}
function move(key: string, step: number) {
  const from = commonOrder.value.indexOf(key), to = from + step
  if (from < 0 || to < 0 || to >= commonOrder.value.length) return
  selectionTouched.value = true
  const target = commonOrder.value[to]
  const sourceIndex = chosen.value.indexOf(key), targetIndex = chosen.value.indexOf(target)
  chosen.value.splice(sourceIndex, 1); chosen.value.splice(targetIndex, 0, key)
}
function toggle(key: string, checked: boolean) {
  selectionTouched.value = true
  chosen.value = checked ? [...chosen.value, key] : chosen.value.filter(value => value !== key)
}
function applySelection() {
  if (selectedSkuKeys.value.length) void props.controller.applySet(selectedRefs.value, selectedSkuKeys.value)
  else void props.controller.applySet(selectedRefs.value)
}
</script>

<template>
  <WorkspaceDialog :open="open" title="生成套图" variant="drawer" width="64rem" @close="emit('close')">
    <div v-if="open" class="space-y-5" data-testid="image-set-dialog">
      <p v-if="planReason" role="status" class="muted">{{ planReason }}</p>
      <p v-if="generateReason && generateReason !== planReason" role="status" class="muted">{{ generateReason }}</p>
      <p v-if="controller.notice.value" role="status" class="rounded-lg bg-slate-100 p-3 text-sm dark:bg-dark-800">{{ controller.notice.value }}</p>
      <button v-if="planningNew && batch?.image_set" class="btn btn-outline" type="button" :disabled="busy" @click="planningNew = false">返回上次套图</button>
      <template v-if="resultPlan">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <p role="status" class="text-sm">{{ batch?.terminal ? '生成结束' : '正在生成套图' }} · {{ batch?.completed }} / {{ batch?.total }} 张<span v-if="batch?.failed"> · {{ batch.failed }} 张未完成</span></p>
          <button class="btn btn-outline" type="button" :disabled="busy" @click="newPlan">规划新套图</button>
        </div>
        <p class="text-sm">统一风格：{{ resultPlan.style }}</p>
        <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <article v-for="result in results" :key="result.page.key" class="rounded-xl border border-slate-200 p-3 dark:border-dark-700" :data-set-page="result.page.key">
            <a v-if="result.asset" :href="imageUrl(result.asset)" target="_blank" rel="noopener" :aria-label="`查看套图 ${result.page.title}`"><img :src="imageUrl(result.asset)" :alt="result.page.title" class="aspect-square w-full rounded-lg object-contain" /></a>
            <div v-else class="flex aspect-square items-center justify-center rounded-lg bg-slate-50 p-4 text-center text-sm dark:bg-dark-800">{{ result.item?.outcome_unknown ? '结果待核对' : result.item?.status === 'failed' ? result.item.error : result.item?.status === 'running' ? '生成中…' : '等待生成' }}</div>
            <p class="mt-3 font-medium">{{ result.page.title }}</p>
            <p class="mt-1 text-xs text-slate-500 dark:text-slate-400">{{ targetLabel(result.page) }}</p>
            <p v-if="result.item?.status === 'success' && !result.asset" class="text-sm text-rose-600">结果素材缺失，请重新读取。</p>
            <div class="mt-3 flex flex-wrap items-center gap-2">
              <label v-if="result.asset" class="mr-auto flex items-center gap-2 text-sm"><input type="checkbox" :checked="chosen.includes(result.page.key)" :disabled="busy" :aria-label="`采用 ${result.page.title}`" @change="toggle(result.page.key, ($event.target as HTMLInputElement).checked)" />采用</label>
              <button v-if="result.asset && chosen.includes(result.page.key) && result.page.scope !== 'sku'" class="btn btn-outline px-2" type="button" :disabled="busy || commonOrder.indexOf(result.page.key) === 0" :aria-label="`前移 ${result.page.title}`" @click="move(result.page.key, -1)">←</button>
              <button v-if="result.asset && chosen.includes(result.page.key) && result.page.scope !== 'sku'" class="btn btn-outline px-2" type="button" :disabled="busy || commonOrder.indexOf(result.page.key) === commonOrder.length - 1" :aria-label="`后移 ${result.page.title}`" @click="move(result.page.key, 1)">→</button>
              <button v-desktop="'image.translate'" class="btn btn-outline" type="button" :disabled="busy || !!generateReason" :aria-label="`重做 ${result.page.title}`" @click="controller.generateSet(undefined, draft.updatedAt, [result.page.key])">重做此图</button>
            </div>
          </article>
        </div>
        <div v-if="selectedRefs.length" class="rounded-lg bg-slate-50 p-3 text-sm dark:bg-dark-800">采用顺序：{{ selectedRefs.map((image, index) => `${index + 1}. ${image.label}`).join(' → ') }}<p class="muted mt-1">第一张作为共用主图。请核对商品颜色、结构和文案是否准确，是否适用于当前各 SKU。</p></div>
        <p v-if="selectedSkuKeys.length" class="muted">采用 {{ selectedSkuKeys.length }} 张 SKU 图，更新对应规格；未采用或生成失败的规格继续使用当前图片。</p>
        <div class="flex justify-end gap-3"><button type="button" class="btn btn-outline" @click="emit('close')">关闭</button><button type="button" class="btn btn-primary" :disabled="busy || !selectedCount" @click="applySelection">{{ selectedSkuKeys.length ? `采用 ${selectedCount} 张并更新图片` : `采用 ${selectedRefs.length} 张并替换共用图` }}</button></div>
      </template>
      <form v-else-if="plan" v-desktop="'image.translate'" class="space-y-5" @submit.prevent="controller.generateSet(plan, planVersion)">
        <label class="block text-sm font-medium">统一视觉风格<textarea v-model="plan.style" class="input mt-2" rows="3" maxlength="2000" required :disabled="busy" /></label>
        <article v-for="(page, index) in plan.pages" :key="page.key" class="space-y-3 rounded-xl border border-slate-200 p-4 dark:border-dark-700">
          <label class="block text-sm font-medium">第 {{ index + 1 }} 张 · {{ targetLabel(page) }}{{ page.role === 'main' ? ' · 主图' : '' }}<input v-model="page.title" class="input mt-2" maxlength="100" required :disabled="busy" /></label>
          <label class="block text-sm">设计内容<textarea v-model="page.prompt" class="input mt-2" rows="4" maxlength="4000" required :disabled="busy" /></label>
          <div class="flex flex-wrap gap-2"><img v-for="id in page.source_image_ids" :key="id" :src="byId.has(id) ? imageUrl(byId.get(id)!) : ''" alt="本页参考素材" class="h-16 w-16 rounded object-contain" /></div>
        </article>
        <p class="muted">生成 {{ plan.pages.length }} 张新图（共用 {{ plan.pages.filter(page => page.scope !== 'sku').length }} 张，SKU {{ plan.pages.filter(page => page.scope === 'sku').length }} 张），不立即替换当前草稿图片。</p>
        <div class="flex justify-end gap-3"><button class="btn btn-outline" type="button" :disabled="busy" @click="plan = null">返回调整</button><button v-desktop="'image.translate'" class="btn btn-primary" type="submit" :disabled="busy || !!generateReason">{{ busy ? '正在提交…' : `开始生成 ${plan.pages.length} 张` }}</button></div>
      </form>
      <form v-else v-desktop="includeCommon ? 'image.plan' : false" class="space-y-5" @submit.prevent="prepare">
        <fieldset :disabled="busy" class="space-y-5">
          <legend class="mb-3 text-sm font-medium">生成范围</legend>
          <div class="flex flex-wrap gap-4">
            <label v-for="option in scopeOptions" :key="option.value" class="flex items-center gap-2 text-sm">
              <input v-model="scope" type="radio" name="image-set-scope" :value="option.value"
                :disabled="(option.value !== 'common' && !availableSkuGroups.length) || (option.value !== 'sku' && !candidates.length)" />
              {{ option.label }}
            </label>
          </div>
          <p v-if="!availableSkuGroups.length" class="muted">当前草稿已启用的 SKU 没有可用的独立图片。</p>
          <fieldset v-if="includeCommon" class="space-y-3">
            <legend class="mb-3 text-sm font-medium">共用图参考素材（{{ sourceIds.length }} / 12）</legend>
            <div class="grid grid-cols-3 gap-3 sm:grid-cols-5">
              <label v-for="image in candidates" :key="image.id" class="relative cursor-pointer rounded-lg border border-slate-200 p-1 dark:border-dark-700"><img :src="imageUrl(image)" alt="套图参考素材" class="aspect-square w-full object-contain" /><input v-model="sourceIds" type="checkbox" :value="image.id" :aria-label="`套图参考 ${image.id}`" class="absolute left-2 top-2 h-5 w-5" /></label>
            </div>
            <label class="block text-sm font-medium">共用图张数<select v-model.number="count" class="input mt-2"><option v-for="number in [2, 3, 4, 5, 6, 7, 8]" :key="number" :value="number">{{ number }} 张</option></select></label>
          </fieldset>
          <div v-if="includeSku" class="space-y-3">
            <p class="text-sm font-medium">SKU 图（已选 {{ selectedGroups.length }} 张）</p>
            <div class="grid max-h-80 grid-cols-2 gap-3 overflow-y-auto sm:grid-cols-3">
              <label v-for="group in skuGroups" :key="group.assetId" class="flex cursor-pointer items-center gap-3 rounded-lg border border-slate-200 p-2 text-sm dark:border-dark-700">
                <input v-model="skuSourceIds" type="checkbox" :value="group.assetId" :disabled="!group.image" :aria-label="`生成 SKU 图 ${group.assetId}`" />
                <img v-if="group.image" :src="imageUrl(group.image)" alt="SKU 原图" class="h-14 w-14 rounded object-contain" />
                <span>{{ group.skus.map(sku => sku.name).join('、') }}<span v-if="!group.image" class="block text-rose-600">素材缺失</span></span>
              </label>
            </div>
            <p class="muted">共用同一张原图的 SKU 合并生成一次，颜色和规格以各自原图为准。</p>
          </div>
          <label class="block text-sm font-medium">补充要求（可选）<textarea v-model="prompt" class="input mt-2" rows="3" maxlength="4000" placeholder="例如：简洁明亮的风格，重点展示收纳方式和材质细节。" /></label>
          <p class="muted">目标语言：{{ draft.language }}。预计生成 {{ total }} 张图片。</p>
          <div class="flex justify-end"><button type="submit" class="btn btn-primary" :disabled="!canPrepare">{{ busy ? '正在规划…' : '自动规划套图' }}</button></div>
        </fieldset>
      </form>
    </div>
  </WorkspaceDialog>
</template>
