<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AuthSettingsPanel from '@/components/auth/AuthSettingsPanel.vue'
import CategoryAttributesPanel from '@/components/domain/CategoryAttributesPanel.vue'
import PublishPrecheckPanel from '@/components/domain/PublishPrecheckPanel.vue'
import CollectView from '@/views/workflow/CollectView.vue'
import DashboardView from '@/views/workflow/DashboardView.vue'
import DraftBoxPanel from '@/components/domain/DraftBoxPanel.vue'
import DraftEditorPanel from '@/components/domain/DraftEditorPanel.vue'
import DraftWorkspacePanel, { type DraftWorkspaceTab } from '@/components/domain/DraftWorkspacePanel.vue'
import LibraryPanel from '@/components/domain/LibraryPanel.vue'
import MercadoLibrePublishedPanel from '@/components/domain/MercadoLibrePublishedPanel.vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import PricingChart from '@/components/domain/PricingChart.vue'
import PricingPanel from '@/components/domain/PricingPanel.vue'
import ProductImageEditorPanel from '@/components/domain/ProductImageEditorPanel.vue'
import ProductEditorPanel from '@/components/domain/ProductEditorPanel.vue'
import ProductResearchPanel from '@/components/domain/ProductResearchPanel.vue'
import PublishJobsPanel from '@/components/domain/PublishJobsPanel.vue'
import RunLog from '@/components/domain/RunLog.vue'
import { workflowNavItems } from '@/constants/navigation'
import { useClipboard } from '@/composables/useClipboard'
import { useBackdropDismiss } from '@/composables/useBackdropDismiss'
import { useAppStore } from '@/stores/app'
import { useWorkflowStore } from '@/stores/workflow'
import { useWorkflowActivityStore } from '@/stores/workflow/activity'
import { useWorkflowCatalogStore } from '@/stores/workflow/catalog'
import { useWorkflowCollectionStore } from '@/stores/workflow/collection'
import { useWorkflowPublishingStore } from '@/stores/workflow/publishing'
import { useWorkflowSettingsStore } from '@/stores/workflow/settings'
import type { DraftIndexItem, ProductIndexItem, UnknownRecord } from '@/types/workflow'

const store = useWorkflowStore()
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
const {
  collectForm,
  collectDiagnostics,
  collectBatchRows,
  browserDebugStatus,
} = storeToRefs(collectionStore)
const {
  pricingInput,
  pricingResult,
  category,
  categoryQuery,
  categoryResults,
  categoryAutoMatching,
  categoryAutoMatchMessage,
  categoryAutoMatchCurrent,
  categoryAutoMatchTotal,
  categoryAutoMatchProductName,
  categoryAttributeTranslations,
  categoryAttributeTranslationsSource,
  categoryAttributeTranslating,
  categoryAttributeLoading,
  categoryAttributeError,
  categoryResultTranslations,
  categoryResultTranslationsSource,
  categoryResultTranslating,
  categoryPrecheck,
  precheck,
  payloadPreview,
  copyGenerating,
  publishJob,
  publishJobStatus,
  publishJobs,
  selectedPublishJobId,
  publishJobsNextCursor,
  publishJobsLoading,
  publishJobsLastUpdated,
  publishLogs,
  mercadoLibreOrders,
  mercadoLibreOrderNotifications,
  mercadoLibreOrdersTotal,
  mercadoLibreOrdersCheckedAt,
  mercadoLibreUserProducts,
  mercadoLibreUserProductStatus,
  mercadoLibreUserProductPage,
  mercadoLibreUserProductPerPage,
  mercadoLibreUserProductTotal,
  mercadoLibreUserProductTotalPages,
  mercadoLibreUserProductRefreshErrors,
  mercadoLibreUserProductsRefreshScope,
  mercadoLibreUserProductsCheckedAt,
  publishResult,
  activeMarketplace,
  platformOptions,
} = storeToRefs(publishingStore)
const {
  appConfig,
  aiConfig,
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
  categoryAutoMatchTargetError,
  currentPublishTargets,
  selectedPublishTarget,
  workflowSteps,
  progressPercent,
  imagePool,
} = storeToRefs(store)

