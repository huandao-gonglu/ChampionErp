<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { imageUrl } from '@/utils/draftImages'
import type { DraftDetail, DraftImageRef, ImageAsset } from '@/types/workflow'
import type { ImageSetPlan } from '@/api/draftImages'
import type { DraftImageWorkbenchController } from '@/composables/useDraftImageWorkbench'

const props = defineProps<{
  open: boolean; draft: DraftDetail; images: ImageAsset[]; referenceIds: string[]
  controller: DraftImageWorkbenchController; loading?: boolean
}>()
const emit = defineEmits<{ close: [] }>()
const count = ref(6)
const prompt = ref('')
const sourceIds = ref<string[]>([])
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
const results = computed(() => resultPlan.value?.pages.map(page => {
  const item = batch.value?.items.find(item => item.source_id === page.key)
  const asset = item?.status === 'success' ? byId.value.get(item.result_asset_ids?.[0] || '') : undefined
  return { page, item, asset }
}) || [])
const selectedRefs = computed<DraftImageRef[]>(() => chosen.value.flatMap(key => {
  const result = results.value.find(result => result.page.key === key)
  return result?.asset ? [{ assetId: result.asset.id, role: result.page.role, order: 0, label: result.page.title }] : []
}).map((ref, order) => ({ ...ref, order, role: order === 0 ? 'main' : ref.role === 'main' ? 'detail' : ref.role })))

