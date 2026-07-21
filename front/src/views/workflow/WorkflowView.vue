<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AuthSettingsPanel from '@/components/auth/AuthSettingsPanel.vue'
import CategoryPrecheckPanel from '@/components/domain/CategoryPrecheckPanel.vue'
import CollectView from '@/views/workflow/CollectView.vue'
import DashboardView from '@/views/workflow/DashboardView.vue'
import DraftBoxPanel from '@/components/domain/DraftBoxPanel.vue'
import DraftEditorPanel from '@/components/domain/DraftEditorPanel.vue'
import LibraryPanel from '@/components/domain/LibraryPanel.vue'
import MercadoLibrePublishedPanel from '@/components/domain/MercadoLibrePublishedPanel.vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import PricingChart from '@/components/domain/PricingChart.vue'
import PricingPanel from '@/components/domain/PricingPanel.vue'
import ProductImageEditorPanel from '@/components/domain/ProductImageEditorPanel.vue'
import ProductEditorPanel from '@/components/domain/ProductEditorPanel.vue'
import ProductResearchPanel from '@/components/domain/ProductResearchPanel.vue'
import RunLog from '@/components/domain/RunLog.vue'
import { workflowNavItems } from '@/constants/navigation'
import { useClipboard } from '@/composables/useClipboard'
import { useAppStore } from '@/stores/app'
import { useWorkflowStore } from '@/stores/workflow'
import type { DraftIndexItem, Marketplace, ProductIndexItem, UnknownRecord } from '@/types/workflow'

const store = useWorkflowStore()
const {
  product,
  collectForm,
  collectDiagnostics,
  collectBatchRows,
  browserDebugStatus,
  productsIndex,
  draftsIndex,
  selectedProductIds,
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
  categoryAutoMatchTargetError,
  categoryAttributeTranslationEnabled,
  categoryAttributeTranslations,
  categoryAttributeTranslationsSource,
  categoryAttributeTranslating,
  categoryResultTranslations,
  categoryResultTranslationsSource,
  categoryResultTranslating,
  categoryPrecheck,
  precheck,
  payloadPreview,
  publishJob,
  publishJobStatus,
  publishLogs,
  mercadoLibreOrders,
  mercadoLibreOrderNotifications,
  mercadoLibreOrdersTotal,
  mercadoLibreOrdersCheckedAt,
  mercadoLibreRemoteItems,
  mercadoLibreRemoteStatus,
  mercadoLibreRemotePage,
  mercadoLibreRemotePerPage,
  mercadoLibreRemoteTotal,
  mercadoLibreRemoteTotalPages,
  publishResult,
  activeMarketplace,
  platformOptions,
  logs,
  appConfig,
  aiConfig,
  storeConfig,
  storeAuthSummary,
  mercadolibreAuthChecklist,
  lastAuthResult,
  authLink,
  currentDraft,
  currentDraftProductContext,
  currentPublishTargets,
  selectedPublishTarget,
  workflowSteps,
  progressPercent,
  imagePool,
  loading,
  error,
} = storeToRefs(store)

const appStore = useAppStore()
const route = useRoute()
const router = useRouter()
const { copied: productIdCopied, copy: copyToClipboard } = useClipboard()
const activeNav = ref('dashboard')
const editorOpen = ref(false)
const draftEditorOpen = ref(false)
const categoryEditorOpen = ref(false)
const editorMode = ref<'text' | 'images'>('text')
const editorContext = ref<'product' | 'draftImage'>('product')
const imageEditorTitle = ref('商品库图片编辑')
const imageEditorDraftId = ref('')
const imageEditorTargetLanguage = ref('')
const stateReady = ref(false)
const navItems = workflowNavItems
const pathNavMap: Record<string, string> = {
  '/': 'dashboard',
  '/research': 'research',
  '/collect': 'collect',
  '/library': 'library',
  '/drafts': 'drafts',
  '/edit': 'library',
  '/media': 'library',
  '/pricing': 'pricing',
  '/publish': 'category',
  '/ml-items': 'mlItems',
  '/settings': 'auth',
  '/auth': 'auth',
  '/logs': 'logs',
  '/pending': 'pending',
}
const navPathMap: Record<string, string> = {
  dashboard: '/',
  research: '/research',
  collect: '/collect',
  library: '/library',
  drafts: '/drafts',
  pricing: '/pricing',
  category: '/publish',
  publish: '/publish?tab=publish',
  mlItems: '/ml-items',
  pending: '/pending',
  auth: '/auth',
  logs: '/logs',
}

