<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AuthSettingsPanel from '@/components/auth/AuthSettingsPanel.vue'
import CategoryPrecheckPanel from '@/components/domain/CategoryPrecheckPanel.vue'
import CollectView from '@/views/workflow/CollectView.vue'
import DashboardView from '@/views/workflow/DashboardView.vue'
import DraftBoxPanel from '@/components/domain/DraftBoxPanel.vue'
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
  categoryAttributeTranslationEnabled,
  categoryAttributeTranslations,
  categoryAttributeTranslationsSource,
  categoryAttributeTranslating,
  categoryResultTranslations,
  categoryResultTranslationsSource,
  categoryResultTranslating,
  categoryCacheStatus,
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
  logs,
  appConfig,
  aiConfig,
  storeConfig,
  storeAuthSummary,
  mercadolibreAuthChecklist,
  lastAuthResult,
  authLink,
  imagePrompt,
  workflowSteps,
  progressPercent,
  imagePool,
  loading,
  error,
} = storeToRefs(store)

const appStore = useAppStore()
const route = useRoute()
const router = useRouter()
const activeNav = ref('dashboard')
const editorOpen = ref(false)
const editorMode = ref<'text' | 'images'>('text')
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

const pricingProductItems = computed(() => productsIndex.value.filter((item) => item.productId))

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
  editorOpen.value = true
}

async function openProductImageEditor(item?: ProductIndexItem) {
  await openProductEditor(item, 'images')
}

async function openDraftEditor(item: DraftIndexItem, mode: 'text' | 'images' = 'text') {
  store.setMarketplace(item.platform)
  await store.loadDraft(item)
  editorMode.value = mode
  editorOpen.value = true
}

async function openDraftPrecheck(item: DraftIndexItem) {
  store.setMarketplace(item.platform)
  await store.loadDraft(item)
  navigate('category')
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
}

async function selectPricingProduct(productId: string) {
  const item = pricingProductItems.value.find((entry) => entry.productId === productId)
  if (item) await store.loadProduct(item)
}

function navigate(key: string) {
  activeNav.value = key
  const nextPath = navPathMap[key] || '/'
  if (route.fullPath !== nextPath) void router.push(nextPath)
  if (key === 'library') void store.refreshProductsIndex()
  if (key === 'drafts') void store.refreshDraftsIndex()
  if (key === 'pricing' && !productsIndex.value.length) void store.refreshProductsIndex()
  if (key === 'logs') void store.refreshPublishLogs()
  if (key === 'mlItems') void store.refreshMercadoLibreRemoteItems()
  if (key === 'auth') void store.loadAiConfig()
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
  if (activeNav.value === 'auth') await store.loadAiConfig()
})