watch(() => props.open, open => {
  if (open && !plan.value) sourceIds.value = [...props.referenceIds].slice(0, 12)
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
function newPlan() {
  planningNew.value = true; plan.value = null
  sourceIds.value = [...props.referenceIds].slice(0, 12)
}
async function prepare() {
  const result = await props.controller.planSet([...sourceIds.value], count.value, prompt.value.trim())
  if (result) { plan.value = result.plan; planVersion.value = result.updated_at }
}
function move(key: string, step: number) {
  const from = chosen.value.indexOf(key), to = from + step
  if (from < 0 || to < 0 || to >= chosen.value.length) return
  selectionTouched.value = true
  chosen.value.splice(from, 1); chosen.value.splice(to, 0, key)
}
function toggle(key: string, checked: boolean) {
  selectionTouched.value = true
  chosen.value = checked ? [...chosen.value, key] : chosen.value.filter(value => value !== key)
}
</script>

<template>
  <WorkspaceDialog :open="open" title="生成共用套图" variant="drawer" width="64rem" @close="emit('close')">
    <div v-if="open" class="space-y-5" data-testid="image-set-dialog">
      <p class="muted">根据参考素材重新设计整套图片，生成后预览并选择采用。只更新共用图，SKU 图片关联保留。</p>
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
            <p v-if="result.item?.status === 'success' && !result.asset" class="text-sm text-rose-600">结果素材缺失，请重新读取。</p>
            <div class="mt-3 flex flex-wrap items-center gap-2">
              <label v-if="result.asset" class="mr-auto flex items-center gap-2 text-sm"><input type="checkbox" :checked="chosen.includes(result.page.key)" :disabled="busy" :aria-label="`采用 ${result.page.title}`" @change="toggle(result.page.key, ($event.target as HTMLInputElement).checked)" />采用</label>
              <button v-if="result.asset && chosen.includes(result.page.key)" class="btn btn-outline px-2" type="button" :disabled="busy || chosen.indexOf(result.page.key) === 0" :aria-label="`前移 ${result.page.title}`" @click="move(result.page.key, -1)">←</button>
              <button v-if="result.asset && chosen.includes(result.page.key)" class="btn btn-outline px-2" type="button" :disabled="busy || chosen.indexOf(result.page.key) === chosen.length - 1" :aria-label="`后移 ${result.page.title}`" @click="move(result.page.key, 1)">→</button>
              <button class="btn btn-outline" type="button" :disabled="busy" :aria-label="`重做 ${result.page.title}`" @click="controller.generateSet(undefined, draft.updatedAt, [result.page.key])">重做此图</button>
            </div>
          </article>
        </div>
        <div v-if="selectedRefs.length" class="rounded-lg bg-slate-50 p-3 text-sm dark:bg-dark-800">采用顺序：{{ selectedRefs.map((image, index) => `${index + 1}. ${image.label}`).join(' → ') }}<p class="muted mt-1">第一张作为共用主图。请核对商品颜色、结构和文案是否准确，是否适用于当前各 SKU。</p></div>
        <div class="flex justify-end gap-3"><button type="button" class="btn btn-outline" @click="emit('close')">关闭</button><button type="button" class="btn btn-primary" :disabled="busy || !selectedRefs.length" @click="controller.applySet(selectedRefs)">采用 {{ selectedRefs.length }} 张并替换共用图</button></div>
      </template>
      <form v-else-if="plan" class="space-y-5" @submit.prevent="controller.generateSet(plan, planVersion)">
        <label class="block text-sm font-medium">统一视觉风格<textarea v-model="plan.style" class="input mt-2" rows="3" maxlength="2000" required :disabled="busy" /></label>
        <article v-for="(page, index) in plan.pages" :key="page.key" class="space-y-3 rounded-xl border border-slate-200 p-4 dark:border-dark-700">
          <label class="block text-sm font-medium">第 {{ index + 1 }} 张{{ index === 0 ? ' · 主图' : '' }}<input v-model="page.title" class="input mt-2" maxlength="100" required :disabled="busy" /></label>
          <label class="block text-sm">设计内容<textarea v-model="page.prompt" class="input mt-2" rows="4" maxlength="4000" required :disabled="busy" /></label>
          <div class="flex flex-wrap gap-2"><img v-for="id in page.source_image_ids" :key="id" :src="byId.has(id) ? imageUrl(byId.get(id)!) : ''" alt="本页参考素材" class="h-16 w-16 rounded object-contain" /></div>
        </article>
        <p class="muted">生成 {{ plan.pages.length }} 张新图，不立即替换当前草稿图片。</p>
        <div class="flex justify-end gap-3"><button class="btn btn-outline" type="button" :disabled="busy" @click="plan = null">返回调整</button><button class="btn btn-primary" type="submit" :disabled="busy">{{ busy ? '正在提交…' : `开始生成 ${plan.pages.length} 张` }}</button></div>
      </form>
      <form v-else class="space-y-5" @submit.prevent="prepare">
        <fieldset :disabled="busy" class="space-y-5">
          <legend class="mb-3 text-sm font-medium">参考素材（{{ sourceIds.length }} / 12）</legend>
          <div class="grid grid-cols-3 gap-3 sm:grid-cols-5">
            <label v-for="image in candidates" :key="image.id" class="relative cursor-pointer rounded-lg border border-slate-200 p-1 dark:border-dark-700"><img :src="imageUrl(image)" alt="套图参考素材" class="aspect-square w-full object-contain" /><input v-model="sourceIds" type="checkbox" :value="image.id" :aria-label="`套图参考 ${image.id}`" class="absolute left-2 top-2 h-5 w-5" /></label>
          </div>
          <label class="block text-sm font-medium">生成张数<select v-model.number="count" class="input mt-2"><option v-for="number in [2, 3, 4, 5, 6, 7, 8]" :key="number" :value="number">{{ number }} 张</option></select></label>
          <label class="block text-sm font-medium">补充要求（可选）<textarea v-model="prompt" class="input mt-2" rows="3" maxlength="4000" placeholder="例如：简洁明亮的风格，重点展示收纳方式和材质细节。" /></label>
          <p class="muted">目标语言：{{ draft.language }}。先识别商品与参考素材，规划主图、卖点、细节或场景内容；可在生成前调整方案。</p>
          <div class="flex justify-end"><button type="submit" class="btn btn-primary" :disabled="busy || !sourceIds.length || sourceIds.length > 12">{{ busy ? '正在规划…' : '自动规划套图' }}</button></div>
        </fieldset>
      </form>
    </div>
  </WorkspaceDialog>
</template>
