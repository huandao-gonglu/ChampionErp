<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { useMediaQuery } from '@vueuse/core'
import {
  PhArrowLeft,
  PhArrowRight,
  PhArrowsClockwise,
  PhBrowsers,
  PhCheckCircle,
  PhFolderOpen,
  PhGearSix,
  PhInfo,
  PhLightning,
  PhLink,
  PhPlay,
  PhPlus,
  PhShieldWarning,
  PhSlidersHorizontal,
  PhSpinnerGap,
  PhTrash,
  PhWarningCircle,
  PhX,
} from '@phosphor-icons/vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { manageLinks, type CollectionLink, type LinkMode } from '@/api/workflow/collectionLinks'
import { useWorkflowCollectionStore } from '@/stores/workflow/collection'
import { businessStorageKey } from '@/utils/accountTransport'
import { desktopOperationReason, hasLocalOperations } from '@/utils/desktop'
import { launchDesktop } from '@/utils/desktopHandoff'
import { normalizeCollectUrl } from '@/utils/collectQueue'
import type { CollectBatchStatus, CollectForm } from '@/types/workflow'
import CollectBatchManager from './CollectBatchManager.vue'
import CollectLinkDetail from './CollectLinkDetail.vue'

const DEFAULT_MODE_STORAGE_KEY = 'champion-erp.collect-default-mode.v1'

const props = defineProps<{ form: CollectForm; loading: boolean; error: string }>()
const emit = defineEmits<{ openLibrary: []; openProduct: [id: string]; saveSettings: [] }>()
const store = useWorkflowCollectionStore()
const splitView = useMediaQuery('(min-width: 1280px)')
const selectedId = ref('')
const selected = computed(() => store.links.find(row => row.id === selectedId.value))
const detailMode = ref<'auto' | 'open' | 'closed'>('auto')
const showDetail = computed(() => !!selected.value && (detailMode.value === 'open' || (detailMode.value === 'auto' && splitView.value)))
const replacingList = computed(() => !splitView.value && showDetail.value)
const listPane = ref<HTMLElement>()
const detailPane = ref<HTMLElement>()
const backButton = ref<HTMLButtonElement>()
let listScrollTop = 0
let documentScrollTop = 0

const search = ref('')
const status = ref<CollectBatchStatus | ''>('')
const modeFilter = ref<LinkMode | ''>('')
const sort = ref<'none' | 'ascending' | 'descending'>('none')
const timeSort = ref<'newest' | 'oldest'>('newest')
const checkedIds = ref(new Set<string>())
const collator = new Intl.Collator('zh-CN', { numeric: true, sensitivity: 'base' })

function createdTimestamp(row: CollectionLink) {
  if (!row.createdAt) return 0
  const parsed = Date.parse(row.createdAt)
  return Number.isFinite(parsed) ? parsed : 0
}

const matching = computed(() => {
  const term = search.value.trim().toLowerCase()
  return store.links.filter(row => {
    if (modeFilter.value && row.mode !== modeFilter.value) return false
    return !term || row.title?.toLowerCase().includes(term) || row.url.toLowerCase().includes(term)
  })
})
const counts = computed(() => {
  const result: Partial<Record<CollectBatchStatus, number>> = {}
  for (const row of matching.value) result[row.status] = (result[row.status] || 0) + 1
  return result
})
const visibleRows = computed(() => {
  const rows = status.value ? matching.value.filter(row => row.status === status.value) : matching.value
  if (sort.value !== 'none') {
    const direction = sort.value === 'ascending' ? 1 : -1
    return [...rows].sort((a, b) => direction * collator.compare(a.title || a.url, b.title || b.url))
  }
  const hasTimestamps = rows.some(row => Boolean(row.createdAt))
  if (!hasTimestamps) return rows
  const indexed = rows.map((row, index) => ({ row, index, ts: createdTimestamp(row) }))
  const direction = timeSort.value === 'newest' ? -1 : 1
  indexed.sort((a, b) => (a.ts === b.ts ? a.index - b.index : direction * (a.ts - b.ts)))
  return indexed.map(item => item.row)
})

const batchRows = computed(() => checkedIds.value.size
  ? visibleRows.value.filter(row => checkedIds.value.has(row.id))
  : visibleRows.value)
const pendingRows = computed(() => batchRows.value.filter(row => row.status === 'pending' || row.status === 'waiting_verification'))
const failedRows = computed(() => batchRows.value.filter(row => row.status === 'failed'))
const removableCheckedRows = computed(() => visibleRows.value.filter(row => checkedIds.value.has(row.id) && row.status !== 'running'))
const completedVisibleRows = computed(() => visibleRows.value.filter(row => row.status === 'success'))
const activeRunningRow = computed(() => {
  const progressId = store.runProgress?.currentId
  if (progressId) {
    const found = store.links.find(row => row.id === progressId)
    if (found) return found
  }
  return store.links.find(row => row.status === 'running')
})
const hasActiveWork = computed(() => store.running || Boolean(store.waitingId) || store.links.some(row => row.status === 'running' || row.status === 'waiting_verification'))

