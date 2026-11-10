<script setup lang="ts">
import { draftContextTitle, draftListTitle } from '@/utils/draftDisplayTitle'
import { modelOperationReason } from '@/utils/desktop'
import { vDesktop } from '@/utils/desktopHandoffDirective'
import { useDesktopHandoffContext, receivedDesktopHandoff, acknowledgeDesktopHandoff, type DesktopWorkflowState } from '@/utils/desktopHandoff'
import { computed, nextTick, onActivated, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AuthSettingsPanel from '@/components/auth/AuthSettingsPanel.vue'
import DraftCategoryWorkspace from '@/views/workflow/DraftCategoryWorkspace.vue'
import DraftPublishPrecheckWorkspace from './DraftPublishPrecheckWorkspace.vue'
import CollectView from '@/views/workflow/CollectView.vue'
import DashboardView from '@/views/workflow/DashboardView.vue'
import DraftBoxPanel from '@/components/domain/DraftBoxPanel.vue'
import DraftImageWorkbench from '@/components/domain/DraftImageWorkbench.vue'
import DraftSkuPanel from '@/components/domain/DraftSkuPanel.vue'
import DraftSkuAttributesEditor from '@/components/domain/DraftSkuAttributesEditor.vue'
import DraftEditorPanel from '@/components/domain/DraftEditorPanel.vue'
import DraftWorkspacePanel, { type DraftWorkspaceTab } from '@/components/domain/DraftWorkspacePanel.vue'
import LibraryPanel from '@/components/domain/LibraryPanel.vue'
import SystemSettingsPanel from '@/components/domain/SystemSettingsPanel.vue'
import OrderCenterPanel from '@/components/domain/OrderCenterPanel.vue'
import OrderIntegrationSettings from '@/components/domain/OrderIntegrationSettings.vue'
import OnlineProductsPanel from '@/components/domain/OnlineProductsPanel.vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import PricingPanel from '@/components/domain/PricingPanel.vue'
import ProductImageEditorPanel from '@/components/domain/ProductImageEditorPanel.vue'
import ProductEditorPanel from '@/components/domain/ProductEditorPanel.vue'
import ProductResearchPanel from '@/components/domain/ProductResearchPanel.vue'
import TaskQueuePanel from '@/components/domain/TaskQueuePanel.vue'
import { workflowNavItems } from '@/constants/navigation'
import { useDraftImageWorkbench } from '@/composables/useDraftImageWorkbench'
import { useDraftImageSaveState } from '@/composables/useDraftImageSaveState'
import { useAiPageContext } from '@/composables/useAiPageContext'
import { aiPageLabels } from '@/types/aiPageContext'
import { useClipboard } from '@/composables/useClipboard'
import { useBackdropDismiss } from '@/composables/useBackdropDismiss'
import { useOrderCenterEntrySync } from '@/composables/useOrderCenterEntrySync'
import { useAppStore } from '@/stores/app'
import { useWorkflowStore } from '@/stores/workflow'
import { useWorkflowActivityStore } from '@/stores/workflow/activity'
import { useWorkflowCatalogStore } from '@/stores/workflow/catalog'
import { loadDraft as loadDraftForHandoff, loadProduct as loadProductForEditor } from '@/api/workflow/catalog'
import { useAiDraftSync } from '@/composables/useAiDraftSync'
import { useWorkflowCollectionStore } from '@/stores/workflow/collection'
import { useWorkflowPublishingStore } from '@/stores/workflow/publishing'
import { useWorkflowSettingsStore } from '@/stores/workflow/settings'
import type { DraftIndexItem, MarketplaceTargetSite, ProductIndexItem } from '@/types/workflow'

const store = useWorkflowStore()
useOrderCenterEntrySync()
const activityStore = useWorkflowActivityStore()
const catalogStore = useWorkflowCatalogStore()
const collectionStore = useWorkflowCollectionStore()
const publishingStore = useWorkflowPublishingStore()
const settingsStore = useWorkflowSettingsStore()

const {
  product,
  productsIndex,
  draftsIndex,
  selectedProductIds,
  currentDraft,
  currentDraftProductContext,
} = storeToRefs(catalogStore)
const imageSaveState = useDraftImageSaveState(currentDraft, () => store.saveCurrentDraft())
const {
  collectForm,
  collectDiagnostics,
  collectBatchRows,
} = storeToRefs(collectionStore)
const {
  pricingInput,
  pricingResult,
  copyGenerating,
  publishJob,
  platformOptions,
} = storeToRefs(publishingStore)
const {
  appConfig,
  aiConfig,
  modelsReading,
  modelsSaving,
  storeConfig,
  storeAuthSummary,
  mercadolibreAuthChecklist,
  lastAuthResult,
  authLink,
} = storeToRefs(settingsStore)
const {
  logs,
  loading,
  error,
} = storeToRefs(activityStore)
const {
  workflowSteps,
  progressPercent,
  imagePool,
} = storeToRefs(store)

const appStore = useAppStore()
const route = useRoute()
const router = useRouter()
const { copied: productIdCopied, copy: copyToClipboard } = useClipboard()
const activeNav = ref('dashboard')
const initialStateLoaded = ref(false)
const editorOpen = ref(false)
const focusProductSku = ref('')
const draftWorkspaceOpen = ref(false)
watch(draftWorkspaceOpen, (open, _previous, onCleanup) => {
  if (!open) return
  const previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  onCleanup(() => { document.body.style.overflow = previousOverflow })
})
const draftWorkspaceTab = ref<DraftWorkspaceTab>('text')
useDesktopHandoffContext(() => {
  if (route.path !== '/') return null
  const workflow: DesktopWorkflowState = {}
  if (draftWorkspaceOpen.value) {
    workflow.draft = currentDraft.value
    workflow.draftContext = currentDraftProductContext.value
    workflow.draftTab = draftWorkspaceTab.value
    workflow.pricingInput = pricingInput.value
  }
  if (editorOpen.value) {
    workflow.product = product.value
    workflow.editorMode = editorMode.value
  }
  if (activeNav.value === 'collect') {
    workflow.collect = { mode: collectForm.value.mode, productUrl: collectForm.value.productUrl, platform: collectForm.value.platform }
    workflow.batchRows = collectBatchRows.value
  }
  return { target: route.fullPath, state: Object.keys(workflow).length ? { workflow } : {} }
})
let restoringHandoff = ''
watch([receivedDesktopHandoff, initialStateLoaded, () => route.fullPath, loading], async ([handoff, loaded]) => {
  if (!handoff || !loaded || route.fullPath !== handoff.target || !handoff.state.workflow || restoringHandoff === handoff.requestId) return
  if (handoff.state.workflow.collectAction && loading.value) return
  restoringHandoff = handoff.requestId
  try {
    const state = handoff.state.workflow as DesktopWorkflowState
    if (state.collect) {
      Object.assign(collectForm.value, state.collect, { mode: state.collect.mode === 'api' ? 'api' : 'browser' })
      if (state.batchRows) collectBatchRows.value = state.batchRows
    }
    if (state.draft?.draftId && state.draftContext) {
      const saved = await loadDraftForHandoff(state.draft.draftId)
      if (receivedDesktopHandoff.value?.requestId !== handoff.requestId) return
      store.acceptLoadedDraft(saved)
      await nextTick()
      // 建立服务端基线后恢复本地编辑，沿用原来的脏状态和版本校验。
      Object.assign(currentDraft.value, state.draft)
      if (state.pricingInput) Object.assign(pricingInput.value, state.pricingInput)
      currentDraftProductContext.value = state.draftContext
      store.setMarketplace(state.draft.platform)
      draftWorkspaceTab.value = state.draftTab || 'text'
      draftWorkspaceOpen.value = true
    }
    if (state.product) {
      product.value = state.product
      editorMode.value = state.editorMode || 'text'
      editorOpen.value = true
    }
    await acknowledgeDesktopHandoff(handoff.requestId, 'workflow')
    if (state.collectAction?.type === 'single') await store.collectProduct()
    else if (state.collectAction?.type === 'batch') await store.collectBatch(state.collectAction.rowIds)
  } catch (cause) {
    activityStore.setError(cause instanceof Error ? cause.message : '桌面页面恢复失败，请回网页重试。')
  } finally { restoringHandoff = '' }
})
const draftSync = useAiDraftSync({
  draft: currentDraft, input: pricingInput, loading,
  accept: result => store.acceptLoadedDraft(result),
  acceptProductContext: result => {
    if (JSON.stringify(currentDraftProductContext.value.skuItems) !== JSON.stringify(result.productContext.skuItems)
      || currentDraftProductContext.value.brand !== result.productContext.brand || currentDraftProductContext.value.model !== result.productContext.model) {
      currentDraftProductContext.value = result.productContext
      pricingResult.value = null
      for (const row of currentDraft.value.skuItems) row.pricing.applied = false
    } else {
      currentDraftProductContext.value.imagePool = result.productContext.imagePool
    }
  },
  clearError: () => activityStore.setError(''),
})
const draftSyncMessage = draftSync.message
const draftRefreshing = draftSync.refreshing

async function reloadDraft() {
  if (draftSync.dirty.value && !window.confirm('重新加载将放弃当前页面未保存的修改，使用最新保存的草稿。是否继续？')) return
  await draftSync.refresh(true)
}

function checkDraftUpdates() {
  if (draftWorkspaceOpen.value) void draftSync.refresh()
}
const editorMode = ref<'text' | 'images'>('text')
const imageEditorTitle = ref('采集箱图片编辑')
const navItems = workflowNavItems

useAiPageContext(() => {
  if (route.path !== '/') return null
  return { page: activeNav.value in aiPageLabels ? activeNav.value as keyof typeof aiPageLabels : 'dashboard' }
})
useAiPageContext(() => {
  if (route.path === '/' && draftWorkspaceOpen.value) {
    const draft = currentDraft.value
    const target = draft.targetSites.length === 1 ? draft.targetSites[0] : undefined
    return {
      page: 'draft_editor', section: draftWorkspaceTab.value,
      draft_id: draft.draftId || undefined,
      product_id: draft.productId || currentDraftProductContext.value.productId || undefined,
      platform: target?.platform, site: target?.site || undefined,
    }
  }
  return null
}, 5)
useAiPageContext(() => editorOpen.value && route.path === '/' ? {
  page: 'product_editor', section: editorMode.value,
  product_id: product.value.productId || undefined,
} : null, 30)

const pricingDraftItems = computed(() => draftsIndex.value.filter((item) => item.draftId))
const draftWorkspaceTitle = computed(() => draftContextTitle(currentDraftProductContext.value, currentDraft.value.draftId) || '草稿编辑')
const draftSaveBlockedReason = computed(() => (
  currentDraft.value.draftId ? '' : '当前草稿暂无 ID。'
))
type PackageDimensionField = 'lengthCm' | 'widthCm' | 'heightCm' | 'weightKg'

function syncPricingPackageDimension(field: PackageDimensionField, value: string) {
  const normalizedValue = value.trim()
  const parsedValue = Number(normalizedValue)
  pricingInput.value[field] = normalizedValue && Number.isFinite(parsedValue) ? parsedValue : 0
}

async function openProductEditor(item?: ProductIndexItem) {
  focusProductSku.value = ''
  if (item) await store.loadProduct(item)
  editorMode.value = 'text'
  imageEditorTitle.value = '采集箱图片编辑'
  editorOpen.value = true
}

async function editDraftProduct(skuId: string) {
  if (loading.value || !currentDraft.value.productId) return
  const draftId = currentDraft.value.draftId
  loading.value = true
  try {
    const result = await loadProductForEditor(currentDraft.value.productId)
    if (currentDraft.value.draftId !== draftId) return
    product.value = result.product
    focusProductSku.value = skuId
    editorMode.value = 'text'
    editorOpen.value = true
  } catch (cause) { activityStore.setError(cause instanceof Error ? cause.message : '读取商品资料失败') }
  finally { loading.value = false }
}
async function saveProductFacts() {
  const input = structuredClone(JSON.parse(JSON.stringify(pricingInput.value)))
  const saved = await store.saveCurrentProduct()
  if (draftWorkspaceOpen.value) {
    pricingInput.value = input
    if (saved) await draftSync.refresh()
  }
}

async function openProductImageEditor(item?: ProductIndexItem) {
  await openProductEditor(item)
  editorMode.value = 'images'
}

const imageWorkbench = useDraftImageWorkbench({
  draft: currentDraft, productContext: currentDraftProductContext, active: draftWorkspaceOpen,
  save: () => imageSaveState.save(),
  accept: result => {
    if ('product' in result) product.value = result.product
    if (result.draft) currentDraft.value = result.draft
    if (result.productContext) currentDraftProductContext.value = result.productContext
    else if ('product' in result && result.product.productId === currentDraftProductContext.value.productId) {
      // 单独上传返回商品素材；加入草稿之前也可从素材列表选取。
      currentDraftProductContext.value.imagePool = result.imagePool
    }
    if (result.productsIndex.length) productsIndex.value = result.productsIndex
    if (result.draftsIndex?.length) draftsIndex.value = result.draftsIndex
  },
})
const imageWorkbenchBusy = imageWorkbench.busy
const draftWorkspaceLoading = computed(() => loading.value || imageWorkbenchBusy.value)

async function openDraftWorkspace(item: DraftIndexItem, tab: DraftWorkspaceTab = 'text') {
  store.setMarketplace(item.platform)
  await store.loadDraft(item)
  if (currentDraft.value.draftId !== item.draftId) return
  draftWorkspaceTab.value = tab
  draftWorkspaceOpen.value = true
}

async function translateEditorImages(imageIds: string[]) {
  await store.translateImages(undefined, { sourceImageIds: imageIds })
}

async function editEditorImages(request: { prompt: string; imageIds: string[] }) {
  await store.editImagesWithPrompt(request.prompt, { sourceImageIds: request.imageIds })
}

async function deleteDraft(item: DraftIndexItem) {
  const title = draftListTitle(item)
  if (!window.confirm(`确认删除草稿「${title}」？商品本身不会被删除。`)) return
  await store.deleteDraft(item)
}

async function deleteDrafts(items: DraftIndexItem[]) {
  const validItems = items.filter((item) => String(item.draftId || '').trim())
  if (!validItems.length) return
  if (!window.confirm(`确认批量删除已勾选的 ${validItems.length} 个草稿？商品本身不会被删除。`)) return
  await store.deleteDrafts(validItems)
}

function closeProductEditor() {
  editorOpen.value = false
  resetProductEditorBackdropPointer()
}

const {
  recordBackdropPointer: recordProductEditorBackdropPointer,
  dismissFromBackdrop: closeProductEditorFromBackdrop,
  resetBackdropPointer: resetProductEditorBackdropPointer,
} = useBackdropDismiss(closeProductEditor)

function closeDraftWorkspace() {
  if (imageSaveState.dirty.value && !window.confirm('图片设置尚未保存，关闭将放弃这些修改。确认关闭？')) return
  draftWorkspaceOpen.value = false
  resetDraftWorkspaceBackdropPointer()
}

const {
  recordBackdropPointer: recordDraftWorkspaceBackdropPointer,
  dismissFromBackdrop: closeDraftWorkspaceFromBackdrop,
  resetBackdropPointer: resetDraftWorkspaceBackdropPointer,
} = useBackdropDismiss(closeDraftWorkspace)

async function copyProductId() {
  if (!product.value.productId) return
  await copyToClipboard(product.value.productId)
}

async function refreshDomainForNav(key: string) {
  await store.hydrateTab(key)
}

function navigate(key: string) {
  if (key === 'publish' && activeNav.value === key) return
  activeNav.value = key
  const nextQuery = key === 'dashboard' ? {} : { tab: key }
  if (route.path !== '/' || String(route.query.tab || '') !== String(nextQuery.tab || '')) {
    void router.push({ name: 'WorkflowHome', query: nextQuery })
  }
  void refreshDomainForNav(key)
}

function openOnlineTask(jobId?: string) {
  void router.push({ name: 'WorkflowHome', query: { tab: 'publish', source: 'online', ...(jobId ? { task_id: `online:${jobId}` } : {}) } })
}
function closeTaskDetail() {
  if (!route.query.task_id) return
  const query = { ...route.query }
  delete query.task_id
  void router.replace({ query })
}

async function claimAndOpenDrafts(productIds: string[], targets: MarketplaceTargetSite[]) {
  const ok = await store.claimProductsToDrafts(productIds, targets)
  if (ok) navigate('drafts')
}

function warnUnsavedImages(event: BeforeUnloadEvent) {
  if (!draftWorkspaceOpen.value || !imageSaveState.dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}
onMounted(async () => {
  window.addEventListener('beforeunload', warnUnsavedImages)
  window.addEventListener('focus', checkDraftUpdates)
  const initialTab = activeNav.value
  const modelLoad = initialTab === 'auth' ? refreshDomainForNav(initialTab) : undefined
  await store.loadState()
  if (activeNav.value === 'auth' && modelLoad) await modelLoad
  else await refreshDomainForNav(activeNav.value)
  initialStateLoaded.value = true
})

onActivated(() => {
  checkDraftUpdates()
  if (initialStateLoaded.value && activeNav.value === 'publish') void refreshDomainForNav('publish')
})
onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', warnUnsavedImages)
  window.removeEventListener('focus', checkDraftUpdates)
})


