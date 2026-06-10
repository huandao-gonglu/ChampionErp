<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AuthSettingsPanel from '@/components/auth/AuthSettingsPanel.vue'
import CategoryPrecheckPanel from '@/components/domain/CategoryPrecheckPanel.vue'
import CollectView from '@/views/workflow/CollectView.vue'
import LibraryPanel from '@/components/domain/LibraryPanel.vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import PricingChart from '@/components/domain/PricingChart.vue'
import PricingPanel from '@/components/domain/PricingPanel.vue'
import ProductImageEditorPanel from '@/components/domain/ProductImageEditorPanel.vue'
import ProductEditorPanel from '@/components/domain/ProductEditorPanel.vue'
import RunLog from '@/components/domain/RunLog.vue'
import StepTimeline from '@/components/domain/StepTimeline.vue'
import { workflowNavItems } from '@/constants/navigation'
import { useAppStore } from '@/stores/app'
import { useWorkflowStore } from '@/stores/workflow'
import type { Marketplace, ProductIndexItem } from '@/types/workflow'

const store = useWorkflowStore()
const {
  product,
  collectForm,
  collectDiagnostics,
  collectBatchRows,
  browserDebugStatus,
  productsIndex,
  selectedProductIds,
  pricingInput,
  pricingResult,
  category,
  categoryQuery,
  categoryResults,
  categoryCacheStatus,
  categoryPrecheck,
  precheck,
  payloadPreview,
  publishJob,
  publishJobStatus,
  publishLogs,
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
  '/collect': 'collect',
  '/library': 'library',
  '/edit': 'library',
  '/media': 'library',
  '/pricing': 'pricing',
  '/publish': 'category',
  '/settings': 'auth',
  '/auth': 'auth',
  '/logs': 'logs',
  '/pending': 'pending',
}
const navPathMap: Record<string, string> = {
  dashboard: '/',
  collect: '/collect',
  library: '/library',
  pricing: '/pricing',
  category: '/publish',
  publish: '/publish?tab=publish',
  pending: '/pending',
  auth: '/auth',
  logs: '/logs',
}

const summaryCards = computed(() => [
  { label: '商品库', value: productsIndex.value.length, hint: 'SQLite 商品记录' },
  { label: '图片池', value: imagePool.value.length, hint: '当前商品图片' },
  { label: '发布日志', value: publishLogs.value.length, hint: '历史发布记录' },
  { label: '完成度', value: `${progressPercent.value}%`, hint: '核心链路进度' },
])

const pricingProductItems = computed(() => productsIndex.value.filter((item) => item.productId))

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
  if (key === 'pricing' && !productsIndex.value.length) void store.refreshProductsIndex()
  if (key === 'logs') void store.refreshPublishLogs()
  if (key === 'auth') void store.loadAiConfig()
}

function toggleTheme() {
  appStore.toggleTheme()
}

onMounted(() => {
  void store.loadState()
})

