<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useClipboard } from '@/composables/useClipboard'
import { statusBadgeClass, workflowStatusLabel } from '@/utils/status'
import type { DraftIndexItem, Marketplace, MarketplaceOption, MarketplaceTargetSite } from '@/types/workflow'

const props = defineProps<{
  drafts: DraftIndexItem[]
  platformOptions: MarketplaceOption[]
  loading: boolean
  error?: string
}>()

const emit = defineEmits<{
  refresh: []
  editText: [item: DraftIndexItem]
  editImages: [item: DraftIndexItem]
  goPublish: [item: DraftIndexItem]
  deleteDraft: [item: DraftIndexItem]
  deleteDrafts: [items: DraftIndexItem[]]
  updateLanguage: [item: DraftIndexItem, language: string]
  updateTargets: [item: DraftIndexItem, targets: MarketplaceTargetSite[]]
}>()

const platformFilter = ref<'all' | Marketplace>('all')
const draftScope = ref<'active' | 'published' | 'all'>('active')
const selectedDraftIds = ref<string[]>([])
const copiedDraftId = ref('')
const editingTargetDraftId = ref('')
const editingTargetKeys = ref<string[]>([])
const targetEditError = ref('')
const { copy: copyToClipboard } = useClipboard()
let copiedDraftTimer: number | null = null
const activeDraftStatusSet = new Set(['claimed', 'copy_ready', 'images_ready', 'ready_to_publish', 'failed', 'not_ready'])
const draftStatusSet = new Set([...activeDraftStatusSet, 'published'])
const languageOptions = computed(() => {
  const languages = new Map<string, { value: string; siteCount: number }>()
  props.platformOptions.forEach((platform) => {
    platform.sites.forEach((site) => {
      const language = String(site.language || '').trim()
      if (!language) return
      const key = language.toLowerCase()
      const current = languages.get(key) || { value: language, siteCount: 0 }
      current.siteCount += 1
      languages.set(key, current)
    })
  })
  return Array.from(languages.values()).map((language) => ({
    ...language,
  }))
})
const allDraftRows = computed(() => props.drafts.filter((item) => {
  if (platformFilter.value !== 'all' && !draftMatchesPlatform(item, platformFilter.value)) return false
  return draftStatusSet.has(String(item.status || ''))
}))

const draftRows = computed(() => allDraftRows.value.filter((row) => {
  if (draftScope.value === 'published') return row.status === 'published'
  if (draftScope.value === 'all') return true
  return activeDraftStatusSet.has(String(row.status || ''))
}))

const activeDraftCount = computed(() => allDraftRows.value.filter((row) => activeDraftStatusSet.has(String(row.status || ''))).length)
const publishedDraftCount = computed(() => allDraftRows.value.filter((row) => row.status === 'published').length)
const selectedDrafts = computed(() => props.drafts.filter((item) => selectedDraftIds.value.includes(draftIdOf(item))))
const selectedCount = computed(() => selectedDrafts.value.length)
const visibleDraftIds = computed(() => draftRows.value.map(draftIdOf).filter(Boolean))
const allChecked = computed(() => visibleDraftIds.value.length > 0 && visibleDraftIds.value.every((id) => selectedDraftIds.value.includes(id)))

function draftIdOf(item: DraftIndexItem) {
  return String(item.draftId || '').trim()
}

function platformOption(platform: Marketplace) {
  return props.platformOptions.find((option) => option.key === platform)
}

function draftMatchesPlatform(item: DraftIndexItem, platform: Marketplace) {
  if (item.platform === platform || (item.platforms || []).includes(platform)) return true
  return (item.targetSites || []).some((target) => target.platform === platform)
}

function languageKey(language: string) {
  return String(language || '').trim().toLowerCase()
}

function targetKey(platform: Marketplace, site: string) {
  return `${platform}:${site}`.toLowerCase()
}

function targetLabel(platform: Marketplace, site: string) {
  const option = platformOption(platform)
  const selected = option?.sites.find((item) => item.code.toLowerCase() === String(site || '').toLowerCase())
  return selected ? `${option?.label} · ${selected.label}（${selected.code}）` : `${option?.label || platform} · ${site || '-'}`
}