const appStore = useAppStore()
const route = useRoute()
const router = useRouter()
const { copied: productIdCopied, copy: copyToClipboard } = useClipboard()
const activeNav = ref('dashboard')
let initialStateLoaded = false
const editorOpen = ref(false)
const draftWorkspaceOpen = ref(false)
const draftWorkspaceTab = ref<DraftWorkspaceTab>('text')
const draftWorkspaceItem = ref<DraftIndexItem | null>(null)
const draftWorkspaceImagesLoadedFor = ref('')
const editorMode = ref<'text' | 'images'>('text')
const imageEditorTitle = ref('商品库图片编辑')
const navItems = workflowNavItems

const pricingDraftItems = computed(() => draftsIndex.value.filter((item) => item.draftId))
const pricingDraftTitle = computed(() => currentDraft.value.title || currentDraftProductContext.value.title || currentDraftProductContext.value.sourceTitle || currentDraft.value.draftId)
const draftWorkspaceTitle = computed(() => currentDraft.value.title || currentDraftProductContext.value.title || currentDraftProductContext.value.sourceTitle || '草稿编辑')
const draftSaveBlockedReason = computed(() => (
  currentDraft.value.draftId ? '' : '当前草稿暂无 ID。'
))
type PackageDimensionField = 'lengthCm' | 'widthCm' | 'heightCm' | 'weightKg'

function syncPricingPackageDimension(field: PackageDimensionField, value: string) {
  const normalizedValue = value.trim()
  const parsedValue = Number(normalizedValue)
  pricingInput.value[field] = normalizedValue && Number.isFinite(parsedValue) ? parsedValue : 0
}

const mercadolibreNotificationUrl = computed(() => {
  const ml = storeConfig.value.mercadolibre as UnknownRecord | undefined
  if (!ml || typeof ml !== 'object' || Array.isArray(ml)) return ''
  return String(ml.notification_url || ml.notifications_url || ml.webhook_url || '')
})

const pendingItems = computed(() => productsIndex.value.filter((item) => {
  const values = [
    item.collectStatus,
    item.workflowStatus,
    item.aiCopyStatus,
    item.imageStatus,
    item.categoryStatus,
    item.attributesStatus,
    item.pricingStatus,
    item.precheckStatus,
    item.publishStatus,
  ].map((value) => String(value || '').toLowerCase())
  return values.some((value) => ['failed', 'not_ready', 'pending', 'partial'].includes(value))
}))

const hasActivePublishJobs = computed(() => publishJobs.value.some((job) => (
  job.status === 'queued' || job.status === 'running'
)))

let publishJobsPollTimer: ReturnType<typeof setInterval> | undefined

function syncPublishJobsPolling() {
  if (publishJobsPollTimer) {
    clearInterval(publishJobsPollTimer)
    publishJobsPollTimer = undefined
  }
  if (activeNav.value !== 'publish' || !hasActivePublishJobs.value) return
  publishJobsPollTimer = setInterval(() => {
    void store.refreshPublishJobs({ quiet: true })
  }, 2500)
}

async function openProductEditor(item?: ProductIndexItem) {
  if (item) await store.loadProduct(item)
  editorMode.value = 'text'
  imageEditorTitle.value = '商品库图片编辑'
  editorOpen.value = true
}

async function openProductImageEditor(item?: ProductIndexItem) {
  await openProductEditor(item)
  editorMode.value = 'images'
}

function productIndexFromDraft(item: DraftIndexItem): ProductIndexItem {
  const platforms = [item.platform]
  return {
    productId: item.sourceProductId || item.productId,
    title: item.productTitle || item.title,
    mainImage: item.mainImage,
    sourcePlatform: item.sourcePlatform,
    sourceUrl: item.sourceUrl,
    createdAt: item.createdAt,
    updatedAt: item.updatedAt,
    platforms,
    draftStatuses: Object.fromEntries(platforms.map((platform) => [platform, item.status])) as ProductIndexItem['draftStatuses'],
    productFilePath: item.productFilePath,
    collectStatus: '',
    workflowStatus: '',
    aiCopyStatus: '',
    imageStatus: '',
    categoryStatus: '',
    attributesStatus: '',
    pricingStatus: '',
    precheckStatus: '',
    publishStatus: item.publishStatus,
    publishQueueReady: false,
    optimized: false,
    raw: item.raw,
  }
}