const dialog = ref<'' | 'add' | 'edit' | 'remove' | 'removeBatch' | 'modeBatch' | 'settings'>('')
const dialogFormId = useId()
const original = ref<CollectionLink>()
const batchTargets = ref<CollectionLink[]>([])
const batchActionLabel = ref('')
const urlInput = ref('')
const quickUrl = ref('')
const quickAdding = ref(false)
const mode = ref<LinkMode>('browser')
const saving = ref(false)
const batchProgress = ref<{ done: number; total: number } | null>(null)
const manualRefreshing = ref(false)
const busy = computed(() => saving.value || quickAdding.value || store.running || props.loading)
const dialogError = ref('')
const notice = ref('')
const actionError = ref('')
const activeError = computed(() => actionError.value || store.runError || store.syncError || props.error)
const inputLineCount = computed(() => urlInput.value.split(/\r?\n/).map(line => line.trim()).filter(Boolean).length)
const currentMode = computed<LinkMode>({
  get: () => (props.form.mode === 'api' ? 'api' : 'browser'),
  set: (value) => {
    props.form.mode = value
    try { localStorage.setItem(businessStorageKey(DEFAULT_MODE_STORAGE_KEY), value) } catch { /* 忽略本地存储不可用 */ }
  },
})

const dialogTitles: Record<Exclude<typeof dialog.value, ''>, string> = {
  add: '添加链接',
  edit: '编辑链接',
  remove: '移除链接',
  removeBatch: '批量移除链接',
  modeBatch: '批量修改采集方式',
  settings: '采集设置',
}

let timer: ReturnType<typeof setTimeout> | undefined
let refreshing = false

function safeHost(url: string) {
  try { return new URL(url).hostname } catch { return url }
}

function keepSelectionVisible() {
  if (!visibleRows.value.some(row => row.id === selectedId.value)) selectedId.value = visibleRows.value[0]?.id || ''
}
function focusListSelection() {
  if (dialog.value) return
  const panel = listPane.value
  const focusTarget = panel?.querySelector<HTMLButtonElement>('[data-collect-row][aria-pressed="true"]') || panel
  focusTarget?.focus({ preventScroll: true })
}
watch([visibleRows, splitView], () => {
  if (!splitView.value && detailMode.value === 'open') {
    if (selected.value) return
    detailMode.value = 'closed'
  }
  keepSelectionVisible()
}, { immediate: true })
watch(() => store.links.map(row => row.id), ids => {
  if (!checkedIds.value.size) return
  const available = new Set(ids)
  for (const id of checkedIds.value) {
    if (!available.has(id)) checkedIds.value.delete(id)
  }
})
watch(replacingList, async (replacing, _, onCleanup) => {
  let current = true
  onCleanup(() => { current = false })
  if (replacing) {
    listScrollTop = listPane.value?.scrollTop || 0
    documentScrollTop = window.scrollY
  }
  await nextTick()
  if (!current) return
  if (replacing) {
    if (window.scrollY) window.scrollTo(0, 0)
    if (!dialog.value) backButton.value?.focus({ preventScroll: true })
    return
  }
  const panel = listPane.value
  if (panel) panel.scrollTop = listScrollTop
  if (window.scrollY !== documentScrollTop) window.scrollTo(0, documentScrollTop)
  focusListSelection()
})
watch(selectedId, () => {
  if (detailPane.value) detailPane.value.scrollTop = 0
})

function openDetail(id: string) {
  selectedId.value = id
  detailMode.value = 'open'
}
function closeDetail() {
  const wasReplacing = replacingList.value
  detailMode.value = 'closed'
  keepSelectionVisible()
  if (!wasReplacing) void nextTick(focusListSelection)
}
function checkRow(id: string, checked: boolean) {
  if (checked) checkedIds.value.add(id)
  else checkedIds.value.delete(id)
}
function checkVisible(checked: boolean) {
  for (const row of visibleRows.value) checkRow(row.id, checked)
}

function scheduleNextPoll() {
  clearTimeout(timer)
  const delayMs = hasActiveWork.value ? 5000 : 30000
  timer = setTimeout(() => {
    if (!document.hidden) void refresh()
    else scheduleNextPoll()
  }, delayMs)
}

async function refresh(manual = false) {
  if (refreshing) return
  refreshing = true
  if (manual) manualRefreshing.value = true
  try { await store.refreshLinks() } catch { /* 状态栏显示同步错误。 */ }
  finally {
    refreshing = false
    manualRefreshing.value = false
    scheduleNextPoll()
  }
}
function refreshVisible() { if (!document.hidden) void refresh() }

watch(hasActiveWork, () => { scheduleNextPoll() })