watch(
  () => route.query,
  () => {
    const tab = String(route.query.tab || '')
    const previous = activeNav.value
    activeNav.value = navItems.some((item) => item.key === tab) ? tab : 'dashboard'
    if (initialStateLoaded.value && previous !== activeNav.value) {
      void refreshDomainForNav(activeNav.value)
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="min-h-screen bg-accent-100 dark:bg-dark-950">
    <div class="min-h-screen lg:grid" :style="{ gridTemplateColumns: appStore.sidebarCollapsed ? '84px minmax(0,1fr)' : '280px minmax(0,1fr)' }">
      <AppSidebar
        class="sticky top-0 hidden h-screen lg:flex"
        :items="navItems"
        :active-key="activeNav"
        :steps="workflowSteps"
        :progress="progressPercent"
        :collapsed="appStore.sidebarCollapsed"
        @navigate="navigate"
        @toggle-collapse="appStore.setSidebarCollapsed(!appStore.sidebarCollapsed)"
      />

      <main class="min-w-0">
        <div class="sticky top-0 z-20 border-b border-accent-200 bg-white/90 px-4 py-3 backdrop-blur dark:border-dark-700 dark:bg-dark-900/90 lg:hidden">
          <select :value="activeNav" class="input" @change="navigate(($event.target as HTMLSelectElement).value)">
            <option v-for="item in navItems" :key="item.key" :value="item.key">{{ item.title }}</option>
          </select>
        </div>

        <div class="px-4 py-6 sm:px-6 lg:px-8">
          <DashboardView
            v-if="activeNav === 'dashboard'"
            :product="product"
            :products-index="productsIndex"
            :selected-ids="selectedProductIds"
            :progress-percent="progressPercent"
            :auth-checklist="mercadolibreAuthChecklist"
            :publish-job="publishJob"
            :logs="logs"
            :loading="loading"
            :error="error"
            @navigate="navigate"
            @refresh-products="store.refreshProductsIndex"
            @open-product="openProductEditor"
            @edit-images="openProductImageEditor"
            @collect="navigate('collect')"
            @publish-selected="store.enqueueSelectedProducts"
          />

          <div v-else-if="activeNav === 'research'" class="space-y-6">
            <PageHeader title="选品调研" description="目标市场需求、相近市场参考、数据源质量和机会评分。" />
            <ProductResearchPanel />
          </div>

          <CollectView
            v-else-if="activeNav === 'collect'"
            :form="collectForm"
            :diagnostics="collectDiagnostics"
            :product="product"
            :loading="loading"
            :error="error"
            :batch-rows="collectBatchRows"
            @collect="store.collectProduct"
            @batch-collect="store.collectBatch"
            @cancel-verification="store.cancelCollectionVerification"
            @update-batch-rows="store.updateCollectBatchRows"
            @clear-product="store.clearCollectedProduct"
            @save-settings="store.saveCollectSettings"
          />

          <LibraryPanel
            v-else-if="activeNav === 'library'"
            :items="productsIndex"
            :selected-ids="selectedProductIds"
            :platform-options="platformOptions"
            :store-config="storeConfig"
            :loading="loading"
            :error="error"
            @refresh="store.refreshProductsIndex"
            @claim="claimAndOpenDrafts"
            @edit="openProductEditor"
            @delete-item="store.deleteProduct"
            @delete-selected="store.deleteSelectedProducts"
            @toggle="store.toggleProductSelection"
            @select-all="store.selectAllProducts"
          />

          <div v-else-if="activeNav === 'drafts'" class="space-y-6">
            <DraftBoxPanel
              :drafts="draftsIndex"
              :platform-options="platformOptions"
              :store-config="storeConfig"
              :loading="loading"
              :error="error"
              @refresh="store.refreshDraftsIndex"
              @update-language="store.updateDraftLanguage"
              @edit="openDraftWorkspace"
              @duplicate-draft="store.duplicateDraft"
              @delete-draft="deleteDraft"
              @delete-drafts="deleteDrafts"
              @update-targets="store.updateDraftTargets"
            />
          </div>

          <TaskQueuePanel v-else-if="activeNav === 'publish'" :initial-task-id="String(route.query.task_id || '')" :initial-source="String(route.query.source || '')" @close-task="closeTaskDetail" />

          <SystemSettingsPanel v-else-if="activeNav === 'systemSettings'" />
          <OrderCenterPanel v-else-if="activeNav === 'orders'" />
          <OnlineProductsPanel v-else-if="activeNav === 'onlineProducts'" @open-task="openOnlineTask" />

          <div v-else-if="activeNav === 'auth'" class="space-y-6">
            <AuthSettingsPanel
              :app-config="appConfig"
              :ai-config="aiConfig"
              :models-reading="modelsReading"
              :models-saving="modelsSaving"
              :store-config="storeConfig"
              :store-auth-summary="storeAuthSummary"
              :platform-options="platformOptions"
              :mercadolibre-checklist="mercadolibreAuthChecklist"
              :last-result="lastAuthResult"
              :auth-link="authLink"
              :loading="loading"
              @save-ai="store.saveAiSettings"
              @test-ai="store.testAiSettings"
              @test-api="store.testPlatformApiConfig"
              @save-store="store.saveStoreConfig"
              @save-currency="store.saveStoreCurrency"
              @test-auth="store.testAuth"
              @refresh-checklist="store.loadMercadoLibreChecklist"
              @generate-ml-link="store.generateMercadoLibreAuthLink"
              @open-ml-link="store.openMercadoLibreAuth"
              @refresh-ml-token="store.refreshMercadoLibreAuthToken"
              @real-ml-test="store.runMercadoLibreAuthTest"
              @exchange-ml-code="store.exchangeMlCode"
              @clear-auth="store.clearPlatformAuth"
            />
            <OrderIntegrationSettings />
          </div>
        </div>
      </main>
    </div>
    <div
      v-if="editorOpen"
      class="fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto bg-slate-950/50 p-4 backdrop-blur-sm"
      @pointerdown="recordProductEditorBackdropPointer"
      @pointerup="closeProductEditorFromBackdrop"
      @pointercancel="resetProductEditorBackdropPointer"
    >
      <div class="w-full max-w-7xl rounded-3xl bg-white p-4 shadow-2xl ring-1 ring-slate-200 dark:bg-dark-900 dark:ring-dark-700 sm:p-6">
        <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 class="text-xl font-black text-slate-950 dark:text-white">{{ product.source.title || product.name || product.productId || '商品编辑' }}</h2>
            <p class="mt-1 text-sm text-slate-500 dark:text-accent-300">{{ product.productId || '在同一工作台完成文本与图片编辑' }}</p>
          </div>
          <div class="flex flex-wrap gap-2">
            <button class="btn btn-outline" :disabled="!product.productId" :title="product.productId || '当前商品暂无 ID'" @click="copyProductId">
              {{ productIdCopied ? '已复制' : '复制id' }}
            </button>
            <button class="btn btn-outline" @click="closeProductEditor">关闭</button>
          </div>
        </div>
        <nav class="mb-5 grid gap-2 rounded-2xl border border-accent-200 bg-accent-50/70 p-2 dark:border-dark-700 dark:bg-dark-950/70 sm:grid-cols-2" aria-label="商品编辑选项">
          <button
            type="button"
            role="tab"
            :aria-selected="editorMode === 'text'"
            class="rounded-xl border px-4 py-3 text-left transition"
            :class="editorMode === 'text' ? 'border-primary-300 bg-white text-primary-700 shadow-sm dark:border-primary-500/60 dark:bg-dark-800 dark:text-primary-200' : 'border-transparent text-accent-600 hover:bg-white/70 dark:text-accent-300 dark:hover:bg-dark-800/70'"
            @click="editorMode = 'text'"
          >
            <span class="block font-semibold">编辑文本</span>
            <span class="mt-1 block text-xs opacity-75">标题、描述和商品属性</span>
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="editorMode === 'images'"
            class="rounded-xl border px-4 py-3 text-left transition"
            :class="editorMode === 'images' ? 'border-primary-300 bg-white text-primary-700 shadow-sm dark:border-primary-500/60 dark:bg-dark-800 dark:text-primary-200' : 'border-transparent text-accent-600 hover:bg-white/70 dark:text-accent-300 dark:hover:bg-dark-800/70'"
            @click="editorMode = 'images'"
          >
            <span class="block font-semibold">编辑图片</span>
            <span class="mt-1 block text-xs opacity-75">翻译、处理和管理商品图片</span>
          </button>
        </nav>
        <ProductEditorPanel
          v-if="editorMode === 'text'"
          :product="product"
          :loading="loading"
          :focus-sku-id="focusProductSku"
          @save="saveProductFacts"
          @assign-upc="store.assignUpc"
        />
        <ProductImageEditorPanel
          v-else
          :title="imageEditorTitle"
          :product="product"
          :images="imagePool"
          :loading="loading"
          :error="error"
          @translate="translateEditorImages"
          @image-edit="editEditorImages"
          @upload="store.uploadReferenceImages"
          @save="store.saveCurrentImagePool"
          @save-sku-images="store.saveCurrentProduct"
          @set-main="store.setMainImage"
          @delete="store.deleteImages"
          @clear="store.clearSourceImages"
        />
      </div>
    </div>
    <div
      v-if="draftWorkspaceOpen"
      class="fixed inset-0 z-50 flex items-center justify-center overflow-hidden bg-slate-950/50 p-2 backdrop-blur-sm sm:p-4"
      @pointerdown="recordDraftWorkspaceBackdropPointer"
      @pointerup="closeDraftWorkspaceFromBackdrop"
      @pointercancel="resetDraftWorkspaceBackdropPointer"
    >
      <div role="dialog" aria-modal="true" :aria-label="draftWorkspaceTitle" class="h-full min-h-0 w-full max-w-[96rem] overflow-hidden rounded-2xl bg-white p-3 shadow-2xl ring-1 ring-slate-200 dark:bg-dark-900 dark:ring-dark-700 sm:p-5">
        <DraftWorkspacePanel
          :active-tab="draftWorkspaceTab"
          :draft-title="draftWorkspaceTitle"
          :draft-id="currentDraft.draftId"
          :sync-message="draftSyncMessage"
          :error-message="['pricing', 'images'].includes(draftWorkspaceTab) ? '' : error"
          :refreshing="draftRefreshing"
          :reload-disabled="loading || imageWorkbenchBusy"
          :editing-disabled="imageWorkbenchBusy"
          @reload="reloadDraft"
          @update-active-tab="draftWorkspaceTab = $event"
          @close="closeDraftWorkspace"
        >
          <template #actions>
            <span v-if="draftWorkspaceTab === 'text' && modelOperationReason('copy.generate')" role="status" class="muted">{{ modelOperationReason('copy.generate') }}</span>
            <button v-if="draftWorkspaceTab === 'text'" v-desktop="'copy.generate'" class="btn btn-outline" :disabled="draftWorkspaceLoading || !!modelOperationReason('copy.generate') || !(currentDraft.productId || currentDraftProductContext.productId)" @click="() => store.generateCopy(true)">
              {{ copyGenerating ? '正在生成本地化文案…' : '生成/改写本地化文案' }}
            </button>
            <button class="btn btn-primary" :disabled="loading || imageWorkbenchBusy || !currentDraft.draftId" :title="draftSaveBlockedReason" @click="imageSaveState.save">{{ draftWorkspaceTab === 'images' ? '保存图片设置' : '保存草稿' }}</button>
          </template>

          <template #text>
            <DraftEditorPanel
              embedded
              :draft="currentDraft"
              :product-context="currentDraftProductContext"
              :platform-options="platformOptions"
              :store-config="storeConfig"
              :loading="draftWorkspaceLoading"
              @update-language="store.updateDraftLanguage"
              @update-targets="store.updateDraftTargets"
            />
          </template>

          <template #skus>
            <DraftSkuPanel :categories="Object.fromEntries(store.targetEditors.map(editor => [editor.key, editor.state.category]))" :draft="currentDraft" :skus="currentDraftProductContext.skuItems" :images="currentDraftProductContext.imagePool" :loading="draftWorkspaceLoading" @edit-product="editDraftProduct">
              <template #attributes="{ row, sku, targetKey }">
                <DraftSkuAttributesEditor :sku-id="row.sku_id" :sku="sku" :target-key="targetKey" />
              </template>
            </DraftSkuPanel>
          </template>

          <template #images>
            <DraftImageWorkbench
              :draft="currentDraft"
              :product-context="currentDraftProductContext"
              :loading="draftWorkspaceLoading"
              :controller="imageWorkbench"
            />
          </template>

          <template #category>
            <DraftCategoryWorkspace :key="currentDraft.draftId" @update-package-dimension="syncPricingPackageDimension" />
          </template>

          <template #pricing>
            <div class="grid min-w-0 gap-6">
              <PricingPanel
                :key="currentDraft.draftId"
                :sku-items="currentDraft.skuItems"
                selection-locked
                :input="pricingInput"
                :result="pricingResult"
                :draft-items="pricingDraftItems"
                :draft-id="currentDraft.draftId"
                :draft-version="currentDraft.updatedAt"
                :saved-pricing="currentDraft.pricing"
                :draft-title="draftWorkspaceTitle"
                :product-context="currentDraftProductContext"
                :platform-options="platformOptions"
                :loading="draftWorkspaceLoading"
                :store-config="storeConfig"
                :error="error"
                :refresh-product="() => draftSync.refresh()"
                @edit-product="editDraftProduct"
                @calculate="store.calculatePrice"
                @restore="store.restorePricingPreview"
                @apply="store.applyPrice"
              />
            </div>
          </template>

          <template #precheck>
            <DraftPublishPrecheckWorkspace :key="currentDraft.draftId" />
          </template>
        </DraftWorkspacePanel>
      </div>
    </div>
  </div>
</template>