async function openDraftWorkspace(item: DraftIndexItem, tab: DraftWorkspaceTab = 'text') {
  store.setMarketplace(item.platform)
  await store.loadDraft(item)
  if (currentDraft.value.draftId !== item.draftId) return
  draftWorkspaceItem.value = item
  draftWorkspaceImagesLoadedFor.value = ''
  draftWorkspaceOpen.value = true
  await switchDraftWorkspaceTab(tab)
}

async function ensureDraftWorkspaceImages() {
  const item = draftWorkspaceItem.value
  if (!item || draftWorkspaceImagesLoadedFor.value === item.draftId) return
  await store.loadProduct(productIndexFromDraft(item))
  if (currentDraft.value.draftId === item.draftId) draftWorkspaceImagesLoadedFor.value = item.draftId
}

async function switchDraftWorkspaceTab(tab: DraftWorkspaceTab) {
  if (tab === 'images') await ensureDraftWorkspaceImages()
  draftWorkspaceTab.value = tab
  if (
    tab === 'category'
    && currentDraft.value.categoryId.trim()
    && !categoryAttributeLoading.value
    && !(
      category.value?.categoryId === currentDraft.value.categoryId.trim()
      && category.value.platform === selectedPublishTarget.value.platform
      && Boolean(category.value.fetchedAt)
    )
  ) {
    await store.loadCategoryAttributes()
  }
}

async function translateEditorImages(imageIds: string[]) {
  await store.translateImages(undefined, { sourceImageIds: imageIds })
}

async function editEditorImages(request: { prompt: string; imageIds: string[] }) {
  await store.editImagesWithPrompt(request.prompt, { sourceImageIds: request.imageIds })
}

async function translateDraftWorkspaceImages(imageIds: string[]) {
  await store.translateImages(currentDraft.value.language, {
    draftId: currentDraft.value.draftId,
    applyToDraft: true,
    draftImageStrategy: 'replace_selected',
    sourceImageIds: imageIds,
  })
}

async function editDraftWorkspaceImages(request: { prompt: string; imageIds: string[] }) {
  await store.editImagesWithPrompt(request.prompt, {
    draftId: currentDraft.value.draftId,
    applyToDraft: true,
    draftImageStrategy: 'append',
    sourceImageIds: request.imageIds,
  })
}

async function deleteDraft(item: DraftIndexItem) {
  const title = item.title || item.productTitle || item.draftId
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
  draftWorkspaceOpen.value = false
  resetDraftWorkspaceBackdropPointer()
  draftWorkspaceItem.value = null
  draftWorkspaceImagesLoadedFor.value = ''
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
  activeNav.value = key
  const nextQuery = key === 'dashboard' ? {} : { tab: key }
  if (route.path !== '/' || String(route.query.tab || '') !== String(nextQuery.tab || '')) {
    void router.push({ name: 'WorkflowHome', query: nextQuery })
  }
  void refreshDomainForNav(key)
}

async function claimSelectedAndOpenDrafts() {
  const ok = await store.claimSelectedProducts()
  if (ok) navigate('drafts')
}

function toggleTheme() {
  appStore.toggleTheme()
}

onMounted(async () => {
  await store.loadState()
  initialStateLoaded = true
  await refreshDomainForNav(activeNav.value)
})

onBeforeUnmount(() => {
  if (publishJobsPollTimer) clearInterval(publishJobsPollTimer)
})

watch([activeNav, hasActivePublishJobs], syncPublishJobsPolling)