function targetCompactLabel(platform: Marketplace, site: string) {
  const option = platformOption(platform)
  const selected = option?.sites.find((item) => item.code.toLowerCase() === String(site || '').toLowerCase())
  return selected ? `${option?.label} · ${selected.code}` : `${option?.label || platform} · ${site || '-'}`
}

function targetMeta(target: MarketplaceTargetSite) {
  return [target.site, target.language, target.currency].filter(Boolean).join(' / ')
}

function targetSitesForLanguage(language: string): MarketplaceTargetSite[] {
  const selectedLanguage = languageKey(language)
  if (!selectedLanguage) return []
  return props.platformOptions.flatMap((platform) => platform.sites
    .filter((site) => languageKey(site.language) === selectedLanguage)
    .map((site) => ({ platform: platform.key, site: site.code, language: site.language, currency: site.currency })))
}

function matchingTargetSites(item: DraftIndexItem): MarketplaceTargetSite[] {
  return targetSitesForLanguage(item.language)
}

function targetSites(item: DraftIndexItem): MarketplaceTargetSite[] {
  const allowedTargets = matchingTargetSites(item)
  const selectedKeys = new Set((item.targetSites || []).map((target) => targetKey(target.platform, target.site)))
  return allowedTargets.filter((target) => selectedKeys.has(targetKey(target.platform, target.site)))
}

function compactTargetLabels(item: DraftIndexItem) {
  const selectedTargets = targetSites(item)
  return selectedTargets.length
    ? selectedTargets.map((target) => targetCompactLabel(target.platform, target.site)).join('、')
    : '未选择市场'
}

function selectedTargetCount(item: DraftIndexItem) {
  return targetSites(item).length
}

function targetOptionCount(item: DraftIndexItem) {
  return matchingTargetSites(item).length
}

function selectedEditingCount(item: DraftIndexItem) {
  const allowedKeys = new Set(matchingTargetSites(item).map((target) => targetKey(target.platform, target.site)))
  return editingTargetKeys.value.filter((key) => allowedKeys.has(key)).length
}

function startTargetEdit(item: DraftIndexItem) {
  editingTargetDraftId.value = draftIdOf(item)
  const selectedTargets = targetSites(item)
  const fallbackTargets = selectedTargets.length ? selectedTargets : matchingTargetSites(item).slice(0, 1)
  editingTargetKeys.value = fallbackTargets.map((target) => targetKey(target.platform, target.site))
  targetEditError.value = ''
}

function toggleTarget(target: MarketplaceTargetSite, checked: boolean) {
  const key = targetKey(target.platform, target.site)
  editingTargetKeys.value = checked
    ? Array.from(new Set([...editingTargetKeys.value, key]))
    : editingTargetKeys.value.filter((value) => value !== key)
  targetEditError.value = ''
}

function saveTargets(item: DraftIndexItem) {
  const targets = matchingTargetSites(item).filter((target) => editingTargetKeys.value.includes(targetKey(target.platform, target.site)))
  if (!targets.length) {
    targetEditError.value = '至少选择一个与当前语言匹配的市场。'
    return
  }
  emit('updateTargets', item, targets)
  editingTargetDraftId.value = ''
  targetEditError.value = ''
}

function cancelTargetEdit() {
  editingTargetDraftId.value = ''
  editingTargetKeys.value = []
  targetEditError.value = ''
}

function changeLanguage(item: DraftIndexItem, language: string) {
  if (languageKey(language) === languageKey(item.language)) return
  if (editingTargetDraftId.value === draftIdOf(item)) cancelTargetEdit()
  emit('updateLanguage', item, language)
}

function toggleDraftSelection(item: DraftIndexItem, checked: boolean) {
  const draftId = draftIdOf(item)
  if (!draftId) return
  const exists = selectedDraftIds.value.includes(draftId)
  if (checked && !exists) selectedDraftIds.value.push(draftId)
  if (!checked) selectedDraftIds.value = selectedDraftIds.value.filter((id) => id !== draftId)
}

