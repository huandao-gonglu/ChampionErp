<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { PhArrowRight, PhArrowsClockwise, PhGearSix, PhPlus } from '@phosphor-icons/vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { manageLinks, type CollectionLink, type LinkMode } from '@/api/workflow/collectionLinks'
import { useWorkflowCollectionStore } from '@/stores/workflow/collection'
import { desktopOperationReason, hasLocalOperations } from '@/utils/desktop'
import { launchDesktop } from '@/utils/desktopHandoff'
import { normalizeCollectUrl } from '@/utils/collectQueue'
import type { CollectForm } from '@/types/workflow'
import CollectBatchManager from './CollectBatchManager.vue'
import CollectLinkDetail from './CollectLinkDetail.vue'

const props = defineProps<{ form: CollectForm; loading: boolean; error: string }>()
const emit = defineEmits<{ openLibrary: []; openProduct: [id: string]; saveSettings: [] }>()
const store = useWorkflowCollectionStore()
const selectedId = ref('')
const selected = computed(() => store.links.find(row => row.id === selectedId.value))
const dialog = ref<'' | 'add' | 'edit' | 'remove' | 'settings'>('')
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
      const entries = urlInput.value.trim().split(/\s+/).map((value, i) => {
        try { return { url: normalizeCollectUrl(value), mode: mode.value } }
        catch (e) { throw new Error(`第 ${i + 1} 个链接无效：${(e as Error).message}`) }
      })
      const result = await manageLinks({ action: 'add', items: entries })
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
</script>

<template>
  <div class="space-y-4">
    <PageHeader title="采集商品">
      <template #actions><button class="btn btn-outline" @click="emit('openLibrary')">采集箱 <PhArrowRight /></button></template>
    </PageHeader>
    <div class="rounded-xl border border-slate-200 bg-white dark:border-dark-700 dark:bg-dark-800">
      <div class="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-slate-200 px-4 py-3 dark:border-dark-700">
        <label class="flex items-center gap-2 text-sm"><span>默认方式</span><select v-model="form.mode" class="input !w-auto !py-1.5 text-sm"><option value="browser">浏览器采集</option><option value="api">API 采集</option></select></label>
        <button class="inline-flex items-center gap-1.5 text-sm text-slate-600 dark:text-accent-300" @click="show('settings')"><PhGearSix />采集设置</button>
        <button class="btn btn-primary ml-auto" :disabled="saving || !store.ready" @click="show('add')"><PhPlus weight="bold" />添加链接</button>
      </div>
      <div v-if="error || store.syncError || store.runError || actionError" role="alert" class="mx-4 mt-3 rounded-lg bg-rose-50 p-3 text-sm text-rose-800 dark:bg-rose-500/10 dark:text-rose-200">{{ actionError || store.runError || store.syncError || error }}</div>
      <div v-if="store.waitingId" role="status" class="mx-4 mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-500/10 dark:text-amber-200"><span>请在原商品页完成验证，完成后会继续采集。</span><button class="underline" @click="store.cancelWait">取消等待</button></div>
      <div class="grid items-start gap-5 p-4 lg:grid-cols-[minmax(0,1fr)_minmax(250px,30%)] xl:p-5">
        <CollectBatchManager :rows="store.links" :ready="store.ready" :selected-id="selectedId" :busy="busy" @select="selectedId = $event" @run="run" @add="show('add')" @open-product="emit('openProduct', $event)" />
        <CollectLinkDetail :row="selected" :busy="busy" @edit="show('edit')" @remove="show('remove')" @open-product="emit('openProduct', $event)" />
      </div>
      <footer class="flex items-center justify-between gap-2 border-t border-slate-200 px-4 py-2 text-xs text-slate-500 dark:border-dark-700 dark:text-accent-300">
        <span role="status">{{ notice || (store.syncError ? '同步失败' : store.ready ? '链接已同步到当前账号' : '正在同步链接…') }}</span>
        <button class="flex items-center gap-1.5 p-1" @click="refresh"><PhArrowsClockwise />刷新</button>
      </footer>
    </div>
    <WorkspaceDialog v-if="dialog" :open="true" :title="({ add: '添加链接', edit: '编辑链接', remove: '移除链接', settings: '采集设置' })[dialog]" width="32rem" :close-disabled="saving" @close="dialog = ''">
      <form v-if="dialog === 'settings'" class="space-y-4" @submit.prevent="emit('saveSettings'); dialog = ''">
        <label class="block text-sm">采集输出目录<input v-model="form.outputDir" class="input mt-2" placeholder="使用默认目录" /></label>
        <button class="btn btn-primary" :disabled="loading">保存设置</button>
      </form>
      <form v-else class="space-y-4" @submit.prevent="save">
        <template v-if="dialog !== 'remove'">
          <label class="block text-sm font-medium">商品 URL
            <textarea v-if="dialog === 'add'" v-model="urlInput" required class="input mt-2 min-h-32" placeholder="每行一个商品链接" />
            <input v-else v-model="urlInput" type="url" required class="input mt-2" />
          </label>
          <label class="block text-sm">采集方式<select v-model="mode" class="input mt-2"><option value="browser">浏览器采集</option><option value="api">1688 API 采集</option></select></label>
        </template>
        <div v-else class="space-y-2 text-sm"><p>移除链接及其采集日志？已入库的商品会保留。</p><p class="break-all text-slate-500">{{ original?.url }}</p></div>
        <p v-if="dialogError" role="alert" class="text-sm text-rose-600">{{ dialogError }}</p>
        <div class="flex justify-end gap-2"><button type="button" class="btn btn-outline" :disabled="saving" @click="dialog = ''">取消</button><button class="btn btn-primary" :disabled="saving || (dialog !== 'remove' && !urlInput.trim())">{{ saving ? '正在保存…' : dialog === 'remove' ? '确认移除' : dialog === 'add' ? '添加到列表' : '保存' }}</button></div>
      </form>
    </WorkspaceDialog>
  </div>
</template>