onMounted(() => {
  try {
    const savedMode = localStorage.getItem(businessStorageKey(DEFAULT_MODE_STORAGE_KEY))
    if (savedMode === 'api' || savedMode === 'browser') props.form.mode = savedMode
  } catch { /* 忽略本地存储不可用 */ }
  void refresh()
  window.addEventListener('focus', refreshVisible)
  document.addEventListener('visibilitychange', refreshVisible)
})
onBeforeUnmount(() => {
  clearTimeout(timer)
  window.removeEventListener('focus', refreshVisible)
  document.removeEventListener('visibilitychange', refreshVisible)
})

function show(kind: 'add' | 'edit' | 'remove' | 'removeBatch' | 'modeBatch' | 'settings', customTargets?: CollectionLink[], customLabel = '') {
  original.value = selected.value
  batchTargets.value = customTargets ? [...customTargets] : (kind === 'removeBatch' || kind === 'modeBatch') ? [...removableCheckedRows.value] : []
  batchActionLabel.value = customLabel
  mode.value = kind === 'edit' && original.value ? original.value.mode : currentMode.value
  urlInput.value = kind === 'edit' ? original.value?.url || '' : ''
  dialogError.value = ''
  batchProgress.value = null
  dialog.value = kind
}

function handleQuickPaste(event: ClipboardEvent) {
  const text = event.clipboardData?.getData('text')
  if (!text || !/\r?\n/.test(text)) return
  const urlMatches = text.match(/https?:\/\//gi) || []
  event.preventDefault()
  if (urlMatches.length >= 2) {
    show('add')
    urlInput.value = text.trim()
    return
  }
  quickUrl.value = text.replace(/\r?\n+/g, ' ').replace(/\s{2,}/g, ' ').trim()
}

async function highlightNewestAfterAdd(previousIds: Set<string>) {
  await store.refreshLinks().catch(() => undefined)
  const newlyAdded = visibleRows.value.find(row => !previousIds.has(row.id))
  if (newlyAdded) {
    selectedId.value = newlyAdded.id
    if (listPane.value) listPane.value.scrollTop = 0
  }
}

async function quickAdd() {
  const raw = quickUrl.value.trim()
  if (!raw || busy.value || !store.ready) return
  quickAdding.value = true
  actionError.value = ''
  notice.value = ''
  const previousIds = new Set(store.links.map(row => row.id))
  try {
    const result = await manageLinks({ action: 'add', items: [{ url: raw, mode: currentMode.value }] })
    quickUrl.value = ''
    notice.value = `已添加 ${result.added ?? 0} 条，跳过 ${result.duplicates ?? 0} 条重复链接。`
    await highlightNewestAfterAdd(previousIds)
  } catch (e) {
    actionError.value = (e as Error).message
  } finally {
    quickAdding.value = false
  }
}

async function save() {
  if (saving.value) return
  saving.value = true
  dialogError.value = ''
  const previousIds = new Set(store.links.map(row => row.id))
  try {
    const row = original.value
    if (dialog.value === 'add') {
      const result = await manageLinks({ action: 'add', items: [{ url: urlInput.value.trim(), mode: mode.value }] })
      notice.value = `已添加 ${result.added} 条，跳过 ${result.duplicates} 条重复链接。`
      dialog.value = ''
      await highlightNewestAfterAdd(previousIds)
      return
    } else if (row && dialog.value === 'edit') {
      await manageLinks({ action: 'edit', id: row.id, revision: row.revision, url: normalizeCollectUrl(urlInput.value), mode: mode.value })
      notice.value = '已保存链接设置。'
    } else if (row && dialog.value === 'remove') {
      await manageLinks({ action: 'remove', id: row.id, revision: row.revision })
      checkedIds.value.delete(row.id)
      notice.value = '已移除链接。'
    } else if (dialog.value === 'removeBatch') {
      let removed = 0
      let skipped = 0
      const total = batchTargets.value.length
      batchProgress.value = { done: 0, total }
      for (const target of batchTargets.value) {
        try {
          await manageLinks({ action: 'remove', id: target.id, revision: target.revision })
          checkedIds.value.delete(target.id)
          removed++
        } catch {
          skipped++
        }
        batchProgress.value = { done: removed + skipped, total }
      }
      notice.value = skipped ? `已移除 ${removed} 条链接，跳过 ${skipped} 条已变更的链接。` : `已批量移除 ${removed} 条链接。`
    } else if (dialog.value === 'modeBatch') {
      let updated = 0
      let unchanged = 0
      let skipped = 0
      const total = batchTargets.value.length
      batchProgress.value = { done: 0, total }
      for (const target of batchTargets.value) {
        if (target.mode === mode.value) {
          unchanged++
        } else {
          try {
            await manageLinks({ action: 'edit', id: target.id, revision: target.revision, url: target.url, mode: mode.value })
            updated++
          } catch {
            skipped++
          }
        }
        batchProgress.value = { done: updated + unchanged + skipped, total }
      }
      const modeName = mode.value === 'api' ? '1688 API 采集' : '浏览器采集'
      const parts = [`已将 ${updated} 条链接切换为${modeName}`]
      if (unchanged) parts.push(`${unchanged} 条原本即为此方式`)
      if (skipped) parts.push(`跳过 ${skipped} 条已变更链接`)
      notice.value = parts.join('，') + '。'
    }
    dialog.value = ''
    await store.refreshLinks().catch(() => undefined)
  } catch (e) {
    dialogError.value = (e as Error).message
    if ((e as { status?: number }).status === 409) await refresh()
  } finally {
    batchProgress.value = null
    saving.value = false
  }
}

function run(rows: CollectionLink[]) {
  if (busy.value || !rows.length) return
  actionError.value = ''
  notice.value = ''
  const targets = rows.map(({ id, revision }) => ({ id, revision }))
  if (rows.some(row => row.mode === 'browser')) {
    if (!hasLocalOperations()) {
      void launchDesktop({ target: '/?tab=collect', state: { workflow: { collectAction: { type: 'batch', targets } } } })
      return
    }
    actionError.value = desktopOperationReason()
    if (actionError.value) return
  }
  void store.runLinks(targets)
}
function runRow(row: CollectionLink) {
  selectedId.value = row.id
  run([row])
}
</script>

<template>
  <div class="collection-page">
    <PageHeader title="采集商品" class="collection-heading">
      <template #leading>
        <button v-if="replacingList" ref="backButton" type="button" class="btn btn-outline collection-action" @click="closeDetail">
          <PhArrowLeft :size="16" aria-hidden="true" />返回列表
        </button>
      </template>
      <template #actions>
        <div class="flex flex-wrap items-center justify-end gap-2">
          <template v-if="!replacingList">
            <button type="button" class="btn btn-primary collection-action tabular-nums" :disabled="busy || !pendingRows.length" @click="run(pendingRows)">
              <PhSpinnerGap v-if="store.running" :size="16" class="animate-spin" aria-hidden="true" />
              <PhPlay v-else :size="15" weight="fill" aria-hidden="true" />
              开始待采集（{{ pendingRows.length }}）
            </button>
            <button
              type="button"
              class="btn btn-outline collection-action tabular-nums"
              :class="failedRows.length ? 'border-rose-200 text-rose-700 hover:bg-rose-50 dark:border-rose-500/30 dark:text-rose-300 dark:hover:bg-rose-500/10' : ''"
              :disabled="busy || !failedRows.length"
              @click="run(failedRows)"
            >
              <PhArrowsClockwise :size="16" aria-hidden="true" />重试失败（{{ failedRows.length }}）
            </button>
            <button
              type="button"
              class="btn btn-outline collection-action border-primary-300/90 text-primary-700 hover:bg-primary-50 dark:border-primary-500/40 dark:text-primary-300 dark:hover:bg-primary-500/10"
              :disabled="busy || !store.ready"
              @click="show('add')"
            >
              <PhPlus :size="16" weight="bold" aria-hidden="true" />添加链接
            </button>
            <button
              type="button"
              class="btn btn-outline collection-action !px-2.5"
              aria-label="刷新链接列表"
              title="刷新链接列表"
              @click="refresh(true)"
            >
              <PhArrowsClockwise :size="16" :class="{ 'animate-spin': manualRefreshing }" aria-hidden="true" />
            </button>
          </template>
          <button type="button" class="btn btn-outline collection-action" @click="emit('openLibrary')">
            采集箱 <PhArrowRight :size="15" aria-hidden="true" />
          </button>
        </div>
      </template>
    </PageHeader>

    <div class="collection-card">
      <!-- 紧凑快速粘贴与默认方式工具栏 -->
      <div
        v-show="!replacingList"
        class="flex shrink-0 flex-wrap items-center gap-2.5 border-b border-accent-200/80 bg-accent-50/70 px-4 py-2.5 dark:border-dark-700 dark:bg-dark-900/50"
      >
        <div
          class="inline-flex shrink-0 items-center rounded-xl border border-accent-200/90 bg-accent-100/90 p-0.5 dark:border-dark-700 dark:bg-dark-950/70"
          role="group"
          aria-label="新链接默认采集方式"
          title="仅决定新加入链接的默认方式；批量修改已有链接请勾选后点击「改方式」"
        >
          <button
            type="button"
            class="mode-segment"
            :aria-pressed="currentMode === 'browser'"
            @click="currentMode = 'browser'"
          >
            <PhBrowsers :size="14" aria-hidden="true" />浏览器采集
          </button>
          <button
            type="button"
            class="mode-segment"
            :aria-pressed="currentMode === 'api'"
            @click="currentMode = 'api'"
          >
            <PhLightning :size="14" aria-hidden="true" />1688 API
          </button>
        </div>

        <div class="flex min-w-[240px] max-w-2xl flex-1 items-center gap-2" role="group" aria-label="快速添加链接">
          <div class="relative min-w-0 flex-1">
            <PhLink :size="15" aria-hidden="true" class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-accent-400" />
            <input
              v-model="quickUrl"
              type="text"
              :disabled="busy || !store.ready"
              aria-label="快速粘贴商品链接或 1688 分享文案"
              placeholder="粘贴商品链接或 1688 分享文案回车加入（粘贴多条自动展开批量框）…"
              class="input h-8 !rounded-xl !py-0 !pl-8 !pr-7 text-xs shadow-sm sm:text-sm"
              @paste="handleQuickPaste"
              @keydown.enter.prevent="quickAdd"
            />
            <button
              v-if="quickUrl"
              type="button"
              aria-label="清空快速输入"
              class="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1 text-accent-400 hover:bg-accent-100 hover:text-accent-700 dark:hover:bg-dark-800 dark:hover:text-accent-200"
              @click="quickUrl = ''"
            >
              <PhX :size="12" aria-hidden="true" />
            </button>
          </div>
          <button
            type="button"
            class="btn btn-outline !min-h-8 shrink-0 !rounded-xl !px-3 !py-1 text-xs"
            :disabled="busy || !store.ready || !quickUrl.trim()"
            @click="quickAdd"
          >
            <PhSpinnerGap v-if="quickAdding" :size="14" class="animate-spin" aria-hidden="true" />
            <PhPlus v-else :size="14" weight="bold" aria-hidden="true" />
            {{ quickAdding ? '加入中…' : '快速加入' }}
          </button>
        </div>

        <button
          type="button"
          class="collection-tool ml-auto shrink-0"
          :title="form.outputDir ? `输出目录：${form.outputDir}` : '配置采集输出目录'"
          @click="show('settings')"
        >
          <PhGearSix :size="15" aria-hidden="true" />
          <span>采集设置</span>
          <span v-if="form.outputDir" class="size-1.5 rounded-full bg-primary-500" aria-hidden="true" />
        </button>
      </div>

      <!-- 状态与反馈提示条（不与 footer 重复） -->
      <div v-if="activeError || store.waitingId || store.running || notice" class="shrink-0 space-y-2 px-4 pt-2.5">
        <div
          v-if="activeError"
          role="alert"
          class="flex items-start justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2 text-sm text-rose-800 [overflow-wrap:anywhere] dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
        >
          <div class="flex min-w-0 items-start gap-2">
            <PhWarningCircle :size="17" weight="fill" class="mt-0.5 shrink-0 text-rose-500 dark:text-rose-400" aria-hidden="true" />
            <span class="min-w-0">{{ activeError }}</span>
          </div>
          <button v-if="actionError" type="button" aria-label="关闭错误提示" class="shrink-0 rounded-md p-0.5 opacity-70 hover:opacity-100" @click="actionError = ''">
            <PhX :size="14" aria-hidden="true" />
          </button>
        </div>

        <div
          v-if="store.waitingId"
          role="status"
          class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
        >
          <div class="flex min-w-0 items-center gap-2.5">
            <PhShieldWarning :size="17" weight="fill" class="shrink-0 animate-pulse text-amber-600 dark:text-amber-400" aria-hidden="true" />
            <span>请在原商品页完成验证，完成后会继续采集。</span>
          </div>
          <button
            type="button"
            class="rounded-lg border border-amber-300 bg-white/80 px-2.5 py-1 text-xs font-semibold text-amber-900 transition hover:bg-white dark:border-amber-500/40 dark:bg-dark-900/60 dark:text-amber-200 dark:hover:bg-dark-800"
            @click="store.cancelWait"
          >
            取消等待
          </button>
        </div>

        <div
          v-else-if="store.running"
          role="status"
          class="space-y-1.5 rounded-xl border border-blue-200 bg-blue-50/90 px-3.5 py-2 text-xs text-blue-900 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-200"
        >
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex min-w-0 items-center gap-2">
              <PhSpinnerGap :size="16" class="shrink-0 animate-spin text-blue-600 dark:text-blue-400" aria-hidden="true" />
              <span class="truncate font-medium">
                <template v-if="store.runProgress">正在采集（第 {{ store.runProgress.current }} / {{ store.runProgress.total }} 条）</template>
                <template v-else>正在采集</template>
                {{ activeRunningRow ? `：${activeRunningRow.title || safeHost(activeRunningRow.url)}` : '…' }}
              </span>
            </div>
            <button
              type="button"
              class="shrink-0 rounded-lg border border-blue-300 bg-white/80 px-2.5 py-0.5 text-xs font-semibold text-blue-800 transition hover:bg-white disabled:opacity-60 dark:border-blue-500/40 dark:bg-dark-900/60 dark:text-blue-200 dark:hover:bg-dark-800"
              :disabled="store.stopRequested"
              @click="store.stopRun"
            >
              {{ store.stopRequested ? '将在本条完成后停止…' : '停止后续采集' }}
            </button>
          </div>
          <div v-if="store.runProgress && store.runProgress.total > 1" class="h-1 w-full overflow-hidden rounded-full bg-blue-200/80 dark:bg-blue-500/20">
            <div
              class="h-full rounded-full bg-blue-600 transition-all duration-300 dark:bg-blue-400"
              :style="{ width: `${Math.round((store.runProgress.current / store.runProgress.total) * 100)}%` }"
            />
          </div>
        </div>

        <div
          v-if="notice && !activeError"
          role="status"
          class="flex items-center justify-between gap-2 rounded-xl border border-primary-200/80 bg-primary-50/80 px-3.5 py-1.5 text-xs font-medium text-primary-800 dark:border-primary-500/25 dark:bg-primary-500/10 dark:text-primary-200"
        >
          <div class="flex min-w-0 items-center gap-2">
            <PhCheckCircle :size="15" weight="fill" class="shrink-0 text-primary-600 dark:text-primary-400" aria-hidden="true" />
            <span>{{ notice }}</span>
          </div>
          <button type="button" aria-label="关闭通知" class="shrink-0 rounded-md p-0.5 opacity-70 hover:opacity-100" @click="notice = ''">
            <PhX :size="13" aria-hidden="true" />
          </button>
        </div>
      </div>

      <!-- 主内容区：列表 + 嵌入式详情 -->
      <div class="collection-panes" :class="{ 'collection-panes--split': splitView && showDetail }">
        <div v-show="!replacingList" ref="listPane" class="collection-pane" role="region" aria-label="采集链接列表" tabindex="0">
          <CollectBatchManager
            v-model:search="search"
            v-model:status="status"
            v-model:mode-filter="modeFilter"
            v-model:sort="sort"
            v-model:time-sort="timeSort"
            :rows="visibleRows"
            :total="store.links.length"
            :counts="counts"
            :ready="store.ready"
            :selected-id="selectedId"
            :checked-ids="checkedIds"
            :busy="busy"
            @select="openDetail"
            @check="checkRow"
            @check-all="checkVisible"
            @clear-checks="checkedIds.clear()"
            @run="runRow"
            @run-batch="run"
            @change-mode-checked="show('modeBatch')"
            @remove-checked="show('removeBatch')"
            @request-add="show('add')"
            @open-product="emit('openProduct', $event)"
          />
        </div>
        <div v-if="showDetail" ref="detailPane" class="collection-pane collection-pane--detail" role="region" aria-label="链接详情" tabindex="0">
          <CollectLinkDetail
            :row="selected"
            :busy="busy"
            :closable="splitView"
            @close="closeDetail"
            @edit="show('edit')"
            @remove="show('remove')"
            @run="runRow"
            @open-product="emit('openProduct', $event)"
          />
        </div>
      </div>

      <!-- 精简底部同步栏 + 清理已完成入口 -->
      <footer class="flex shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-accent-200/80 bg-accent-50/60 px-4 py-2 text-xs text-accent-600 dark:border-dark-700 dark:bg-dark-900/40 dark:text-accent-300">
        <div class="flex items-center gap-2">
          <span
            class="size-2 shrink-0 rounded-full"
            :class="store.syncError ? 'bg-rose-500' : store.ready ? 'bg-primary-500' : 'animate-pulse bg-amber-400'"
            aria-hidden="true"
          />
          <span role="status">{{ store.syncError ? '同步失败' : store.ready ? '链接已同步到当前账号' : '正在同步链接…' }}</span>
        </div>
        <button
          v-if="completedVisibleRows.length && !replacingList"
          type="button"
          class="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium text-accent-500 transition hover:bg-accent-200/60 hover:text-rose-700 disabled:opacity-45 dark:text-accent-400 dark:hover:bg-white/10 dark:hover:text-rose-300"
          :disabled="busy"
          @click="show('removeBatch', completedVisibleRows, '已入采集箱的')"
        >
          <PhTrash :size="13" aria-hidden="true" />清理已入箱（{{ completedVisibleRows.length }}）
        </button>
      </footer>
    </div>

    <!-- 弹窗：添加 / 编辑 / 移除 / 批量移除 / 批量改方式 / 设置 -->
    <WorkspaceDialog
      v-if="dialog"
      :open="true"
      :title="dialogTitles[dialog]"
      width="34rem"
      :close-disabled="saving"
      @close="dialog = ''"
    >
      <template #close>取消</template>
      <template #header-actions>
        <button
          type="submit"
          :form="dialogFormId"
          class="btn collection-action"
          :class="dialog === 'remove' || dialog === 'removeBatch' ? 'bg-rose-600 text-white hover:bg-rose-700 dark:bg-rose-500 dark:hover:bg-rose-400' : 'btn-primary'"
          :disabled="saving || (dialog === 'settings' ? loading : (dialog === 'add' || dialog === 'edit') && !urlInput.trim()) || ((dialog === 'removeBatch' || dialog === 'modeBatch') && !batchTargets.length)"
        >
          {{
            saving
              ? (dialog === 'add'
                ? '正在添加…'
                : dialog === 'remove' || dialog === 'removeBatch'
                  ? (batchProgress ? `正在移除（${batchProgress.done}/${batchProgress.total}）…` : '正在移除…')
                  : (batchProgress ? `正在更新（${batchProgress.done}/${batchProgress.total}）…` : '正在保存…'))
              : dialog === 'remove' || dialog === 'removeBatch'
                ? '确认移除'
                : dialog === 'add'
                  ? '添加到列表'
                  : '保存'
          }}
        </button>
      </template>

      <form v-if="dialog === 'settings'" :id="dialogFormId" class="space-y-4" @submit.prevent="emit('saveSettings'); dialog = ''">
        <label class="block text-sm font-medium text-accent-800 dark:text-accent-100">
          <span class="inline-flex items-center gap-1.5"><PhFolderOpen :size="16" aria-hidden="true" />采集输出目录</span>
          <input v-model="form.outputDir" class="input mt-2" placeholder="使用默认目录" />
        </label>
        <p class="text-xs leading-relaxed text-accent-500 dark:text-accent-400">
          留空时使用系统默认目录保存采集临时文件与调试产物。修改后对后续新发起的采集生效。
        </p>
      </form>

      <form v-else :id="dialogFormId" class="space-y-4" @submit.prevent="save">
        <template v-if="dialog === 'add' || dialog === 'edit'">
          <div>
            <div class="flex items-center justify-between gap-2">
              <label :for="`${dialogFormId}-url`" class="text-sm font-medium text-accent-800 dark:text-accent-100">
                {{ dialog === 'add' ? '商品链接或 1688 分享文案' : '商品 URL' }}
              </label>
              <div v-if="dialog === 'add' && urlInput.trim()" class="flex items-center gap-2 text-xs">
                <span class="rounded-full bg-primary-50 px-2 py-0.5 font-medium text-primary-700 tabular-nums dark:bg-primary-500/15 dark:text-primary-300">
                  {{ inputLineCount }} 行
                </span>
                <button type="button" class="text-accent-500 hover:text-accent-800 dark:text-accent-400 dark:hover:text-accent-100" @click="urlInput = ''">
                  清空
                </button>
              </div>
            </div>
            <textarea
              v-if="dialog === 'add'"
              :id="`${dialogFormId}-url`"
              v-model="urlInput"
              required
              class="input mt-2 min-h-36 resize-y font-mono text-xs leading-relaxed"
              placeholder="每行一条，支持直接粘贴商品链接或完整的 1688 分享文案（短链将自动转换为标准商品地址）"
            />
            <input v-else :id="`${dialogFormId}-url`" v-model="urlInput" type="url" required class="input mt-2 font-mono text-xs" />
          </div>

          <div class="space-y-2">
            <label class="block text-sm font-medium text-accent-800 dark:text-accent-100">
              采集方式
              <select v-model="mode" class="input mt-2 block max-w-56">
                <option value="browser">浏览器采集</option>
                <option value="api">1688 API 采集</option>
              </select>
            </label>
            <div class="flex items-start gap-2 rounded-xl border border-accent-200/80 bg-accent-50/80 p-3 text-xs leading-relaxed text-accent-600 dark:border-dark-700 dark:bg-dark-800/60 dark:text-accent-300">
              <PhInfo :size="15" class="mt-0.5 shrink-0 text-primary-600 dark:text-primary-400" aria-hidden="true" />
              <span v-if="mode === 'browser'">
                浏览器采集由桌面端打开商品页读取完整信息，支持 1688 及通用站点，遇到登录或人机验证时可在窗口内完成。
              </span>
              <span v-else>
                1688 API 采集通过当前账号已授权的 1688 采购通道直连读取，速度更快，网页端即可直接执行。
              </span>
            </div>
          </div>

          <p v-if="dialog === 'edit'" class="text-xs text-accent-500 dark:text-accent-400">
            修改链接或采集方式后，状态会重置为「未开始」；更换 URL 时会清除原链接的结果与诊断日志。
          </p>
        </template>

        <div v-else-if="dialog === 'modeBatch'" class="space-y-4 text-sm">
          <div class="flex items-start gap-2.5 rounded-xl border border-primary-200/80 bg-primary-50/70 p-3 text-primary-900 dark:border-primary-500/25 dark:bg-primary-500/10 dark:text-primary-200">
            <PhSlidersHorizontal :size="17" class="mt-0.5 shrink-0 text-primary-600 dark:text-primary-400" aria-hidden="true" />
            <p>将当前筛选中已勾选的 <strong>{{ batchTargets.length }}</strong> 条非运行链接切换为指定采集方式。修改方式后这些链接会重置为「未开始」。</p>
          </div>
          <label class="block text-sm font-medium text-accent-800 dark:text-accent-100">
            目标采集方式
            <select v-model="mode" class="input mt-2 block max-w-56">
              <option value="browser">浏览器采集</option>
              <option value="api">1688 API 采集</option>
            </select>
          </label>
        </div>

        <div v-else-if="dialog === 'remove'" class="space-y-3 text-sm">
          <div class="flex items-start gap-3 rounded-xl border border-rose-200/80 bg-rose-50/70 p-3.5 text-rose-900 dark:border-rose-500/25 dark:bg-rose-500/10 dark:text-rose-200">
            <PhTrash :size="18" class="mt-0.5 shrink-0 text-rose-600 dark:text-rose-400" aria-hidden="true" />
            <p>移除链接及其采集日志？已入库的商品会保留在采集箱中。</p>
          </div>
          <div class="rounded-xl border border-accent-200 bg-accent-50/70 p-3 dark:border-dark-700 dark:bg-dark-800/60">
            <p v-if="original?.title" class="font-semibold text-accent-900 dark:text-white">{{ original.title }}</p>
            <p class="mt-1 break-all font-mono text-xs text-accent-500 dark:text-accent-400">{{ original?.url }}</p>
          </div>
        </div>

        <div v-else-if="dialog === 'removeBatch'" class="space-y-3 text-sm">
          <div class="flex items-start gap-3 rounded-xl border border-rose-200/80 bg-rose-50/70 p-3.5 text-rose-900 dark:border-rose-500/25 dark:bg-rose-500/10 dark:text-rose-200">
            <PhTrash :size="18" class="mt-0.5 shrink-0 text-rose-600 dark:text-rose-400" aria-hidden="true" />
            <p>确认移除当前筛选下{{ batchActionLabel || '已勾选的' }} <strong>{{ batchTargets.length }}</strong> 条链接及其采集日志？已入库的商品会保留在采集箱中。</p>
          </div>
          <ul class="max-h-44 space-y-1.5 overflow-y-auto rounded-xl border border-accent-200 bg-accent-50/70 p-3 text-xs dark:border-dark-700 dark:bg-dark-800/60">
            <li v-for="item in batchTargets.slice(0, 8)" :key="item.id" class="truncate text-accent-700 dark:text-accent-200">
              {{ item.title || item.url }}
            </li>
            <li v-if="batchTargets.length > 8" class="text-accent-500 dark:text-accent-400">
              …以及其他 {{ batchTargets.length - 8 }} 条链接
            </li>
          </ul>
        </div>

        <p v-if="dialogError" role="alert" class="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{{ dialogError }}</p>
      </form>
    </WorkspaceDialog>
  </div>
</template>

<style scoped>
.collection-page {
  gap: 12px;
  @apply flex min-h-0 min-w-0 flex-1 flex-col;
}
.collection-heading {
  position: sticky;
  inset-block-start: var(--workflow-mobile-nav-height, 0px);
  @apply z-10 min-h-10 shrink-0 items-center bg-accent-100 dark:bg-dark-900;
}
.collection-card {
  @apply flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-accent-200/90 bg-white shadow-sm dark:border-dark-700 dark:bg-dark-800/70;
}
.collection-panes {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr);
  gap: 16px;
  @apply min-h-0 flex-1;
}
.collection-panes--split {
  grid-template-columns: minmax(0, 1fr) clamp(360px, 27%, 440px);
}
.collection-pane {
  scrollbar-gutter: stable;
  @apply min-h-0 min-w-0 overflow-auto px-4 py-3;
}
.collection-panes--split .collection-pane--detail {
  @apply border-l border-accent-200/80 bg-accent-50/40 dark:border-dark-700 dark:bg-dark-900/40;
}
.collection-action {
  @apply min-h-9 rounded-xl px-3 py-1.5;
}
.collection-action.btn-primary {
  @apply bg-primary-700 bg-none shadow-none hover:bg-primary-800 dark:bg-primary-500 dark:text-primary-950 dark:hover:bg-primary-400;
}
.mode-segment {
  @apply inline-flex min-h-7 items-center gap-1 rounded-lg px-2.5 text-xs font-medium text-accent-600 transition hover:text-accent-900 dark:text-accent-300 dark:hover:text-white;
}
.mode-segment[aria-pressed='true'] {
  @apply bg-white font-semibold text-primary-700 shadow-sm dark:bg-dark-800 dark:text-primary-300;
}
.collection-tool {
  @apply inline-flex min-h-8 items-center gap-1.5 rounded-xl px-2 text-xs font-medium text-accent-600 transition hover:bg-accent-200/60 hover:text-accent-950 dark:text-accent-300 dark:hover:bg-white/10 dark:hover:text-white;
}
@media (height < 560px) {
  .collection-panes { grid-template-rows: auto; }
  .collection-pane { overflow: visible; }
}
</style>
