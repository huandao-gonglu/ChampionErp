<script setup lang="ts">
import { draftContextTitle, draftListTitle } from '@/utils/draftDisplayTitle'
import { modelOperationReason } from '@/utils/desktop'
import { vDesktop } from '@/utils/desktopHandoffDirective'
import { useDesktopHandoffContext, receivedDesktopHandoff, acknowledgeDesktopHandoff, type DesktopWorkflowState } from '@/utils/desktopHandoff'
import { computed, nextTick, onActivated, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { until } from '@vueuse/core'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import {
  PhArrowLeft,
  PhArrowSquareOut,
  PhCheck,
  PhCheckCircle,
  PhCopy,
  PhFloppyDisk,
  PhImage,
  PhWarningCircle,
  PhX,
} from '@phosphor-icons/vue'
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
import AlibabaDirectPurchaseView from '@/views/workflow/AlibabaDirectPurchaseView.vue'
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
  libraryRows,
  draftsIndex,
  selectedProductIds,
  currentDraft,
  currentDraftProductContext,
} = storeToRefs(catalogStore)
const imageSaveState = useDraftImageSaveState(currentDraft, () => store.saveCurrentDraft())
const {
  collectForm,
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
const productEditorTab = ref<'basic' | 'skus' | 'attributes' | 'images'>('basic')
const productAttributesValid = ref(true)
const productEditorSnapshot = ref('')
const productEditorNotice = ref('')
const draftWorkspaceOpen = ref(false)
let listScrollBeforeEditor = 0
let listScrollBeforeDraft = 0
const draftImageEditor = ref<InstanceType<typeof DraftImageWorkbench>>()
const productImageEditor = ref<InstanceType<typeof ProductImageEditorPanel>>()
const draftWorkspaceTab = ref<DraftWorkspaceTab>('text')
useDesktopHandoffContext(() => {
  if (route.path !== '/') return null
  const workflow: DesktopWorkflowState = {}
  if (draftWorkspaceOpen.value) {
    workflow.draft = currentDraft.value
    workflow.draftContext = currentDraftProductContext.value
    workflow.draftTab = draftWorkspaceTab.value
    workflow.draftImageAction = draftImageEditor.value?.pendingImageAction()
    workflow.pricingInput = pricingInput.value
  }
  if (editorOpen.value) {
    workflow.product = product.value
    workflow.editorMode = editorMode.value
    workflow.productImageAction = productImageEditor.value?.pendingImageAction()
  }
  if (activeNav.value === 'collect') {
    workflow.collect = { mode: collectForm.value.mode, productUrl: collectForm.value.productUrl, platform: collectForm.value.platform }
  }
  return { target: route.fullPath, state: Object.keys(workflow).length ? { workflow } : {} }
})
let restoringHandoff = ''
watch([receivedDesktopHandoff, initialStateLoaded, () => route.fullPath, loading, () => collectionStore.running], async ([handoff, loaded]) => {
  if (!handoff || !loaded || route.fullPath !== handoff.target || !handoff.state.workflow || restoringHandoff === handoff.requestId) return
  if (handoff.state.workflow.collectAction && (loading.value || collectionStore.running)) return
  if ((handoff.state.workflow.draftImageAction?.translationRequestId || handoff.state.workflow.draftImageSetAction) && loading.value) return
  restoringHandoff = handoff.requestId
  try {
    const state = handoff.state.workflow as DesktopWorkflowState
    const imageAction = state.draftImageAction
    const imageSetAction = state.draftImageSetAction
    const translationId = imageAction?.action === 'translate' && imageAction.translationRequestId
    if (state.collect) {
      Object.assign(collectForm.value, state.collect, { mode: state.collect.mode === 'api' ? 'api' : 'browser' })
    }
    if (state.draft?.draftId && state.draftContext) {
      const saved = await loadDraftForHandoff(state.draft.draftId)
      if (receivedDesktopHandoff.value?.requestId !== handoff.requestId) return
      store.acceptLoadedDraft(saved)
      await nextTick()
      // 建立服务端基线后恢复本地编辑，沿用原来的脏状态和版本校验。
      if (!translationId && !imageSetAction) {
        Object.assign(currentDraft.value, state.draft)
        if (state.pricingInput) Object.assign(pricingInput.value, state.pricingInput)
        currentDraftProductContext.value = state.draftContext
      }
      store.setMarketplace(state.draft.platform)
      draftWorkspaceTab.value = state.draftTab || 'text'
      draftWorkspaceOpen.value = true
    }
    if (state.product) {
      product.value = state.product
      editorMode.value = state.editorMode || 'text'
      editorOpen.value = true
    }
    if (state.draftImageAction || state.draftImageSetAction || state.productImageAction) {
      await nextTick()
      if (receivedDesktopHandoff.value?.requestId !== handoff.requestId) return
      if (translationId && state.draft?.draftId && imageAction) {
        const draft = state.draft
        const isCurrent = () => receivedDesktopHandoff.value?.requestId === handoff.requestId
          && route.fullPath === handoff.target && draftWorkspaceOpen.value && currentDraft.value.draftId === draft.draftId
        await until(() => !isCurrent() || (!imageWorkbench.checking.value && !loading.value)).toBe(true)
        if (isCurrent()) await imageWorkbench.resumeTranslation(imageAction.imageIds, translationId, draft.updatedAt)
      } else if (imageAction) draftImageEditor.value?.restoreImageAction(imageAction)
      if (imageSetAction && state.draft?.draftId) {
        const draft = state.draft
        const isCurrent = () => receivedDesktopHandoff.value?.requestId === handoff.requestId
          && route.fullPath === handoff.target && draftWorkspaceOpen.value && currentDraft.value.draftId === draft.draftId
        await until(() => !isCurrent() || (!imageWorkbench.checking.value && !loading.value)).toBe(true)
        if (isCurrent()) {
          draftImageEditor.value?.restoreImageSetAction(imageSetAction)
          await imageWorkbench.resumeImageSet(imageSetAction)
        }
      }
      if (state.productImageAction) productImageEditor.value?.restoreImageAction(state.productImageAction)
    }
    await acknowledgeDesktopHandoff(handoff.requestId, 'workflow')
    if (state.collectAction?.type === 'single') await store.collectProduct()
    else if (state.collectAction?.type === 'batch') await collectionStore.runLinks(state.collectAction.targets)
  } catch (cause) {
    activityStore.setError(cause instanceof Error ? cause.message : '桌面页面恢复失败，请回网页重试。')
  } finally { if (restoringHandoff === handoff.requestId) restoringHandoff = '' }
})
const draftSync = useAiDraftSync({
  active: draftWorkspaceOpen,
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
const editorMode = computed<'text' | 'images'>({
  get: () => (productEditorTab.value === 'images' ? 'images' : 'text'),
  set: (value) => { productEditorTab.value = value === 'images' ? 'images' : 'basic' },
})
const productEditorDirty = computed(() => Boolean(editorOpen.value && productEditorSnapshot.value && JSON.stringify(product.value) !== productEditorSnapshot.value))
const productEditorMainImage = computed(() => {
  const pool = imagePool.value
  const main = pool.find((img) => img.isMain) || pool[0]
  return main ? (main.previewUrl || main.url || '') : ''
})
const productEditorMissingSkuCount = computed(() => product.value.skuItems.filter((sku) => {
  if (!sku.active) return false
  const positive = (v: unknown) => { const n = Number(String(v ?? '').trim()); return Number.isFinite(n) && n > 0 }
  return !positive(sku.cost_cny)
    || !positive(sku.package_dimensions?.length_cm)
    || !positive(sku.package_dimensions?.width_cm)
    || !positive(sku.package_dimensions?.height_cm)
    || !positive(sku.package_dimensions?.weight_kg)
}).length)
const productEditorAttributeCount = computed(() => Object.keys(product.value.attributes || {}).length + Object.keys(product.value.source.attributes || {}).length)
const productEditorBackLabel = computed(() => {
  if (draftWorkspaceOpen.value) return '返回草稿编辑'
  const current = navItems.find((item) => item.key === activeNav.value)
  return current ? `返回${current.title}` : '返回列表'
})
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

function scrollWindowTo(y: number) {
  if (window.scrollY !== y) window.scrollTo(0, y)
}

async function openProductEditor(item?: Pick<ProductIndexItem, 'productId' | 'productFilePath'>, mode: 'text' | 'images' = 'text') {
  if (item) {
    editorOpen.value = false
    if (!await store.loadProduct(item)) return
  }
  listScrollBeforeEditor = window.scrollY
  focusProductSku.value = ''
  productEditorTab.value = mode === 'images' ? 'images' : 'basic'
  productEditorNotice.value = ''
  productEditorSnapshot.value = JSON.stringify(product.value)
  imageEditorTitle.value = '采集箱图片编辑'
  editorOpen.value = true
  await nextTick()
  scrollWindowTo(0)
}

async function editDraftProduct(skuId: string) {
  if (loading.value || !currentDraft.value.productId) return
  const draftId = currentDraft.value.draftId
  loading.value = true
  try {
    const result = await loadProductForEditor(currentDraft.value.productId)
    if (currentDraft.value.draftId !== draftId) return
    listScrollBeforeEditor = window.scrollY
    product.value = result.product
    focusProductSku.value = skuId
    productEditorTab.value = 'skus'
    productEditorNotice.value = ''
    productEditorSnapshot.value = JSON.stringify(product.value)
    editorOpen.value = true
    await nextTick()
    scrollWindowTo(0)
  } catch (cause) { activityStore.setError(cause instanceof Error ? cause.message : '读取商品资料失败') }
  finally { loading.value = false }
}
async function saveProductFacts() {
  productEditorNotice.value = ''
  const input = structuredClone(JSON.parse(JSON.stringify(pricingInput.value)))
  const saved = await store.saveCurrentProduct()
  if (saved) {
    productEditorSnapshot.value = JSON.stringify(product.value)
    productEditorNotice.value = '商品资料已保存。'
  }
  if (draftWorkspaceOpen.value) {
    pricingInput.value = input
    if (saved) await draftSync.refresh()
  }
}

async function openProductImageEditor(item?: ProductIndexItem) {
  await openProductEditor(item, 'images')
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

const openingDraftId = ref('')
async function openDraftWorkspace(item: DraftIndexItem, tab: DraftWorkspaceTab = 'text') {
  store.setMarketplace(item.platform)
  openingDraftId.value = item.draftId
  const loaded = await store.loadDraft(item)
  openingDraftId.value = ''
  if (!loaded || activeNav.value !== 'drafts') return
  listScrollBeforeDraft = window.scrollY
  draftWorkspaceTab.value = tab
  draftWorkspaceOpen.value = true
  await nextTick()
  scrollWindowTo(0)
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
  if (productEditorDirty.value && !window.confirm('商品资料有未保存修改，离开将放弃这些更改。确认返回？')) return false
  editorOpen.value = false
  productEditorNotice.value = ''
  productEditorSnapshot.value = ''
  void nextTick(() => scrollWindowTo(listScrollBeforeEditor))
  return true
}

function closeDraftWorkspace() {
  if (imageSaveState.dirty.value && !window.confirm('图片设置尚未保存，离开将放弃这些修改。确认返回？')) return false
  if (draftSync.dirty.value && !window.confirm('草稿内容或平台属性有未保存修改，离开将放弃这些修改。确认返回？')) return false
  draftWorkspaceOpen.value = false
  const row = draftsIndex.value.find(item => item.draftId === currentDraft.value.draftId)
  if (activeNav.value === 'drafts' && row?.updatedAt !== currentDraft.value.updatedAt) void store.refreshDraftsIndex()
  void nextTick(() => scrollWindowTo(listScrollBeforeDraft))
  return true
}

async function copyProductId() {
  if (!product.value.productId) return
  await copyToClipboard(product.value.productId)
}

let settingsLoad: Promise<void> | undefined
let fullStateLoad: Promise<void> | undefined
let navigationSequence = 0
async function refreshDomainForNav(key: string) {
  const sequence = ++navigationSequence
  if (['drafts', 'library', 'collect', 'orders', 'onlineProducts', 'publish', 'research', 'directPurchase'].includes(key)) {
    const configReady = fullStateLoad ?? (settingsLoad ??= store.loadState(true))
    await Promise.all([configReady, store.hydrateTab(key)])
  } else {
    fullStateLoad ??= settingsLoad
      ? settingsLoad.then(() => product.value.productId ? undefined : store.loadState())
      : store.loadState()
    const earlyLoad = key === 'auth' || key === 'dashboard' ? store.hydrateTab(key) : undefined
    await fullStateLoad
    if (sequence !== navigationSequence) return
    await (earlyLoad ?? store.hydrateTab(key))
  }
  if (sequence === navigationSequence) initialStateLoaded.value = true
}

function navigate(key: string) {
  if (editorOpen.value && !closeProductEditor()) return
  if (draftWorkspaceOpen.value && !closeDraftWorkspace()) return
  if (key === 'publish' && activeNav.value === key) return
  const nextQuery = key === 'dashboard' ? {} : { tab: key }
  if (route.path !== '/' || String(route.query.tab || '') !== String(nextQuery.tab || '')) {
    void router.push({ name: 'WorkflowHome', query: nextQuery })
  } else {
    void refreshDomainForNav(key)
  }
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

const libraryNotice = ref('')
const draftsNotice = ref('')

async function claimAndOpenDrafts(productIds: string[], targets: MarketplaceTargetSite[]) {
  const ok = await store.claimProductsToDrafts(productIds, targets)
  if (ok) navigate('drafts')
}

async function handleExportLibraryProducts(productIds: string[]) {
  libraryNotice.value = `正在打包导出 ${productIds.length} 个商品（ZIP），请稍候…`
  const message = await store.exportSelectedProducts(productIds)
  libraryNotice.value = message || ''
}

async function handleImportLibraryProducts(file: File) {
  libraryNotice.value = '正在校验并导入商品压缩包（ZIP），请稍候…'
  const message = await store.importProductsFromZip(file)
  libraryNotice.value = message || ''
}

async function handleExportDrafts(draftIds: string[]) {
  draftsNotice.value = `正在打包导出 ${draftIds.length} 份草稿（ZIP），请稍候…`
  const message = await store.exportSelectedDrafts(draftIds)
  draftsNotice.value = message || ''
}

async function handleImportDrafts(file: File) {
  draftsNotice.value = '正在校验并导入草稿压缩包（ZIP），请稍候…'
  const message = await store.importDraftsFromZip(file)
  draftsNotice.value = message || ''
}

function warnUnsavedImages(event: BeforeUnloadEvent) {
  if (!draftWorkspaceOpen.value || (!imageSaveState.dirty.value && !draftSync.dirty.value)) return
  event.preventDefault()
  event.returnValue = ''
}
onMounted(async () => {
  window.addEventListener('beforeunload', warnUnsavedImages)
  window.addEventListener('focus', checkDraftUpdates)
  await refreshDomainForNav(activeNav.value)
})

onActivated(() => {
  checkDraftUpdates()
  if (initialStateLoaded.value && activeNav.value === 'publish') void refreshDomainForNav('publish')
})
onBeforeUnmount(() => {
  draftWorkspaceOpen.value = false
  window.removeEventListener('beforeunload', warnUnsavedImages)
  window.removeEventListener('focus', checkDraftUpdates)
})


watch(
  () => route.query,
  () => {
    const tab = String(route.query.tab || '')
    const previous = activeNav.value
    activeNav.value = navItems.some((item) => item.key === tab) ? tab : 'dashboard'
    if (previous !== activeNav.value) {
      editorOpen.value = false
      draftWorkspaceOpen.value = false
      if (settingsLoad || fullStateLoad) void refreshDomainForNav(activeNav.value)
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="min-h-dvh bg-accent-100 dark:bg-dark-900">
    <div class="min-h-dvh items-start md:grid md:grid-cols-[auto_minmax(0,1fr)]">
      <div class="sticky top-0 hidden md:block">
        <AppSidebar
          :items="navItems"
          :active-key="activeNav"
          :collapsed="appStore.sidebarCollapsed"
          @navigate="navigate"
          @toggle-collapse="appStore.setSidebarCollapsed(!appStore.sidebarCollapsed)"
        />
      </div>

      <main class="workflow-main min-w-0" :class="{ 'workflow-main--collect': activeNav === 'collect' && !editorOpen && !draftWorkspaceOpen }">
        <div class="workflow-mobile-nav sticky top-0 z-20 flex shrink-0 items-center border-b border-accent-200 bg-white dark:border-dark-700 dark:bg-dark-900 md:hidden">
          <select :value="activeNav" aria-label="工作区导航" class="input h-9 max-w-sm !py-0" @change="navigate(($event.target as HTMLSelectElement).value)">
            <option v-for="item in navItems" :key="item.key" :value="item.key">{{ item.title }}</option>
          </select>
        </div>

        <div class="workflow-content py-4">
          <!-- 主导航视图层（进入商品/草稿编辑时通过 v-show 隐藏，返回时完整保留列表筛选、勾选与状态） -->
          <div
            v-show="!editorOpen && !draftWorkspaceOpen"
            :class="activeNav === 'collect' ? 'flex min-h-0 flex-1 flex-col' : ''"
          >
            <DashboardView
              v-if="activeNav === 'dashboard'"
              :product="product"
              :products-index="productsIndex"
              :selected-ids="selectedProductIds"
              :progress-percent="progressPercent"
              :auth-checklist="mercadolibreAuthChecklist"
              :publish-job="publishJob"
              :logs="logs"
              :loading="loading || store.productsRefreshing"
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
              :loading="loading"
              :error="error"
              @open-product="id => openProductEditor({ productId: id, productFilePath: '' })"
              @open-library="navigate('library')"
              @save-settings="store.saveCollectSettings"
            />

            <LibraryPanel
              v-else-if="activeNav === 'library'"
              :items="libraryRows"
              :selected-ids="selectedProductIds"
              :platform-options="platformOptions"
              :store-config="storeConfig"
              :loading="loading"
              :refreshing="store.productsRefreshing"
              :error="error"
              :notice="libraryNotice"
              @refresh="store.refreshProductsIndex('list')"
              @claim="claimAndOpenDrafts"
              @edit="openProductEditor"
              @delete-item="store.deleteProduct"
              @delete-selected="store.deleteSelectedProducts"
              @export-products="handleExportLibraryProducts"
              @import-products="handleImportLibraryProducts"
              @clear-notice="libraryNotice = ''"
              @toggle="store.toggleProductSelection"
              @select-all="store.selectAllProducts"
            />

            <div v-else-if="activeNav === 'drafts'" class="space-y-6">
              <DraftBoxPanel
                :drafts="draftsIndex"
                :refreshing="store.draftsRefreshing"
                :opening-draft-id="openingDraftId"
                :platform-options="platformOptions"
                :store-config="storeConfig"
                :loading="loading"
                :error="error"
                :notice="draftsNotice"
                @refresh="store.refreshDraftsIndex"
                @update-language="store.updateDraftLanguage"
                @edit="openDraftWorkspace"
                @duplicate-draft="store.duplicateDraft"
                @delete-draft="deleteDraft"
                @delete-drafts="deleteDrafts"
                @export-drafts="handleExportDrafts"
                @import-drafts="handleImportDrafts"
                @clear-notice="draftsNotice = ''"
                @update-targets="store.updateDraftTargets"
              />
            </div>

            <TaskQueuePanel v-else-if="activeNav === 'publish'" :initial-task-id="String(route.query.task_id || '')" :initial-source="String(route.query.source || '')" @close-task="closeTaskDetail" />

            <SystemSettingsPanel v-else-if="activeNav === 'systemSettings'" />
            <OrderCenterPanel v-else-if="activeNav === 'orders'" />
            <OnlineProductsPanel v-else-if="activeNav === 'onlineProducts'" @open-task="openOnlineTask" />
            <AlibabaDirectPurchaseView v-else-if="activeNav === 'directPurchase'" @open-auth="navigate('auth')" />

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

          <!-- 原地商品编辑工作台（占满主区宽度，无全屏遮罩弹窗） -->
          <section
            v-if="editorOpen"
            aria-label="商品编辑工作台"
            class="flex min-w-0 flex-col rounded-2xl border border-accent-200/90 bg-accent-50/70 shadow-sm dark:border-dark-700 dark:bg-dark-900/70"
          >
            <header class="product-editor-sticky border-b border-accent-200/90 bg-white/95 px-4 pt-3.5 backdrop-blur-sm dark:border-dark-700 dark:bg-dark-900/95 sm:px-5">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <div class="flex min-w-0 flex-1 items-center gap-3">
                  <button
                    type="button"
                    data-testid="product-editor-back"
                    class="btn btn-outline !min-h-9 shrink-0 !rounded-xl !px-3 !py-1.5 text-sm"
                    @click="closeProductEditor"
                  >
                    <PhArrowLeft :size="15" aria-hidden="true" />{{ productEditorBackLabel }}
                  </button>
                  <span class="h-5 w-px shrink-0 bg-accent-200 dark:bg-dark-700" aria-hidden="true" />
                  <img
                    v-if="productEditorMainImage"
                    :src="productEditorMainImage"
                    alt=""
                    class="size-10 shrink-0 rounded-xl border border-accent-200 object-cover dark:border-dark-700"
                  />
                  <div v-else class="grid size-10 shrink-0 place-items-center rounded-xl border border-accent-200 bg-accent-100 text-accent-400 dark:border-dark-700 dark:bg-dark-800">
                    <PhImage :size="18" aria-hidden="true" />
                  </div>
                  <div class="min-w-0 flex-1">
                    <h2 class="truncate text-base font-bold text-accent-950 dark:text-white" :title="product.source.title || product.name || product.productId || '商品编辑'">
                      {{ product.source.title || product.name || product.productId || '商品编辑' }}
                    </h2>
                    <div class="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-accent-500 dark:text-accent-400">
                      <button
                        type="button"
                        class="inline-flex items-center gap-1 rounded-md bg-accent-100 px-1.5 py-0.5 font-mono text-[11px] text-accent-600 transition hover:bg-accent-200/70 hover:text-accent-900 disabled:opacity-50 dark:bg-dark-800 dark:text-accent-300 dark:hover:bg-dark-700"
                        :disabled="!product.productId"
                        :title="product.productId || '当前商品暂无 ID'"
                        @click="copyProductId"
                      >
                        <span>{{ product.productId || '无商品 ID' }}</span>
                        <PhCheck v-if="productIdCopied" :size="11" class="text-primary-600 dark:text-primary-400" aria-hidden="true" />
                        <PhCopy v-else :size="11" aria-hidden="true" />
                      </button>
                      <a
                        v-if="product.source.sourceUrl"
                        :href="product.source.sourceUrl"
                        target="_blank"
                        rel="noopener noreferrer"
                        class="inline-flex items-center gap-1 text-[11px] text-accent-500 transition hover:text-primary-700 hover:underline dark:text-accent-400 dark:hover:text-primary-300"
                      >
                        来源链接 <PhArrowSquareOut :size="11" aria-hidden="true" />
                      </a>
                      <span v-if="productEditorDirty" class="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-300">
                        有未保存修改
                      </span>
                    </div>
                  </div>
                </div>

                <div class="flex shrink-0 flex-wrap items-center gap-2">
                  <button
                    v-if="productEditorTab !== 'images'"
                    type="button"
                    class="btn btn-primary !min-h-9 !rounded-xl !px-3.5 !py-1.5 text-sm"
                    :disabled="loading || !productAttributesValid"
                    @click="saveProductFacts"
                  >
                    <PhFloppyDisk :size="15" aria-hidden="true" />保存商品
                  </button>
                </div>
              </div>

              <!-- 4 分区工作台导航 -->
              <nav class="mt-3 flex gap-1 overflow-x-auto" role="tablist" aria-label="商品编辑选项">
                <button
                  type="button"
                  role="tab"
                  :aria-selected="productEditorTab === 'basic'"
                  class="product-editor-tab"
                  @click="productEditorTab = 'basic'"
                >
                  基础资料
                </button>
                <button
                  type="button"
                  role="tab"
                  :aria-selected="productEditorTab === 'skus'"
                  class="product-editor-tab"
                  @click="productEditorTab = 'skus'"
                >
                  规格与 SKU
                  <span class="product-editor-badge tabular-nums">{{ product.skuItems.length }}</span>
                  <span
                    v-if="productEditorMissingSkuCount"
                    class="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-amber-800 tabular-nums dark:bg-amber-500/20 dark:text-amber-200"
                  >
                    缺 {{ productEditorMissingSkuCount }}
                  </span>
                </button>
                <button
                  type="button"
                  role="tab"
                  :aria-selected="productEditorTab === 'attributes'"
                  class="product-editor-tab"
                  @click="productEditorTab = 'attributes'"
                >
                  商品属性
                  <span class="product-editor-badge tabular-nums">{{ productEditorAttributeCount }}</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  :aria-selected="productEditorTab === 'images'"
                  class="product-editor-tab"
                  @click="productEditorTab = 'images'"
                >
                  图片素材
                  <span class="product-editor-badge tabular-nums">{{ imagePool.length }}</span>
                </button>
              </nav>
            </header>

            <div v-if="error || productEditorNotice" class="space-y-2 px-4 pt-3 sm:px-5">
              <div
                v-if="error"
                role="alert"
                class="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2 text-sm font-medium text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
              >
                <PhWarningCircle :size="17" weight="fill" class="mt-0.5 shrink-0 text-rose-500 dark:text-rose-400" aria-hidden="true" />
                <span class="min-w-0 break-words">{{ error }}</span>
              </div>
              <div
                v-else-if="productEditorNotice"
                role="status"
                class="flex items-center justify-between gap-2 rounded-xl border border-primary-200/80 bg-primary-50/80 px-3.5 py-2 text-xs font-medium text-primary-800 dark:border-primary-500/25 dark:bg-primary-500/10 dark:text-primary-200"
              >
                <span class="inline-flex items-center gap-2"><PhCheckCircle :size="15" weight="fill" class="text-primary-600 dark:text-primary-400" aria-hidden="true" />{{ productEditorNotice }}</span>
                <button type="button" aria-label="关闭保存提示" class="rounded p-0.5 opacity-70 hover:opacity-100" @click="productEditorNotice = ''">
                  <PhX :size="13" aria-hidden="true" />
                </button>
              </div>
            </div>

            <div class="min-w-0 p-4 sm:p-5">
              <ProductEditorPanel
                v-if="productEditorTab !== 'images'"
                embedded
                :active-section="productEditorTab"
                :product="product"
                :loading="loading"
                :focus-sku-id="focusProductSku"
                @validity-change="productAttributesValid = $event"
                @save="saveProductFacts"
                @assign-upc="store.assignUpc"
              />
              <ProductImageEditorPanel
                v-else
                ref="productImageEditor"
                embedded
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
          </section>

          <!-- 原地草稿编辑工作台（占满主区宽度；从草稿内跳转补商品 SKU 时通过 v-show 保留上下文） -->
          <div v-if="draftWorkspaceOpen" v-show="!editorOpen">
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
                <button v-if="draftWorkspaceTab === 'text'" v-desktop="'copy.generate'" class="btn btn-outline !min-h-9 !rounded-xl !px-3 !py-1.5 text-sm" :disabled="draftWorkspaceLoading || !!modelOperationReason('copy.generate') || !(currentDraft.productId || currentDraftProductContext.productId)" @click="() => store.generateCopy(true)">
                  {{ copyGenerating ? '正在生成本地化文案…' : '生成/改写本地化文案' }}
                </button>
                <button class="btn btn-primary !min-h-9 !rounded-xl !px-3.5 !py-1.5 text-sm" :disabled="loading || imageWorkbenchBusy || !currentDraft.draftId" :title="draftSaveBlockedReason" @click="imageSaveState.save">{{ draftWorkspaceTab === 'images' ? '保存图片设置' : '保存草稿' }}</button>
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
                  ref="draftImageEditor"
                  :draft="currentDraft"
                  :product-context="currentDraftProductContext"
                  :loading="draftWorkspaceLoading"
                  :controller="imageWorkbench"
                />
              </template>

              <template #category>
                <DraftCategoryWorkspace
                  :key="currentDraft.draftId"
                  @update-package-dimension="syncPricingPackageDimension"
                  @open-sku-tab="draftWorkspaceTab = 'skus'"
                />
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
      </main>
    </div>
  </div>
</template>

<style scoped>
.workflow-main {
  --workflow-mobile-nav-height: 0px;
}
.workflow-content, .workflow-mobile-nav {
  padding-inline: 20px;
}
.workflow-main--collect {
  @apply flex min-h-dvh flex-col;
}
.workflow-main--collect > .workflow-content {
  @apply flex min-h-0 flex-1 flex-col;
}
@media (width < 768px) {
  .workflow-main {
    --workflow-mobile-nav-height: 56px;
  }
}
.workflow-mobile-nav {
  block-size: var(--workflow-mobile-nav-height);
}
@media (height >= 560px) {
  .workflow-main--collect {
    block-size: 100dvh;
  }
}
.product-editor-sticky {
  position: sticky;
  inset-block-start: var(--workflow-mobile-nav-height, 0px);
  z-index: 20;
  border-top-left-radius: 1rem;
  border-top-right-radius: 1rem;
}
.product-editor-tab {
  @apply inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 border-transparent px-3 py-2.5 text-xs font-medium text-accent-600 transition hover:text-accent-950 dark:text-accent-300 dark:hover:text-white;
}
.product-editor-tab[aria-selected='true'] {
  @apply border-primary-500 font-semibold text-primary-700 dark:text-primary-300;
}
.product-editor-badge {
  @apply inline-flex min-w-5 items-center justify-center rounded-full bg-accent-100 px-1.5 py-0.5 text-[11px] font-semibold leading-none text-accent-600 dark:bg-dark-800 dark:text-accent-300;
}
.product-editor-tab[aria-selected='true'] .product-editor-badge {
  @apply bg-primary-100 text-primary-800 dark:bg-primary-500/20 dark:text-primary-200;
}
</style>