watch(
  () => route.fullPath,
  () => {
    const tab = String(route.query.tab || '')
    const previous = activeNav.value
    activeNav.value = navItems.some((item) => item.key === tab) ? tab : pathNavMap[route.path] || 'dashboard'
    if (activeNav.value === 'mlItems' && previous !== activeNav.value) void store.refreshMercadoLibreRemoteItems()
    if (activeNav.value === 'auth' && previous !== activeNav.value) void store.loadAiConfig()
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
            @generate-copy="store.generateCopy"
            @import-manual="store.importManual"
            @clean1688="store.previewClean1688Text"
          />

          <LibraryPanel
            v-else-if="activeNav === 'library'"
            :items="productsIndex"
            :selected-ids="selectedProductIds"
            :claim-platforms="collectForm.selectedClaimPlatforms"
            :loading="loading"
            :error="error"
            @refresh="store.refreshProductsIndex"
            @load="openProductEditor"
            @edit-images="openProductImageEditor"
            @delete-item="store.deleteProduct"
            @delete-selected="store.deleteSelectedProducts"
            @toggle="store.toggleProductSelection"
            @select-all="store.selectAllProducts"
            @set-claim-platforms="store.setClaimPlatforms"
            @claim="claimSelectedAndOpenDrafts"
            @generate-copy="store.generateCopyForSelectedProducts"
            @generate-image-prompt="store.generateImagePromptPack"
            @publish-selected="store.enqueueSelectedProducts"
            @go-publish="navigate('category')"
          />

          <div v-else-if="activeNav === 'drafts'" class="space-y-6">
            <PageHeader title="草稿箱" description="来自商品库的平台编辑稿，聚焦未发布完成的文案、图片、类目和发布预检。" />
            <DraftBoxPanel
              :drafts="draftsIndex"
              :loading="loading"
              :error="error"
              @refresh="store.refreshDraftsIndex"
              @edit-text="(item) => openDraftEditor(item, 'text')"
              @edit-images="(item) => openDraftEditor(item, 'images')"
              @go-publish="openDraftPrecheck"
              @delete-draft="deleteDraft"
              @delete-drafts="deleteDrafts"
            />
          </div>

          <div v-else-if="activeNav === 'pricing'" class="space-y-6">
            <PageHeader title="核价" description="成本、运费、佣金、汇率和利润计算。" />
            <section class="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
              <PricingPanel
                :input="pricingInput"
                :result="pricingResult"
                :product-items="pricingProductItems"
                :product-id="product.productId"
                :product-title="product.source.title || product.name || product.productId"
                :source-platform="product.source.sourcePlatform"
                :draft-price="product.drafts.mercadolibre.price"
                :loading="loading"
                @calculate="store.calculatePrice"
                @select-product="selectPricingProduct"
                @refresh-products="store.refreshProductsIndex"
                @edit-product="openProductEditor()"
              />
              <PricingChart :result="pricingResult" />
            </section>
          </div>

          <div v-else-if="activeNav === 'category'" class="space-y-6">
            <PageHeader title="发布预检" description="类目搜索、必填属性填充、payload 预览、发布前校验。" />
            <CategoryPrecheckPanel
              :product="product"
              :active-marketplace="activeMarketplace"
              :claim-platforms="collectForm.selectedClaimPlatforms"
              :category="category"
              :category-query="categoryQuery"
              :category-results="categoryResults"
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
              :products-index="productsIndex"
              :category-cache-status="categoryCacheStatus"
              :loading="loading"
              @update-category-query="categoryQuery = $event"
              @set-marketplace="setMarketplace"
              @set-claim-platforms="store.setClaimPlatforms"
              @search-category="store.searchCategory"
              @suggest-category="store.suggestCategoryByAi"
              @select-category="store.selectCategory"
              @apply-category="store.loadCategoryAttributes"
              @set-translate-attributes-enabled="store.setCategoryAttributeTranslationEnabled"
              @fill-attributes="store.fillAttributesByAi"
              @category-precheck="store.runCategoryOnlyPrecheck"
              @refresh-categories="store.refreshCategories"
              @precheck="store.runPrecheck"
              @preview-payload="store.previewPayload"
              @publish="() => store.enqueuePublish()"
              @claim-current="claimCurrentAndOpenDrafts"
              @load-product="store.loadProduct"
              @refresh-products="store.refreshProductsIndex"
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
            <h2 class="text-xl font-black text-slate-950 dark:text-white">{{ editorMode === 'images' ? '商品图片编辑' : '商品文本编辑' }}</h2>
          </div>
          <div class="flex flex-wrap gap-2">
            <button class="btn btn-outline" :class="editorMode === 'text' ? 'bg-slate-100' : ''" @click="editorMode = 'text'">编辑文本</button>
            <button class="btn btn-outline" :class="editorMode === 'images' ? 'bg-slate-100' : ''" @click="editorMode = 'images'">编辑图片</button>
            <button class="btn btn-outline" @click="closeProductEditor">关闭</button>
          </div>
        </div>
        <ProductEditorPanel
          v-if="editorMode === 'text'"
          :product="product"
          :active-marketplace="activeMarketplace"
          :loading="loading"
          @save="store.saveCurrentProduct"
          @generate-copy="store.generateCopy"
          @assign-upc="store.assignUpc"
          @set-marketplace="setMarketplace"
          @go-pricing="navigate('pricing'); closeProductEditor()"
          @go-images="editorMode = 'images'"
          @go-publish="navigate('category'); closeProductEditor()"
        />
        <ProductImageEditorPanel
          v-else
          :product="product"
          :active-marketplace="activeMarketplace"
          :image-prompt="imagePrompt"
          :images="imagePool"
          :loading="loading"
          :error="error"
          @set-marketplace="setMarketplace"
          @generate-prompt="store.generateImagePromptPack"
          @translate="store.translateImages"
          @upload="store.uploadReferenceImages"
          @sync-generated="store.syncGeneratedImagePool"
          @save="store.saveCurrentImagePool"
          @set-main="store.setMainImage"
          @delete="store.deleteImages"
          @clear="store.clearSourceImages"
        />
      </div>
    </div>
  </div>
</template>
