<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { useMediaQuery } from '@vueuse/core'
import { PhArrowLeft, PhArrowRight, PhArrowsClockwise, PhGearSix, PhPlay, PhPlus } from '@phosphor-icons/vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { manageLinks, type CollectionLink, type LinkMode } from '@/api/workflow/collectionLinks'
import { useWorkflowCollectionStore } from '@/stores/workflow/collection'
import { desktopOperationReason, hasLocalOperations } from '@/utils/desktop'
import { launchDesktop } from '@/utils/desktopHandoff'
import { normalizeCollectUrl } from '@/utils/collectQueue'
import type { CollectBatchStatus, CollectForm } from '@/types/workflow'
import CollectBatchManager from './CollectBatchManager.vue'
import CollectLinkDetail from './CollectLinkDetail.vue'

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
const sort = ref<'none' | 'ascending' | 'descending'>('none')
const checkedIds = ref(new Set<string>())
const collator = new Intl.Collator('zh-CN', { numeric: true, sensitivity: 'base' })
const matching = computed(() => {
  const term = search.value.trim().toLowerCase()
  return store.links.filter(row => !term || row.title?.toLowerCase().includes(term) || row.url.toLowerCase().includes(term))
})
const counts = computed(() => {
  const result: Partial<Record<CollectBatchStatus, number>> = {}
  for (const row of matching.value) result[row.status] = (result[row.status] || 0) + 1
  return result
})
const visibleRows = computed(() => {
  const rows = status.value ? matching.value.filter(row => row.status === status.value) : matching.value
  if (sort.value === 'none') return rows
  const direction = sort.value === 'ascending' ? 1 : -1
  return [...rows].sort((a, b) => direction * collator.compare(a.title || a.url, b.title || b.url))
})
const batchRows = computed(() => checkedIds.value.size
  ? visibleRows.value.filter(row => checkedIds.value.has(row.id))
  : visibleRows.value)
const pendingRows = computed(() => batchRows.value.filter(row => row.status === 'pending' || row.status === 'waiting_verification'))
const failedRows = computed(() => batchRows.value.filter(row => row.status === 'failed'))

const dialog = ref<'' | 'add' | 'edit' | 'remove' | 'settings'>('')
const dialogFormId = useId()
const original = ref<CollectionLink>()
const urlInput = ref('')
const mode = ref<LinkMode>('browser')
const saving = ref(false)
const busy = computed(() => saving.value || store.running || props.loading)
const dialogError = ref('')
const notice = ref('')
const actionError = ref('')
let timer: ReturnType<typeof setInterval> | undefined
let refreshing = false

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

async function refresh() {
  if (refreshing) return
  refreshing = true
  try { await store.refreshLinks() } catch { /* 状态栏显示同步错误。 */ }
  finally { refreshing = false }
}
function refreshVisible() { if (!document.hidden) void refresh() }
onMounted(() => {
  void refresh()
  timer = setInterval(refreshVisible, 5000)
  window.addEventListener('focus', refreshVisible)
  document.addEventListener('visibilitychange', refreshVisible)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  window.removeEventListener('focus', refreshVisible)
  document.removeEventListener('visibilitychange', refreshVisible)
})