watch(
  () => route.fullPath,
  () => {
    const tab = String(route.query.tab || '')
    activeNav.value = navItems.some((item) => item.key === tab) ? tab : pathNavMap[route.path] || 'dashboard'
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
        <div class="sticky top-0 z-20 border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur lg:hidden">
          <select v-model="activeNav" class="input">
            <option v-for="item in navItems" :key="item.key" :value="item.key">{{ item.title }}</option>
          </select>
        </div>

        <div class="px-4 py-6 sm:px-6 lg:px-8">
          <div v-if="activeNav === 'dashboard'" class="space-y-6">
            <PageHeader title="跨境 ERP Web 工作台" description="一站式管理采集、商品库、商品编辑、图片文案、核价、发布预检、平台授权和发布日志。" />
            <section class="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <article v-for="card in summaryCards" :key="card.label" class="card">
                <p class="text-sm font-semibold text-slate-500">{{ card.label }}</p>
                <p class="mt-2 text-3xl font-black text-slate-950">{{ card.value }}</p>
                <p class="mt-1 text-xs text-slate-500">{{ card.hint }}</p>
              </article>
            </section>
            <StepTimeline :steps="workflowSteps" :progress="progressPercent" />
            <section class="grid gap-6 xl:grid-cols-[1fr_0.9fr]">
              <LibraryPanel
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
                @claim="store.claimSelectedProducts"
                @generate-copy="store.generateCopyForSelectedProducts"
                @generate-image-prompt="store.generateImagePromptPack"
                @publish-selected="store.enqueueSelectedProducts"
                @go-publish="navigate('category')"
              />
              <RunLog :logs="logs" />
            </section>
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
            @choose-images="store.uploadReferenceImages"
            @clear-images="store.clearSourceImages"
            @generate-copy="store.generateCopy"
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
            @claim="store.claimSelectedProducts"
            @generate-copy="store.generateCopyForSelectedProducts"
            @generate-image-prompt="store.generateImagePromptPack"
            @publish-selected="store.enqueueSelectedProducts"
            @go-publish="navigate('category')"
          />

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
              :category="category"
              :category-query="categoryQuery"
              :category-results="categoryResults"
              :category-precheck="categoryPrecheck"
              :precheck="precheck"
              :payload-preview="payloadPreview"
              :products-index="productsIndex"
              :category-cache-status="categoryCacheStatus"
              :loading="loading"
              @update-category-query="categoryQuery = $event"
              @set-marketplace="setMarketplace"
              @search-category="store.searchCategory"
              @suggest-category="store.suggestCategoryByAi"
              @select-category="store.selectCategory"
              @apply-category="store.loadCategoryAttributes"
              @fill-attributes="store.fillAttributesByAi"
              @category-precheck="store.runCategoryOnlyPrecheck"
              @refresh-categories="store.refreshCategories"
              @precheck="store.runPrecheck"
              @preview-payload="store.previewPayload"
              @publish="() => store.enqueuePublish()"
              @claim-current="store.claimCurrentProduct"
              @load-product="store.loadProduct"
              @refresh-products="store.refreshProductsIndex"
            />
          </div>

          <div v-else-if="activeNav === 'publish'" class="space-y-6">
            <PageHeader title="发布队列" description="发布队列、任务状态和运行日志。" />
            <section class="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
              <section class="card">
                <div class="flex flex-wrap items-center justify-between gap-3">
                  <div><h2 class="card-title">发布任务</h2><p class="muted mt-1">对应后端 `/api/publish-bus/enqueue` 和状态查询接口。</p></div>
                  <div class="flex flex-wrap gap-2"><button class="btn btn-outline" :disabled="!publishJob" @click="store.refreshPublishJob">刷新任务状态</button><button class="btn btn-primary" :disabled="loading || !precheck?.ok" @click="() => store.enqueuePublish()">发布入队</button><button class="btn btn-outline" :disabled="loading || activeMarketplace === 'mercadolibre' || !precheck?.ok" @click="store.publishDirect">非 ML 直接发布</button><button class="btn btn-primary" :disabled="loading || activeMarketplace !== 'mercadolibre' || !precheck?.ok" @click="store.confirmRealPublish">确认 ML 真实发布</button></div>
                </div>
                <div class="mt-5 rounded-2xl border border-slate-200 bg-slate-50 p-4">
                  <template v-if="publishJob">
                    <p class="text-sm text-slate-500">Job ID</p><p class="mt-1 break-all font-semibold text-slate-950">{{ publishJob.jobId }}</p>
                    <div class="mt-4 flex flex-wrap gap-2"><span class="badge-success">{{ publishJob.status }}</span><span v-for="platform in publishJob.platforms" :key="platform" class="badge-info">{{ platform }}</span></div>
                    <pre v-if="publishJobStatus" class="mt-4 max-h-80 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">{{ JSON.stringify(publishJobStatus, null, 2) }}</pre>
                  </template>
                  <template v-else><p class="text-sm text-slate-600">通过预检后可加入发布队列，生成任务状态。</p></template>
                  <pre v-if="publishResult" class="mt-4 max-h-80 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">{{ JSON.stringify(publishResult, null, 2) }}</pre>
                </div>
              </section>
              <RunLog :logs="logs" />
            </section>
          </div>

          <div v-else-if="activeNav === 'pending'" class="space-y-6">
            <PageHeader title="待处理" description="汇总采集、文案、图片、类目、预检或发布仍处于 pending / failed / not_ready / partial 的商品。" />
            <section class="card">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <div><h2 class="card-title">待处理商品</h2><p class="muted mt-1">来自商品库状态字段，便于继续补齐流程。</p></div>
                <button class="btn btn-outline" :disabled="loading" @click="store.refreshProductsIndex">刷新</button>
              </div>
              <div class="mt-4 overflow-auto rounded-2xl border border-slate-200">
                <table class="w-full text-left text-sm">
                  <thead class="bg-slate-50 text-xs text-slate-500"><tr><th class="p-3">商品</th><th class="p-3">采集</th><th class="p-3">流程</th><th class="p-3">文案</th><th class="p-3">图片</th><th class="p-3">类目</th><th class="p-3">预检</th><th class="p-3">发布</th><th class="p-3">操作</th></tr></thead>
                  <tbody>
                    <tr v-for="item in pendingItems" :key="item.productId" class="border-t">
                      <td class="max-w-sm p-3"><div class="font-semibold text-slate-950">{{ item.title || item.productId || '-' }}</div><div class="mt-1 truncate text-xs text-slate-500">{{ item.sourceUrl }}</div></td>
                      <td class="p-3"><span class="badge-muted">{{ item.collectStatus || '-' }}</span></td>
                      <td class="p-3"><span class="badge-muted">{{ item.workflowStatus || '-' }}</span></td>
                      <td class="p-3"><span class="badge-muted">{{ item.aiCopyStatus || '-' }}</span></td>
                      <td class="p-3"><span class="badge-muted">{{ item.imageStatus || '-' }}</span></td>
                      <td class="p-3"><span class="badge-muted">{{ item.categoryStatus || '-' }}</span></td>
                      <td class="p-3"><span class="badge-muted">{{ item.precheckStatus || '-' }}</span></td>
                      <td class="p-3"><span class="badge-muted">{{ item.publishStatus || '-' }}</span></td>
                      <td class="p-3"><button class="btn btn-outline py-1.5" @click="openProductEditor(item)">继续处理</button></td>
                    </tr>
                    <tr v-if="!pendingItems.length"><td colspan="9" class="p-6 text-center text-slate-500">暂无待处理商品。</td></tr>
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
            <section class="card">
              <div class="flex flex-wrap items-center justify-between gap-3"><div><h2 class="card-title">发布日志</h2><p class="muted mt-1">来自 `/api/publish-logs`。</p></div><button class="btn btn-outline" :disabled="loading" @click="store.refreshPublishLogs">刷新日志</button></div>
              <div class="mt-4 overflow-auto rounded-2xl border border-slate-200">
                <table class="w-full text-left text-sm">
                  <thead class="bg-slate-50 text-xs text-slate-500"><tr><th class="p-3">时间</th><th class="p-3">商品</th><th class="p-3">平台</th><th class="p-3">状态</th><th class="p-3">错误码</th><th class="p-3">错误</th><th class="p-3">Payload</th></tr></thead>
                  <tbody><tr v-for="item in publishLogs" :key="`${item.jobId}-${item.startedAt}-${item.platform}`" class="border-t"><td class="p-3">{{ item.finishedAt || item.startedAt }}</td><td class="p-3">{{ item.productId || '-' }}</td><td class="p-3">{{ item.platform || '-' }}</td><td class="p-3"><span class="badge-muted">{{ item.status || '-' }}</span></td><td class="p-3 font-mono">{{ item.errorCode || '-' }}</td><td class="max-w-sm p-3">{{ item.errorMessage || '-' }}</td><td class="max-w-xs truncate p-3">{{ item.requestPayloadPath || '-' }}</td></tr><tr v-if="!publishLogs.length"><td colspan="7" class="p-6 text-center text-slate-500">暂无发布日志。</td></tr></tbody>
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