const pricingDraftItems = computed(() => draftsIndex.value.filter((item) => item.draftId))
const pricingDraftTitle = computed(() => currentDraft.value.title || currentDraftProductContext.value.title || currentDraftProductContext.value.sourceTitle || currentDraft.value.draftId)

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

function setMarketplace(value: Marketplace) {
  store.setMarketplace(value)
}

async function openProductEditor(item?: ProductIndexItem, mode: 'text' | 'images' = 'text') {
  if (item) await store.loadProduct(item)
  editorMode.value = mode
  editorContext.value = 'product'
  imageEditorTitle.value = '商品库图片编辑'
  imageEditorDraftId.value = ''
  imageEditorTargetLanguage.value = ''
  editorOpen.value = true
}

async function openProductImageEditor(item?: ProductIndexItem) {
  await openProductEditor(item, 'images')
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

async function openDraftEditor(item: DraftIndexItem) {
  store.setMarketplace(item.platform)
  await store.loadDraft(item)
  draftEditorOpen.value = true
}

async function openDraftImageEditor(item: DraftIndexItem) {
  draftEditorOpen.value = false
  store.setMarketplace(item.platform)
  await store.loadDraft(item)
  await store.loadProduct(productIndexFromDraft(item))
  editorMode.value = 'images'
  editorContext.value = 'draftImage'
  imageEditorTitle.value = '草稿图片编辑'
  imageEditorDraftId.value = item.draftId
  imageEditorTargetLanguage.value = item.language
  editorOpen.value = true
}

async function openDraftCategoryEditor(item: DraftIndexItem) {
  await store.loadDraft(item)
  categoryEditorOpen.value = true
  await nextTick()
  void store.autoSuggestCategoriesForDraft()
}

async function translateEditorImages() {
  if (editorContext.value === 'draftImage') {
    await store.translateImages(imageEditorTargetLanguage.value, {
      draftId: imageEditorDraftId.value,
      applyToDraft: true,
      draftImageStrategy: 'replace_selected',
    })
    return
  }
  await store.translateImages()
}

async function editEditorImages(prompt: string) {
  if (editorContext.value === 'draftImage') {
    await store.editImagesWithPrompt(prompt, {
      draftId: imageEditorDraftId.value,
      applyToDraft: true,
      draftImageStrategy: 'append',
    })
    return
  }
  await store.editImagesWithPrompt(prompt)
}

async function openDraftPrecheck(item: DraftIndexItem) {
  await store.loadDraft(item)
  navigate('category')
}

async function openDraftPricing(item: DraftIndexItem) {
  const ok = await store.loadDraftForPricing(item)
  if (!ok) return
  activeNav.value = 'pricing'
  await router.push({ path: '/pricing', query: { draftId: item.draftId } })
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

async function openProductPrecheck(item: ProductIndexItem, platform: Marketplace = activeMarketplace.value) {
  store.setMarketplace(platform)
  await store.loadProduct(item)
  navigate('category')
}

function closeProductEditor() {
  editorOpen.value = false
  imageEditorDraftId.value = ''
  imageEditorTargetLanguage.value = ''
}

function closeDraftEditor() {
  draftEditorOpen.value = false
}

function closeCategoryEditor() {
  categoryEditorOpen.value = false
}

async function copyProductId() {
  if (!product.value.productId) return
  await copyToClipboard(product.value.productId)
}

async function selectPricingDraft(draftId: string) {
  if (!draftId) return
  const ok = await store.loadDraftForPricing(draftId)
  if (ok) await router.replace({ path: '/pricing', query: { draftId } })
}

function openPricingDraftEditor() {
  if (!currentDraft.value.draftId) return
  draftEditorOpen.value = true
}

function navigate(key: string) {
  activeNav.value = key
  const nextPath = navPathMap[key] || '/'
  if (route.fullPath !== nextPath) void router.push(nextPath)
  if (key === 'library') void store.refreshProductsIndex()
  if (key === 'drafts') void store.refreshDraftsIndex()
  if (key === 'pricing' && !draftsIndex.value.length) void store.refreshDraftsIndex()
  if (key === 'logs') void store.refreshPublishLogs()
  if (key === 'mlItems') void store.refreshMercadoLibreRemoteItems()
  if (key === 'auth') void store.loadAiConfig()
}

async function applyPricingDraftFromRoute() {
  const draftId = String(route.query.draftId || '').trim()
  if (activeNav.value !== 'pricing' || !draftId || currentDraft.value.draftId === draftId) return
  await store.loadDraftForPricing(draftId)
}

async function claimSelectedAndOpenDrafts() {
  const ok = await store.claimSelectedProducts()
  if (ok) navigate('drafts')
}

async function claimCurrentAndOpenDrafts() {
  const ok = await store.claimCurrentProduct()
  if (ok) navigate('drafts')
}

function toggleTheme() {
  appStore.toggleTheme()
}

onMounted(async () => {
  await store.loadState()
  stateReady.value = true
  if (activeNav.value === 'auth') await store.loadAiConfig()
  await applyPricingDraftFromRoute()
})

watch(
  () => route.fullPath,
  () => {
    const tab = String(route.query.tab || '')
    const previous = activeNav.value
    activeNav.value = navItems.some((item) => item.key === tab) ? tab : pathNavMap[route.path] || 'dashboard'
    if (activeNav.value === 'mlItems' && previous !== activeNav.value) void store.refreshMercadoLibreRemoteItems()
    if (activeNav.value === 'auth' && previous !== activeNav.value) void store.loadAiConfig()
    if (activeNav.value === 'pricing' && stateReady.value) void applyPricingDraftFromRoute()
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
            :remote-items="mercadoLibreRemoteItems"
            :remote-total="mercadoLibreRemoteTotal"
            :remote-status="mercadoLibreRemoteStatus"
            :auth-checklist="mercadolibreAuthChecklist"
            :precheck="precheck"
            :publish-job="publishJob"
            :logs="logs"
            :loading="loading"
            :error="error"
            @navigate="navigate"
            @refresh-products="store.refreshProductsIndex"
            @refresh-logs="store.refreshPublishLogs"
            @refresh-orders="store.refreshMercadoLibreOrders"
            @refresh-remote="store.refreshMercadoLibreRemoteItems"
            @open-product="openProductEditor"
            @edit-images="openProductImageEditor"
            @open-precheck="openProductPrecheck"
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
            @load="openProductEditor"
            @edit-images="openProductImageEditor"
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
              :loading="loading"
              :error="error"
              @refresh="store.refreshDraftsIndex"
              @update-language="store.updateDraftLanguage"
              @edit-text="openDraftEditor"
              @edit-images="openDraftImageEditor"
              @edit-category="openDraftCategoryEditor"
              @go-pricing="openDraftPricing"
              @go-publish="openDraftPrecheck"
              @delete-draft="deleteDraft"
              @delete-drafts="deleteDrafts"
              @update-targets="store.updateDraftTargets"
            />
          </div>

          <div v-else-if="activeNav === 'pricing'" class="space-y-6">
            <PageHeader title="核价" description="成本、运费、佣金、汇率和利润计算。" />
            <section class="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
              <PricingPanel
                :input="pricingInput"
                :result="pricingResult"
                :draft-items="pricingDraftItems"
                :draft-id="currentDraft.draftId"
                :draft-title="pricingDraftTitle"
                :product-context="currentDraftProductContext"
                :draft-price="currentDraft.price"
                :platform-options="platformOptions"
                :loading="loading"
                @calculate="store.calculatePrice"
                @select-draft="selectPricingDraft"
                @refresh-drafts="store.refreshDraftsIndex"
                @edit-draft="openPricingDraftEditor"
              />
              <PricingChart :result="pricingResult" />
            </section>
          </div>

          <div v-else-if="activeNav === 'category'" class="space-y-6">
            <PageHeader title="发布预检" description="类目搜索、必填属性填充、payload 预览、发布前校验。" />
            <CategoryPrecheckPanel
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
              :category-attribute-translation-enabled="categoryAttributeTranslationEnabled"
              :category-attribute-translations="categoryAttributeTranslations"
              :category-attribute-translations-source="categoryAttributeTranslationsSource"
              :category-attribute-translating="categoryAttributeTranslating"
              :category-result-translations="categoryResultTranslations"
              :category-result-translations-source="categoryResultTranslationsSource"
              :category-result-translating="categoryResultTranslating"
              :category-precheck="categoryPrecheck"
              :precheck="precheck"
              :payload-preview="payloadPreview"
              :loading="loading"
              @update-category-query="categoryQuery = $event"
              @select-publish-target="store.selectPublishTarget"
              @search-category="store.searchCategory"
              @suggest-category="store.suggestCategoryByAi"
              @select-category="store.selectCategory"
              @apply-category="store.loadCategoryAttributes"
              @set-translate-attributes-enabled="store.setCategoryAttributeTranslationEnabled"
              @fill-attributes="store.fillAttributesByAi"
              @category-precheck="store.runCategoryOnlyPrecheck"
              @precheck="store.runPrecheck"
              @preview-payload="store.previewPayload"
              @publish="() => store.enqueuePublish()"
            />
          </div>

          <div v-else-if="activeNav === 'publish'" class="space-y-6">
            <PageHeader title="发布队列" description="发布队列、任务状态和运行日志。" />
            <section class="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
              <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
                <div class="flex flex-wrap items-center justify-between gap-3">
                  <div><h2 class="card-title">发布任务</h2><p class="muted mt-1">对应后端 `/api/publish-bus/enqueue` 和状态查询接口。</p></div>
                  <div class="flex flex-wrap gap-2"><button class="btn btn-outline" :disabled="!publishJob" @click="store.refreshPublishJob">刷新任务状态</button><button class="btn btn-primary" :disabled="loading || !precheck?.ok" @click="() => store.enqueuePublish()">发布入队</button><button class="btn btn-outline" :disabled="loading || activeMarketplace === 'mercadolibre' || !precheck?.ok" @click="store.publishDirect">非 ML 直接发布</button><button class="btn btn-primary" :disabled="loading || activeMarketplace !== 'mercadolibre' || !precheck?.ok" @click="store.confirmRealPublish">确认 ML 真实发布</button></div>
                </div>
                <div class="mt-5 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
                  <template v-if="publishJob">
                    <p class="text-sm text-accent-500 dark:text-accent-400">Job ID</p><p class="mt-1 break-all font-semibold text-accent-950 dark:text-white">{{ publishJob.jobId }}</p>
                    <div class="mt-4 flex flex-wrap gap-2"><span class="badge-success">{{ publishJob.status }}</span><span v-for="platform in publishJob.platforms" :key="platform" class="badge-info">{{ platform }}</span></div>
                    <pre v-if="publishJobStatus" class="mt-4 max-h-80 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">{{ JSON.stringify(publishJobStatus, null, 2) }}</pre>
                  </template>
                  <template v-else><p class="text-sm text-accent-600 dark:text-accent-300">通过预检后可加入发布队列，生成任务状态。</p></template>
                  <pre v-if="publishResult" class="mt-4 max-h-80 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">{{ JSON.stringify(publishResult, null, 2) }}</pre>
                </div>
              </section>
              <RunLog :logs="logs" />
            </section>
          </div>

          <div v-else-if="activeNav === 'mlItems'" class="space-y-6">
            <PageHeader title="ML 已发布商品" description="实时查看 Mercado Libre 账号商品，并支持通过 API 下架或结束发布。" />
            <MercadoLibrePublishedPanel
              :items="mercadoLibreRemoteItems"
              :status="mercadoLibreRemoteStatus"
              :page="mercadoLibreRemotePage"
              :per-page="mercadoLibreRemotePerPage"
              :total="mercadoLibreRemoteTotal"
              :total-pages="mercadoLibreRemoteTotalPages"
              :loading="loading"
              :error="error"
              @refresh="store.refreshMercadoLibreRemoteItems"
              @close-item="(item) => store.closeMercadoLibreRemoteItem(item.id)"
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
    <div v-if="editorOpen" class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-950/50 p-4 backdrop-blur-sm" @click.self="closeProductEditor">
      <div class="w-full rounded-3xl bg-white p-4 shadow-2xl ring-1 ring-slate-200 dark:bg-dark-900 dark:ring-dark-700 sm:p-6" :class="editorMode === 'images' ? 'max-w-7xl' : 'max-w-6xl'">
        <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 class="text-xl font-black text-slate-950 dark:text-white">{{ editorMode === 'images' ? imageEditorTitle : '商品文本编辑' }}</h2>
          </div>
          <div class="flex flex-wrap gap-2">
            <button class="btn btn-outline" :disabled="!product.productId" :title="product.productId || '当前商品暂无 ID'" @click="copyProductId">
              {{ productIdCopied ? '已复制' : '复制id' }}
            </button>
            <button v-if="editorContext === 'product'" class="btn btn-outline" :class="editorMode === 'text' ? 'bg-slate-100' : ''" @click="editorMode = 'text'">编辑文本</button>
            <button class="btn btn-outline" :class="editorMode === 'images' ? 'bg-slate-100' : ''" @click="editorMode = 'images'">编辑图片</button>
            <button class="btn btn-outline" @click="closeProductEditor">关闭</button>
          </div>
        </div>
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
          :show-translate-action="editorContext === 'draftImage'"
          :draft="editorContext === 'draftImage' ? currentDraft : undefined"
          @translate="translateEditorImages"
          @image-edit="editEditorImages"
          @upload="store.uploadReferenceImages"
          @save="store.saveCurrentImagePool"
          @save-draft-images="store.saveCurrentDraft"
          @set-main="store.setMainImage"
          @delete="store.deleteImages"
          @clear="store.clearSourceImages"
        />
      </div>
    </div>
    <div v-if="draftEditorOpen" class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-950/50 p-4 backdrop-blur-sm" @click.self="closeDraftEditor">
      <div class="w-full max-w-7xl rounded-3xl bg-white p-4 shadow-2xl ring-1 ring-slate-200 dark:bg-dark-900 dark:ring-dark-700 sm:p-6">
        <DraftEditorPanel
          :draft="currentDraft"
          :product-context="currentDraftProductContext"
          :platform-options="platformOptions"
          :loading="loading"
          @generate-copy="() => store.generateCopy(true)"
          @save="store.saveCurrentDraft"
          @close="closeDraftEditor"
        />
      </div>
    </div>
    <div v-if="categoryEditorOpen" class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-950/50 p-4 backdrop-blur-sm" @click.self="closeCategoryEditor">
      <div class="relative w-full max-w-7xl rounded-3xl bg-white p-4 shadow-2xl ring-1 ring-slate-200 dark:bg-dark-900 dark:ring-dark-700 sm:p-6">
        <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 class="text-xl font-black text-slate-950 dark:text-white">类目/属性</h2>
            <p class="mt-1 text-sm text-slate-500 dark:text-slate-300">编辑当前草稿各目标站点的类目和平台属性。</p>
          </div>
          <div class="flex flex-wrap gap-2">
            <button class="btn btn-primary" :disabled="loading || !currentDraft.draftId" @click="store.saveCurrentDraft">保存草稿</button>
            <button class="btn btn-outline" @click="closeCategoryEditor">关闭</button>
          </div>
        </div>
        <CategoryPrecheckPanel
          mode="category"
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
          :category-attribute-translation-enabled="categoryAttributeTranslationEnabled"
          :category-attribute-translations="categoryAttributeTranslations"
          :category-attribute-translations-source="categoryAttributeTranslationsSource"
          :category-attribute-translating="categoryAttributeTranslating"
          :category-result-translations="categoryResultTranslations"
          :category-result-translations-source="categoryResultTranslationsSource"
          :category-result-translating="categoryResultTranslating"
          :category-precheck="categoryPrecheck"
          :precheck="precheck"
          :payload-preview="payloadPreview"
          :loading="loading"
          @update-category-query="categoryQuery = $event"
          @select-publish-target="store.selectPublishTarget"
          @search-category="store.searchCategory"
          @suggest-category="store.suggestCategoryByAi"
          @select-category="store.selectCategory"
          @apply-category="store.loadCategoryAttributes"
          @set-translate-attributes-enabled="store.setCategoryAttributeTranslationEnabled"
          @fill-attributes="store.fillAttributesByAi"
          @category-precheck="store.runCategoryOnlyPrecheck"
          @precheck="store.runPrecheck"
          @preview-payload="store.previewPayload"
          @publish="() => store.enqueuePublish()"
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
    </div>
  </div>
</template>