watch(
  () => route.query,
  () => {
    const tab = String(route.query.tab || '')
    const previous = activeNav.value
    activeNav.value = navItems.some((item) => item.key === tab) ? tab : 'dashboard'
    if (initialStateLoaded && previous !== activeNav.value) {
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
        @toggle-theme="toggleTheme"
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
            :pending-items="pendingItems"
            :selected-ids="selectedProductIds"
            :progress-percent="progressPercent"
            :publish-logs="publishLogs"
            :orders="mercadoLibreOrders"
            :order-notifications="mercadoLibreOrderNotifications"
            :orders-total="mercadoLibreOrdersTotal"
            :orders-checked-at="mercadoLibreOrdersCheckedAt"
            :notification-url="mercadolibreNotificationUrl"
            :user-products="mercadoLibreUserProducts"
            :user-product-total="mercadoLibreUserProductTotal"
            :user-product-status="mercadoLibreUserProductStatus"
            :auth-checklist="mercadolibreAuthChecklist"
            :publish-job="publishJob"
            :logs="logs"
            :loading="loading"
            :error="error"
            @navigate="navigate"
            @refresh-products="store.refreshProductsIndex"
            @refresh-logs="store.refreshPublishLogs"
            @refresh-orders="store.refreshMercadoLibreOrders"
            @refresh-user-products="store.refreshMercadoLibreUserProducts"
            @open-product="openProductEditor"
            @edit-images="openProductImageEditor"
            @claim-selected="claimSelectedAndOpenDrafts"
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
            :browser-status="browserDebugStatus"
            @collect="store.collectProduct"
            @batch-collect="store.collectBatch"
            @collect-from-browser="store.collectFromBrowserTab"
            @open1688-browser="store.open1688Browser"
            @check-browser="store.checkBrowserDebugStatus"
            @open-profile="store.openDebugProfile"
            @clear-product="store.clearCollectedProduct"
            @save-settings="store.saveCollectSettings"
            @import-manual="store.importManual"
            @clean1688="store.previewClean1688Text"
          />

          <LibraryPanel
            v-else-if="activeNav === 'library'"
            :items="productsIndex"
            :selected-ids="selectedProductIds"
            :loading="loading"
            :error="error"
            @refresh="store.refreshProductsIndex"
            @edit="openProductEditor"
            @delete-item="store.deleteProduct"
            @delete-selected="store.deleteSelectedProducts"
            @toggle="store.toggleProductSelection"
            @select-all="store.selectAllProducts"
            @claim="claimSelectedAndOpenDrafts"
          />

          <div v-else-if="activeNav === 'drafts'" class="space-y-6">
            <PageHeader title="草稿箱" description="从商品库复制出的独立编辑稿，来源商品只作为关联和参考。" />
            <DraftBoxPanel
              :drafts="draftsIndex"
              :platform-options="platformOptions"
              :store-config="storeConfig"
              :loading="loading"
              :error="error"
              @refresh="store.refreshDraftsIndex"
              @update-language="store.updateDraftLanguage"
              @edit="openDraftWorkspace"
              @delete-draft="deleteDraft"
              @delete-drafts="deleteDrafts"
              @update-targets="store.updateDraftTargets"
            />
          </div>

          <div v-else-if="activeNav === 'publish'" class="space-y-6">
            <PageHeader title="发布队列" description="发布队列、任务状态和运行日志。" />
            <PublishJobsPanel
              :jobs="publishJobs"
              :selected-job-id="selectedPublishJobId"
              :selected-job-status="publishJobStatus"
              :loading="publishJobsLoading"
              :next-cursor="publishJobsNextCursor"
              :last-updated="publishJobsLastUpdated"
              :precheck-ok="Boolean(precheck?.ok)"
              :active-marketplace="activeMarketplace"
              :platform-options="platformOptions"
              :busy="loading"
              @refresh="() => store.refreshPublishJobs()"
              @select="store.selectPublishJob"
              @load-more="store.loadMorePublishJobs"
              @enqueue="store.enqueuePublish"
              @publish-direct="store.publishDirect"
              @reconcile="publishingStore.reconcileSelectedPublishJob"
            />
            <RunLog :logs="logs" />
            <pre v-if="publishResult" class="max-h-80 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">{{ JSON.stringify(publishResult, null, 2) }}</pre>
          </div>

          <div v-else-if="activeNav === 'mlUserProducts'" class="space-y-6">
            <PageHeader title="ML User Products" description="按 Siteless User Product 查看本地 publication 快照；刷新仅对账远端身份映射，不同步状态与价格。" />
            <MercadoLibrePublishedPanel
              :user-products="mercadoLibreUserProducts"
              :status="mercadoLibreUserProductStatus"
              :page="mercadoLibreUserProductPage"
              :per-page="mercadoLibreUserProductPerPage"
              :total="mercadoLibreUserProductTotal"
              :total-pages="mercadoLibreUserProductTotalPages"
              :refresh-errors="mercadoLibreUserProductRefreshErrors"
              :refresh-scope="mercadoLibreUserProductsRefreshScope"
              :checked-at="mercadoLibreUserProductsCheckedAt"
              :loading="loading"
              :error="error"
              @refresh="store.refreshMercadoLibreUserProducts"
              @pause-user-product="(userProduct) => store.pauseMercadoLibreUserProductById(userProduct.sitelessUserProductId)"
            />
          </div>

          <div v-else-if="activeNav === 'pending'" class="space-y-6">
            <PageHeader title="待处理" description="汇总采集、文案、图片、类目、预检或发布仍处于 pending / failed / not_ready / partial 的商品。" />
            <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <div><h2 class="card-title">待处理商品</h2><p class="muted mt-1">来自商品库状态字段，便于继续补齐流程。</p></div>
                <button class="btn btn-outline" :disabled="loading" @click="store.refreshProductsIndex">刷新</button>
              </div>
              <div class="mt-4 overflow-hidden rounded-lg border border-accent-200 dark:border-dark-700">
                <table class="w-full table-fixed text-left text-sm">
                  <colgroup>
                    <col class="w-[30%]" />
                    <col class="w-[7%]" />
                    <col class="w-[7%]" />
                    <col class="w-[7%]" />
                    <col class="w-[7%]" />
                    <col class="w-[7%]" />
                    <col class="w-[7%]" />
                    <col class="w-[7%]" />
                    <col class="w-[21%]" />
                  </colgroup>
                  <thead class="border-b border-accent-200 bg-accent-50 text-xs text-accent-500 dark:border-dark-700 dark:bg-dark-950/70 dark:text-accent-400"><tr><th class="p-3">商品</th><th class="px-1.5 py-3"><span class="block truncate" title="采集">采集</span></th><th class="px-1.5 py-3"><span class="block truncate" title="流程">流程</span></th><th class="px-1.5 py-3"><span class="block truncate" title="文案">文案</span></th><th class="px-1.5 py-3"><span class="block truncate" title="图片">图片</span></th><th class="px-1.5 py-3"><span class="block truncate" title="类目">类目</span></th><th class="px-1.5 py-3"><span class="block truncate" title="预检">预检</span></th><th class="px-1.5 py-3"><span class="block truncate" title="发布">发布</span></th><th class="p-3">操作</th></tr></thead>
                  <tbody class="divide-y divide-accent-100 dark:divide-dark-800">
                    <tr v-for="item in pendingItems" :key="item.productId" class="align-top transition hover:bg-accent-50/70 dark:hover:bg-dark-800/60">
                      <td class="min-w-0 p-3"><div class="truncate font-semibold text-accent-950 dark:text-white" :title="item.title || item.productId || '-'">{{ item.title || item.productId || '-' }}</div><div class="mt-1 truncate text-xs text-accent-500 dark:text-accent-400" :title="item.sourceUrl">{{ item.sourceUrl }}</div></td>
                      <td class="px-1.5 py-3"><span class="badge-muted max-w-full truncate" :title="item.collectStatus || '-'">{{ item.collectStatus || '-' }}</span></td>
                      <td class="px-1.5 py-3"><span class="badge-muted max-w-full truncate" :title="item.workflowStatus || '-'">{{ item.workflowStatus || '-' }}</span></td>
                      <td class="px-1.5 py-3"><span class="badge-muted max-w-full truncate" :title="item.aiCopyStatus || '-'">{{ item.aiCopyStatus || '-' }}</span></td>
                      <td class="px-1.5 py-3"><span class="badge-muted max-w-full truncate" :title="item.imageStatus || '-'">{{ item.imageStatus || '-' }}</span></td>
                      <td class="px-1.5 py-3"><span class="badge-muted max-w-full truncate" :title="item.categoryStatus || '-'">{{ item.categoryStatus || '-' }}</span></td>
                      <td class="px-1.5 py-3"><span class="badge-muted max-w-full truncate" :title="item.precheckStatus || '-'">{{ item.precheckStatus || '-' }}</span></td>
                      <td class="px-1.5 py-3"><span class="badge-muted max-w-full truncate" :title="item.publishStatus || '-'">{{ item.publishStatus || '-' }}</span></td>
                      <td class="p-3"><button class="btn btn-outline whitespace-nowrap px-3 py-1.5 text-xs" @click="openProductEditor(item)">继续处理</button></td>
                    </tr>
                    <tr v-if="!pendingItems.length"><td colspan="9" class="p-6 text-center text-accent-500 dark:text-accent-300">暂无待处理商品。</td></tr>
                  </tbody>
                </table>
              </div>
            </section>
          </div>

          <AuthSettingsPanel
            v-else-if="activeNav === 'auth'"
            :app-config="appConfig"
            :ai-config="aiConfig"
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

          <div v-else class="space-y-6">
            <PageHeader title="发布日志" description="展示发布请求、响应、错误码和下一步处理建议。" />
            <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
              <div class="flex flex-wrap items-center justify-between gap-3"><div><h2 class="card-title">发布日志</h2><p class="muted mt-1">来自 `/api/publish-logs`。</p></div><button class="btn btn-outline" :disabled="loading" @click="store.refreshPublishLogs">刷新日志</button></div>
              <div class="mt-4 overflow-hidden rounded-lg border border-accent-200 dark:border-dark-700">
                <table class="w-full table-fixed text-left text-sm">
                  <colgroup>
                    <col class="w-[16%]" />
                    <col class="w-[16%]" />
                    <col class="w-[10%]" />
                    <col class="w-[10%]" />
                    <col class="w-[10%]" />
                    <col class="w-[24%]" />
                    <col class="w-[14%]" />
                  </colgroup>
                  <thead class="border-b border-accent-200 bg-accent-50 text-xs text-accent-500 dark:border-dark-700 dark:bg-dark-950/70 dark:text-accent-400"><tr><th class="p-3">时间</th><th class="p-3">商品</th><th class="p-3">平台</th><th class="p-3">状态</th><th class="p-3">错误码</th><th class="p-3">错误</th><th class="p-3">Payload</th></tr></thead>
                  <tbody class="divide-y divide-accent-100 dark:divide-dark-800"><tr v-for="item in publishLogs" :key="`${item.jobId}-${item.startedAt}-${item.platform}`" class="align-top transition hover:bg-accent-50/70 dark:hover:bg-dark-800/60"><td class="p-3 text-accent-700 dark:text-accent-200"><span class="block truncate" :title="item.finishedAt || item.startedAt">{{ item.finishedAt || item.startedAt }}</span></td><td class="p-3 text-accent-700 dark:text-accent-200"><span class="block truncate" :title="item.productId || '-'">{{ item.productId || '-' }}</span></td><td class="p-3 text-accent-700 dark:text-accent-200"><span class="block truncate" :title="item.platform || '-'">{{ item.platform || '-' }}</span></td><td class="p-3"><span class="badge-muted max-w-full truncate" :title="item.status || '-'">{{ item.status || '-' }}</span></td><td class="p-3 font-mono text-accent-700 dark:text-accent-200"><span class="block truncate" :title="item.errorCode || '-'">{{ item.errorCode || '-' }}</span></td><td class="p-3 text-accent-700 dark:text-accent-200"><span class="block truncate" :title="item.errorMessage || '-'">{{ item.errorMessage || '-' }}</span></td><td class="p-3 text-accent-500 dark:text-accent-400"><span class="block truncate" :title="item.requestPayloadPath || '-'">{{ item.requestPayloadPath || '-' }}</span></td></tr><tr v-if="!publishLogs.length"><td colspan="7" class="p-6 text-center text-accent-500 dark:text-accent-300">暂无发布日志。</td></tr></tbody>
                </table>
              </div>
            </section>
          </div>
        </div>
      </main>
    </div>
    <div
      v-if="editorOpen"
      class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-950/50 p-4 backdrop-blur-sm"
      @pointerdown="recordProductEditorBackdropPointer"
      @pointerup="closeProductEditorFromBackdrop"
      @pointercancel="resetProductEditorBackdropPointer"
    >
      <div class="w-full max-w-7xl rounded-3xl bg-white p-4 shadow-2xl ring-1 ring-slate-200 dark:bg-dark-900 dark:ring-dark-700 sm:p-6">
        <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 class="text-xl font-black text-slate-950 dark:text-white">{{ product.name || '商品编辑' }}</h2>
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
          @save="store.saveCurrentProduct"
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
          @set-main="store.setMainImage"
          @delete="store.deleteImages"
          @clear="store.clearSourceImages"
        />
      </div>
    </div>
    <div
      v-if="draftWorkspaceOpen"
      class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-950/50 p-4 backdrop-blur-sm"
      @pointerdown="recordDraftWorkspaceBackdropPointer"
      @pointerup="closeDraftWorkspaceFromBackdrop"
      @pointercancel="resetDraftWorkspaceBackdropPointer"
    >
      <div class="w-full max-w-[96rem] rounded-3xl bg-white p-4 shadow-2xl ring-1 ring-slate-200 dark:bg-dark-900 dark:ring-dark-700 sm:p-6">
        <DraftWorkspacePanel
          :active-tab="draftWorkspaceTab"
          :draft-title="draftWorkspaceTitle"
          :draft-id="currentDraft.draftId"
          @update-active-tab="switchDraftWorkspaceTab"
          @close="closeDraftWorkspace"
        >
          <template #actions>
            <button v-if="draftWorkspaceTab === 'text'" class="btn btn-outline" :disabled="loading || !(currentDraft.productId || currentDraftProductContext.productId)" @click="() => store.generateCopy(true)">
              {{ copyGenerating ? '正在生成本地化文案…' : '生成/改写本地化文案' }}
            </button>
            <button class="btn btn-primary" :disabled="loading || !currentDraft.draftId" :title="draftSaveBlockedReason" @click="store.saveCurrentDraft">保存草稿</button>
          </template>

          <template #text>
            <DraftEditorPanel
              embedded
              :draft="currentDraft"
              :product-context="currentDraftProductContext"
              :platform-options="platformOptions"
              :store-config="storeConfig"
              :loading="loading"
              @update-language="store.updateDraftLanguage"
              @update-targets="store.updateDraftTargets"
            />
          </template>

          <template #images>
            <ProductImageEditorPanel
              title="草稿图片编辑"
              :product="product"
              :images="imagePool"
              :loading="loading"
              :error="error"
              show-translate-action
              :draft="currentDraft"
              @translate="translateDraftWorkspaceImages"
              @image-edit="editDraftWorkspaceImages"
              @upload="store.uploadReferenceImages"
              @save="store.saveCurrentImagePool"
              @save-draft-images="store.saveCurrentDraft"
              @set-main="store.setMainImage"
              @delete="store.deleteImages"
              @clear="store.clearSourceImages"
            />
          </template>

          <template #category>
            <div class="relative">
              <CategoryAttributesPanel
                :draft="currentDraft"
                :product-context="currentDraftProductContext"
                :publish-targets="currentPublishTargets"
                :selected-publish-target="selectedPublishTarget"
                :platform-options="platformOptions"
                :category="category"
                :category-query="categoryQuery"
                :category-results="categoryResults"
                :category-auto-match-product-name="categoryAutoMatchProductName"
                :category-auto-match-target-error="categoryAutoMatchTargetError"
                :category-attribute-translations="categoryAttributeTranslations"
                :category-attribute-translations-source="categoryAttributeTranslationsSource"
                :category-attribute-translating="categoryAttributeTranslating"
                :category-attribute-loading="categoryAttributeLoading"
                :category-attribute-error="categoryAttributeError"
                :category-result-translations="categoryResultTranslations"
                :category-result-translations-source="categoryResultTranslationsSource"
                :category-result-translating="categoryResultTranslating"
                :category-precheck="categoryPrecheck"
                :precheck="precheck"
                :loading="loading"
                @update-category-query="categoryQuery = $event"
                @select-publish-target="store.selectPublishTarget"
                @search-category="store.searchCategory"
                @suggest-category="store.suggestCategoryByAi"
                @select-category="store.selectCategory"
                @apply-category="store.loadCategoryAttributes"
                @translate-category-results="store.translateCategoryResults"
                @translate-category-attributes="store.translateCategoryAttributes"
                @fill-attributes="store.fillAttributesByAi"
                @update-package-dimension="syncPricingPackageDimension"
                @invalidate-category-precheck="store.invalidateCategoryPrecheck"
                @category-precheck="store.runCategoryOnlyPrecheck"
              />
              <div v-if="categoryAutoMatching" class="absolute inset-0 z-20 flex items-center justify-center rounded-3xl bg-white/90 p-6 text-center backdrop-blur-sm dark:bg-dark-950/90">
                <div class="max-w-md">
                  <div class="mx-auto size-10 animate-spin rounded-full border-4 border-brand-100 border-t-brand-600 dark:border-brand-950 dark:border-t-brand-400" />
                  <h3 class="mt-5 text-lg font-black text-slate-950 dark:text-white">正在自动识别并匹配类目</h3>
                  <p class="mt-2 text-sm text-slate-600 dark:text-slate-300">{{ categoryAutoMatchMessage || '正在准备商品信息…' }}</p>
                  <p v-if="categoryAutoMatchTotal" class="mt-3 text-xs font-semibold text-brand-700 dark:text-brand-300">已处理 {{ categoryAutoMatchCurrent }} / {{ categoryAutoMatchTotal }} 个目标站点</p>
                  <p class="mt-5 text-xs text-slate-500 dark:text-slate-400">完成后会自动关闭，请逐站点检查候选类目并手动确认。</p>
                </div>
              </div>
            </div>
          </template>

          <template #pricing>
            <div class="grid min-w-0 gap-6">
              <PricingPanel
                selection-locked
                :input="pricingInput"
                :result="pricingResult"
                :draft-items="pricingDraftItems"
                :draft-id="currentDraft.draftId"
                :draft-title="pricingDraftTitle"
                :product-context="currentDraftProductContext"
                :platform-options="platformOptions"
                :loading="loading"
                @calculate="store.calculatePrice"
                @apply="store.applyPrice"
              />
              <PricingChart :result="pricingResult" />
            </div>
          </template>

          <template #precheck>
            <PublishPrecheckPanel
              :draft="currentDraft"
              :product-context="currentDraftProductContext"
              :publish-targets="currentPublishTargets"
              :selected-publish-target="selectedPublishTarget"
              :platform-options="platformOptions"
              :precheck="precheck"
              :payload-preview="payloadPreview"
              :loading="loading"
              @select-publish-target="store.selectPublishTarget"
              @update-package-dimension="syncPricingPackageDimension"
              @invalidate-publish-validation="store.invalidatePublishValidation"
              @precheck="store.runPrecheck"
              @preview-payload="store.previewPayload"
              @publish="() => store.enqueuePublish()"
            />
          </template>
        </DraftWorkspacePanel>
      </div>
    </div>
  </div>
</template>
