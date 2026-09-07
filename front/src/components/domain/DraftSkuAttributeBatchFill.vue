<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useWorkflowStore } from '@/stores/workflow'
import { useWorkflowCatalogStore } from '@/stores/workflow/catalog'

const props = defineProps<{ targetKey: string; loading: boolean }>()
const { targetEditors } = storeToRefs(useWorkflowStore())
const { currentDraftProductContext } = storeToRefs(useWorkflowCatalogStore())
const editor = computed(() => targetEditors.value.find(item => item.key === props.targetKey))
const selected = computed(() => {
  const facts = new Map(currentDraftProductContext.value.skuItems.filter(sku => sku.active).map(sku => [sku.id, sku]))
  return (editor.value?.draft.skuItems || [])
    .filter(row => row.selected && facts.has(row.sku_id))
    .map(row => ({ id: row.sku_id, name: facts.get(row.sku_id)!.name || row.sku_id }))
})
type FillResult = { id: string; name: string; filled: number; review: string[]; warning: string; error: string }
const results = ref<FillResult[]>([])
const running = ref(false)
const stopRequested = ref(false)
const progress = ref('')
const summary = ref('')
const issueResults = computed(() => results.value.filter(item => item.review.length || item.warning || item.error))

watch([() => props.targetKey, () => editor.value?.draft.draftId], () => {
  stopRequested.value = true
  results.value = []
  summary.value = ''
})
onBeforeUnmount(() => { stopRequested.value = true })

async function fillSelected() {
  const current = editor.value
  if (!current || running.value || props.loading || current.state.busy || !selected.value.length || !current.draft.categoryId) return
  const items = [...selected.value]
  const categoryId = current.draft.categoryId
  const stillCurrent = () => current.attached() && editor.value === current && current.draft.categoryId === categoryId
  results.value = []
  summary.value = ''
  stopRequested.value = false
  running.value = true
  progress.value = '正在读取当前市场的 SKU 属性…'
  try {
    await current.run(async () => {
      if (!current.state.category?.fetchedAt || current.state.category.categoryId !== categoryId) {
        await current.actions.loadCategoryAttributes()
      }
      if (!stillCurrent()) return
      if (stopRequested.value) {
        summary.value = '批量填写已停止，尚未开始填写 SKU。'
        return
      }
      const category = current.state.category
      if (!category?.fetchedAt || category.categoryId !== categoryId || current.state.categoryAttributeError) {
        summary.value = current.state.categoryAttributeError || current.state.error || '平台属性读取失败，请重试。'
        return
      }
      const attributes = [...category.requiredAttributes, ...category.optionalAttributes]
        .filter(attr => attr.variationRole === 'variant' && !attr.readOnly && attr.managedBy !== 'listing_grouping')
      if (!attributes.length) {
        summary.value = current.target.platform === 'mercadolibre'
          ? '当前类目没有预定义的 SKU 差异字段。使用 Mercado User Products 时，可以在各 SKU 的“属性 / 详情”添加自定义规格。'
          : '当前类目没有可填写的 SKU 差异属性，请到“类目/属性”核对类目。'
        return
      }
      progress.value = '正在复用并翻译来源规格，相同文本只翻译一次…'
      const sourceResult = await current.actions.fillAttributesByAi('', true)
      if (!stillCurrent()) return
      if (!sourceResult) {
        summary.value = current.state.error || '来源规格复用失败，请重试。'
        return
      }
      const labels = new Map(attributes.map(attr => [attr.id, current.state.categoryAttributeTranslations[attr.id]?.label || attr.name || attr.id]))
      // 只编排已有业务请求；每个 SKU 仍独立取证、校验和保存，不复制其他规格的结果。
      for (const [index, item] of items.entries()) {
        if (stopRequested.value || !stillCurrent()) break
        progress.value = `正在填写 ${index + 1} / ${items.length}：${item.name}`
        const result = await current.actions.fillAttributesByAi(item.id)
        if (!stillCurrent()) return
        results.value.push({
          ...item, filled: result?.filledCount || 0,
          review: (result?.needReview || []).map(value => {
            const id = String(value)
            return labels.get(id) || id
          }),
          warning: result?.warning || '',
          error: result ? '' : current.state.error || '此次填写未完成，请重试。',
        })
        if (!result) break
      }
      if (!stillCurrent()) return
      const saved = results.value.filter(item => !item.error).length
      const filled = sourceResult.filledCount + results.value.reduce((total, item) => total + item.filled, 0)
      const review = results.value.reduce((total, item) => total + item.review.length, 0)
      const finished = saved === items.length
      summary.value = `${sourceResult.filledCount ? `来源复用已保存 ${sourceResult.filledCount} 项。` : ''}${finished ? '批量填写完成' : '批量填写已停止'}：已保存 ${saved} / ${items.length} 个 SKU，新增 ${filled} 项属性，${review} 项待复核。${finished ? '' : '再次点击可继续补齐空缺，已填内容会保留。'}`
    })
  } finally {
    progress.value = ''
    running.value = false
  }
}
</script>

<template>
  <div class="rounded-lg border border-accent-200 p-4 dark:border-dark-700" data-testid="sku-batch-attributes">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h4 class="font-semibold">批量填写 SKU 属性</h4>
        <p class="muted mt-1">先复用并翻译来源颜色、尺码，再为 {{ selected.length }} 个 SKU 补齐必要的平台属性。保留已填内容，可选字段不要求全部填写。</p>
      </div>
      <button class="btn btn-primary" :disabled="loading || running || editor?.state.busy || !editor?.draft.categoryId || !selected.length" @click="fillSelected">一键 AI 填写 SKU 属性（{{ selected.length }}）</button>
    </div>
    <p v-if="!editor?.draft.categoryId" class="mt-2 text-sm text-amber-700">请先在“类目/属性”为当前市场选择类目。</p>
    <div v-if="running" class="mt-3 flex flex-wrap items-center gap-3">
      <p role="status" class="text-sm">{{ progress }}。每个 SKU 完成后自动保存。</p>
      <button class="btn btn-outline" :disabled="stopRequested" @click="stopRequested = true">{{ stopRequested ? '正在停止…' : '完成当前 SKU 后停止' }}</button>
    </div>
    <p v-if="summary" class="mt-3 text-sm" role="status">{{ summary }}</p>
    <ul v-if="issueResults.length" class="mt-3 space-y-2 text-sm" aria-label="SKU 属性待复核结果">
      <li v-for="item in issueResults" :key="item.id" class="rounded bg-amber-50 p-3 text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
        <span class="font-medium">{{ item.name }}</span>
        <p v-if="item.review.length">待复核：{{ item.review.join('、') }}</p>
        <p v-if="item.warning" class="whitespace-pre-line">{{ item.warning }}</p>
        <p v-if="item.error" class="whitespace-pre-line">{{ item.error }}</p>
      </li>
    </ul>
  </div>
</template>