function draftRowKey(item: DraftIndexItem) {
  return draftIdOf(item) || `${item.sourceProductId || item.productId}:${item.platform}:${item.createdAt}`
}

function selectVisibleDrafts(checked: boolean) {
  if (!checked) {
    selectedDraftIds.value = selectedDraftIds.value.filter((id) => !visibleDraftIds.value.includes(id))
    return
  }
  selectedDraftIds.value = Array.from(new Set([...selectedDraftIds.value, ...visibleDraftIds.value]))
}

function deleteSelectedDrafts() {
  if (!selectedDrafts.value.length) return
  emit('deleteDrafts', selectedDrafts.value)
}

async function copyDraftId(item: DraftIndexItem) {
  const draftId = draftIdOf(item)
  if (!draftId) return
  await copyToClipboard(draftId)
  copiedDraftId.value = draftId
  if (copiedDraftTimer) window.clearTimeout(copiedDraftTimer)
  copiedDraftTimer = window.setTimeout(() => {
    copiedDraftId.value = ''
    copiedDraftTimer = null
  }, 1500)
}

watch(() => props.drafts.map(draftIdOf), (draftIds) => {
  const existingIds = new Set(draftIds)
  selectedDraftIds.value = selectedDraftIds.value.filter((id) => existingIds.has(id))
  if (editingTargetDraftId.value && !existingIds.has(editingTargetDraftId.value)) cancelTargetEdit()
})