function show(kind: 'add' | 'edit' | 'remove' | 'settings') {
  original.value = selected.value
  mode.value = kind === 'edit' && original.value ? original.value.mode : props.form.mode === 'api' ? 'api' : 'browser'
  urlInput.value = kind === 'edit' ? original.value?.url || '' : ''
  dialogError.value = ''
  dialog.value = kind
}
async function save() {
  if (saving.value) return
  saving.value = true
  dialogError.value = ''
  try {
    const row = original.value
    if (dialog.value === 'add') {
      const result = await manageLinks({ action: 'add', items: [{ url: urlInput.value.trim(), mode: mode.value }] })
      notice.value = `已添加 ${result.added} 条，跳过 ${result.duplicates} 条重复链接。`
    } else if (row && dialog.value === 'edit') {
      await manageLinks({ action: 'edit', id: row.id, revision: row.revision, url: normalizeCollectUrl(urlInput.value), mode: mode.value })
      notice.value = '已保存链接设置。'
    } else if (row && dialog.value === 'remove') {
      await manageLinks({ action: 'remove', id: row.id, revision: row.revision })
      notice.value = '已移除链接。'
    }
    dialog.value = ''
    await store.refreshLinks().catch(() => undefined)
  } catch (e) {
    dialogError.value = (e as Error).message
    if ((e as { status?: number }).status === 409) await refresh()
  } finally { saving.value = false }
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
          <PhArrowLeft aria-hidden="true" />返回列表
        </button>
      </template>
      <template #actions>
        <div class="flex flex-wrap items-center justify-end gap-2">
          <template v-if="!replacingList">
            <button type="button" class="btn btn-primary collection-action tabular-nums" :disabled="busy || !pendingRows.length" @click="run(pendingRows)">
              <PhPlay weight="fill" aria-hidden="true" />开始待采集（{{ pendingRows.length }}）
            </button>
            <button type="button" class="btn btn-outline collection-action tabular-nums" :disabled="busy || !failedRows.length" @click="run(failedRows)">
              <PhArrowsClockwise aria-hidden="true" />重试失败（{{ failedRows.length }}）
            </button>
            <button type="button" class="btn btn-primary collection-action" :disabled="busy || !store.ready" @click="show('add')">
              <PhPlus weight="bold" aria-hidden="true" />添加链接
            </button>
            <button type="button" class="btn btn-outline collection-action" aria-label="刷新链接列表" title="刷新链接列表" @click="refresh">
              <PhArrowsClockwise :size="16" aria-hidden="true" />
            </button>
          </template>
          <button type="button" class="btn btn-outline collection-action" @click="emit('openLibrary')">
            采集箱 <PhArrowRight aria-hidden="true" />
          </button>
        </div>
      </template>
    </PageHeader>
    <div class="collection-card">
      <div v-show="!replacingList" class="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-accent-200 px-4 py-3 dark:border-dark-700">
        <label class="flex items-center gap-2 text-sm">
          <span class="shrink-0">默认方式</span>
          <select v-model="form.mode" class="input !w-auto !rounded-lg !py-1.5 text-sm">
            <option value="browser">浏览器采集</option><option value="api">API 采集</option>
          </select>
        </label>
        <button type="button" class="collection-tool" @click="show('settings')"><PhGearSix :size="16" aria-hidden="true" />采集设置</button>
      </div>
      <div v-if="error || store.syncError || store.runError || actionError" role="alert" class="mx-4 mt-3 rounded-lg bg-rose-50 p-3 text-sm text-rose-800 [overflow-wrap:anywhere] dark:bg-rose-500/10 dark:text-rose-200">{{ actionError || store.runError || store.syncError || error }}</div>
      <div v-if="store.waitingId" role="status" class="mx-4 mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-500/10 dark:text-amber-200">
        <span>请在原商品页完成验证，完成后会继续采集。</span><button type="button" class="underline" @click="store.cancelWait">取消等待</button>
      </div>
      <div class="collection-panes" :class="{ 'collection-panes--split': splitView && showDetail }">
        <div v-show="!replacingList" ref="listPane" class="collection-pane" role="region" aria-label="采集链接列表" tabindex="0">
          <CollectBatchManager
            v-model:search="search"
            v-model:status="status"
            v-model:sort="sort"
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
            @open-product="emit('openProduct', $event)"
          />
        </div>
        <div v-if="showDetail" ref="detailPane" class="collection-pane collection-pane--detail" role="region" aria-label="链接详情" tabindex="0">
          <CollectLinkDetail :row="selected" :busy="busy" :closable="splitView" @close="closeDetail" @edit="show('edit')" @remove="show('remove')" @open-product="emit('openProduct', $event)" />
        </div>
      </div>
      <footer class="shrink-0 border-t border-accent-200 px-4 py-2.5 text-xs text-accent-600 dark:border-dark-700 dark:text-accent-300">
        <span role="status">{{ notice || (store.syncError ? '同步失败' : store.ready ? '链接已同步到当前账号' : '正在同步链接…') }}</span>
      </footer>
    </div>
    <WorkspaceDialog v-if="dialog" :open="true" :title="({ add: '添加链接', edit: '编辑链接', remove: '移除链接', settings: '采集设置' })[dialog]" width="32rem" :close-disabled="saving" @close="dialog = ''">
      <template #close>取消</template>
      <template #header-actions>
        <button
          type="submit" :form="dialogFormId" class="btn btn-primary collection-action"
          :disabled="saving || (dialog === 'settings' ? loading : dialog !== 'remove' && !urlInput.trim())"
        >
          {{ saving ? (dialog === 'add' ? '正在添加…' : '正在保存…') : dialog === 'remove' ? '确认移除' : dialog === 'add' ? '添加到列表' : '保存' }}
        </button>
      </template>
      <form v-if="dialog === 'settings'" :id="dialogFormId" @submit.prevent="emit('saveSettings'); dialog = ''">
        <label class="block text-sm">采集输出目录<input v-model="form.outputDir" class="input mt-2" placeholder="使用默认目录" /></label>
      </form>
      <form v-else :id="dialogFormId" class="space-y-4" @submit.prevent="save">
        <template v-if="dialog !== 'remove'">
          <label class="block text-sm font-medium">{{ dialog === 'add' ? '商品链接或 1688 分享文案' : '商品 URL' }}
            <textarea v-if="dialog === 'add'" v-model="urlInput" required class="input mt-2 min-h-32" placeholder="粘贴商品链接或完整的 1688 分享文案，支持多条" />
            <input v-else v-model="urlInput" type="url" required class="input mt-2" />
          </label>
          <label class="block text-sm">采集方式<select v-model="mode" class="input mt-2 block max-w-52"><option value="browser">浏览器采集</option><option value="api">1688 API 采集</option></select></label>
        </template>
        <div v-else class="space-y-2 text-sm"><p>移除链接及其采集日志？已入库的商品会保留。</p><p class="break-all text-slate-500">{{ original?.url }}</p></div>
        <p v-if="dialogError" role="alert" class="text-sm text-rose-600">{{ dialogError }}</p>
      </form>
    </WorkspaceDialog>
  </div>
</template>

<style scoped>
.collection-page {
  gap: 16px;
  @apply flex min-h-0 min-w-0 flex-1 flex-col;
}
.collection-heading {
  position: sticky;
  inset-block-start: var(--workflow-mobile-nav-height, 0px);
  @apply z-10 min-h-10 shrink-0 items-center bg-accent-100 dark:bg-dark-900;
}
.collection-card {
  @apply flex min-h-0 min-w-0 flex-1 flex-col rounded-xl border border-accent-200 bg-white dark:border-dark-700 dark:bg-dark-800/60;
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
  @apply min-h-0 min-w-0 overflow-auto p-4;
}
.collection-panes--split .collection-pane--detail {
  @apply border-l border-accent-200 dark:border-dark-700;
}
.collection-action {
  @apply min-h-9 rounded-lg px-3 py-1.5;
}
.collection-action.btn-primary {
  @apply bg-primary-700 bg-none shadow-none hover:bg-primary-800 dark:bg-primary-500 dark:text-primary-950 dark:hover:bg-primary-400;
}
.collection-tool {
  @apply inline-flex min-h-8 items-center gap-1.5 rounded-lg px-2 text-sm text-accent-700 hover:bg-accent-100 dark:text-accent-300 dark:hover:bg-white/5;
}
@media (height < 560px) {
  .collection-panes { grid-template-rows: auto; }
  .collection-pane { overflow: visible; }
}
</style>