onBeforeUnmount(() => {
  if (copiedDraftTimer) window.clearTimeout(copiedDraftTimer)
})
</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div class="min-w-0">
        <p class="text-xs font-semibold uppercase text-primary-600 dark:text-primary-300">草稿箱</p>
        <h2 class="mt-2 card-title">草稿箱</h2>
        <p class="muted mt-1">商品从母库复制到这里后独立编辑，来源商品只作为关联和参考。</p>
      </div>
      <div class="grid w-full gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] lg:w-auto">
        <select v-model="platformFilter" class="input sm:w-48">
          <option value="all">全部平台</option>
          <option v-for="platform in props.platformOptions" :key="platform.key" :value="platform.key">{{ platform.label }}</option>
        </select>
        <select v-model="draftScope" class="input sm:w-44">
          <option value="active">待编辑/待发布</option>
          <option value="published">已发布</option>
          <option value="all">全部草稿</option>
        </select>
        <button class="btn btn-outline" :disabled="props.loading" @click="emit('refresh')">刷新</button>
      </div>
    </div>

    <div class="mt-5 rounded-lg border border-accent-200 bg-accent-50 p-3 dark:border-dark-700 dark:bg-dark-950/70">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="text-sm text-accent-500 dark:text-accent-400">
          当前显示：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ draftRows.length }}</span> 条
          <span class="mx-2 text-accent-300 dark:text-dark-600">/</span>
          待处理：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ activeDraftCount }}</span>
          <span class="mx-2 text-accent-300 dark:text-dark-600">/</span>
          已发布：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ publishedDraftCount }}</span>
          <span class="mx-2 text-accent-300 dark:text-dark-600">/</span>
          已勾选：<span class="font-semibold text-accent-800 dark:text-accent-100">{{ selectedCount }}</span> 个
        </div>
        <button class="btn btn-outline px-3 py-1.5 text-xs text-rose-600 hover:border-rose-300 hover:bg-rose-50 dark:text-rose-200 dark:hover:border-rose-500/50 dark:hover:bg-rose-500/10" :disabled="props.loading || !selectedCount" @click="deleteSelectedDrafts">批量删除选中</button>
      </div>
    </div>
    <div v-if="props.error" class="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-medium text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
      {{ props.error }}
    </div>

    <div class="mt-5 overflow-x-auto rounded-lg border border-accent-200 dark:border-dark-700">
      <table class="w-full min-w-[1320px] table-fixed text-left text-sm">
        <colgroup>
          <col class="w-[52px]" />
          <col />
          <col class="w-[148px]" />
          <col class="w-[240px]" />
          <col class="w-[120px]" />
          <col />
          <col class="w-[340px]" />
        </colgroup>
        <thead class="border-b border-accent-200 bg-accent-50 text-xs text-accent-500 dark:border-dark-700 dark:bg-dark-950/70 dark:text-accent-400">
          <tr>
            <th class="p-3"><input class="size-4 rounded border-accent-300 text-primary-600" type="checkbox" aria-label="全选当前草稿" :checked="allChecked" :disabled="props.loading || !visibleDraftIds.length" @change="selectVisibleDrafts(($event.target as HTMLInputElement).checked)" /></th>
            <th class="p-3">商品</th>
            <th class="whitespace-nowrap p-3">语言</th>
            <th class="whitespace-nowrap p-3">市场</th>
            <th class="whitespace-nowrap p-3">草稿状态</th>
            <th class="p-3">来源商品</th>
            <th class="p-3">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-accent-100 dark:divide-dark-800">
          <tr v-for="row in draftRows" :key="draftRowKey(row)" class="align-top transition hover:bg-accent-50/70 dark:hover:bg-dark-800/60">
            <td class="p-3"><input class="size-4 rounded border-accent-300 text-primary-600" type="checkbox" aria-label="勾选草稿" :checked="selectedDraftIds.includes(draftIdOf(row))" :disabled="props.loading || !draftIdOf(row)" @change="toggleDraftSelection(row, ($event.target as HTMLInputElement).checked)" /></td>
            <td class="min-w-0 p-3">
              <div class="flex gap-3">
                <img v-if="row.mainImage" :src="row.mainImage" class="size-12 rounded-lg object-cover" />
                <div v-else class="flex size-12 shrink-0 items-center justify-center rounded-lg bg-accent-100 text-[10px] font-bold text-accent-500 dark:bg-dark-800 dark:text-accent-300">无图</div>
                <div class="min-w-0">
                  <div class="truncate font-semibold text-accent-950 dark:text-white">{{ row.title || row.productTitle || row.productId || '-' }}</div>
                  <div class="mt-1 truncate text-xs text-accent-500 dark:text-accent-400">{{ row.productTitle || row.sourceProductId || row.productId }}</div>
                </div>
              </div>
            </td>
            <td class="p-3 align-top">
              <select class="input h-9 w-28 min-w-0 py-1.5 text-xs sm:w-32" :value="row.language" :disabled="props.loading || !languageOptions.length" @change="changeLanguage(row, ($event.target as HTMLSelectElement).value)">
                <option value="" disabled>选择语言</option>
                <option v-for="language in languageOptions" :key="language.value" :value="language.value">{{ language.value }}</option>
              </select>
              <p class="mt-1 whitespace-nowrap text-[11px] text-accent-500 dark:text-accent-400">可选市场 {{ targetOptionCount(row) }} 个</p>
            </td>
            <td class="p-3 align-top">
              <div class="space-y-2">
                <button
                  type="button"
                  class="input flex h-9 w-56 max-w-full items-center justify-between gap-2 py-1.5 text-left text-xs"
                  :title="targetSites(row).map((target) => targetLabel(target.platform, target.site)).join('、') || compactTargetLabels(row)"
                  :disabled="props.loading || !targetOptionCount(row)"
                  :aria-expanded="editingTargetDraftId === draftIdOf(row)"
                  @click="editingTargetDraftId === draftIdOf(row) ? cancelTargetEdit() : startTargetEdit(row)"
                >
                  <span class="min-w-0 truncate">{{ compactTargetLabels(row) }}</span>
                  <span class="shrink-0 text-[11px] text-accent-400 dark:text-accent-500">{{ selectedTargetCount(row) }}/{{ targetOptionCount(row) }}</span>
                </button>
                <div v-if="editingTargetDraftId === draftIdOf(row)" class="rounded-lg border border-accent-200 bg-white p-2 text-xs shadow-sm dark:border-dark-700 dark:bg-dark-900">
                  <div class="mb-2 flex items-center justify-between gap-2 text-[11px] font-semibold text-accent-500 dark:text-accent-400">
                    <span>市场列表</span>
                    <span>{{ row.language || '-' }} · 已选 {{ selectedEditingCount(row) }}</span>
                  </div>
                  <div class="max-h-48 space-y-1 overflow-y-auto pr-1">
                    <label
                      v-for="target in matchingTargetSites(row)"
                      :key="targetKey(target.platform, target.site)"
                      class="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 transition hover:bg-accent-50 dark:hover:bg-dark-800"
                    >
                      <input
                        class="mt-0.5 size-4 rounded border-accent-300 text-primary-600"
                        type="checkbox"
                        :checked="editingTargetKeys.includes(targetKey(target.platform, target.site))"
                        :disabled="props.loading"
                        @change="toggleTarget(target, ($event.target as HTMLInputElement).checked)"
                      />
                      <span class="min-w-0">
                        <span class="block truncate font-semibold text-accent-800 dark:text-accent-100">{{ targetLabel(target.platform, target.site) }}</span>
                        <span class="block truncate text-[11px] text-accent-500 dark:text-accent-400">{{ targetMeta(target) }}</span>
                      </span>
                    </label>
                    <p v-if="!matchingTargetSites(row).length" class="rounded-md bg-rose-50 px-2 py-1.5 text-rose-700 dark:bg-rose-500/10 dark:text-rose-200">当前语言没有配置可选站点。</p>
                  </div>
                  <p v-if="targetEditError" class="mt-2 text-[11px] font-semibold text-rose-600 dark:text-rose-200">{{ targetEditError }}</p>
                  <div class="mt-2 flex gap-1.5">
                    <button class="btn btn-primary px-2 py-1 text-[11px]" :disabled="props.loading || !editingTargetKeys.length" @click="saveTargets(row)">保存</button>
                    <button class="btn btn-outline px-2 py-1 text-[11px]" :disabled="props.loading" @click="cancelTargetEdit">取消</button>
                  </div>
                </div>
              </div>
            </td>
            <td class="p-3"><span class="inline-flex max-w-full truncate" :class="statusBadgeClass(row.status)" :title="workflowStatusLabel(row.status)">{{ workflowStatusLabel(row.status) }}</span></td>
            <td class="min-w-0 p-3">
              <div class="truncate font-mono text-xs font-semibold text-accent-950 dark:text-white" :title="row.sourceProductId || row.productId">{{ row.sourceProductId || row.productId || '-' }}</div>
              <div class="mt-1 truncate text-xs text-accent-500 dark:text-accent-400" :title="row.sourceUrl">{{ row.sourcePlatform || '-' }} · {{ row.sourceUrl || '-' }}</div>
            </td>
            <td class="p-3">
              <div class="flex flex-wrap gap-2">
                <button class="btn btn-outline whitespace-nowrap px-3 py-1.5 text-xs" :disabled="props.loading" @click="emit('editText', row)">编辑文本</button>
                <button class="btn btn-secondary whitespace-nowrap px-3 py-1.5 text-xs" :disabled="props.loading" @click="emit('editImages', row)">编辑图片</button>
                <button class="btn btn-primary whitespace-nowrap px-3 py-1.5 text-xs" :disabled="props.loading" @click="emit('goPublish', row)">发布预检</button>
                <button class="btn btn-outline whitespace-nowrap px-3 py-1.5 text-xs" :disabled="props.loading || !draftIdOf(row)" :title="draftIdOf(row) || '当前草稿暂无 ID'" @click="copyDraftId(row)">
                  {{ copiedDraftId === draftIdOf(row) ? '已复制' : '复制草稿id' }}
                </button>
                <button class="btn btn-outline whitespace-nowrap px-3 py-1.5 text-xs text-rose-600 hover:border-rose-300 hover:bg-rose-50 dark:text-rose-200 dark:hover:border-rose-500/50 dark:hover:bg-rose-500/10" :disabled="props.loading" @click="emit('deleteDraft', row)">删除</button>
              </div>
            </td>
          </tr>
          <tr v-if="!draftRows.length">
            <td class="p-6 text-center text-accent-500 dark:text-accent-300" colspan="7">暂无草稿。可先从商品库勾选商品并推到草稿箱。</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
