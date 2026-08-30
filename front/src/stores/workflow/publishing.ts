import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  pauseMercadoLibreUserProduct,
  fetchMercadoLibreOrders,
  fetchMercadoLibreUserProducts,
  fetchPublishJob,
  fetchPublishJobs,
  fetchPublishLogs,
  reconcilePublishJob,
} from '@/api/workflow/publishing'
import { createDefaultPricingInput } from '@/constants/initialState'
import { useWorkflowActivityStore } from '@/stores/workflow/activity'
import type {
  CategoryAttributeTranslations,
  CategoryPrecheckResult,
  CategoryResultTranslations,
  CategorySearchResult,
  CategorySelection,
  Marketplace,
  MarketplaceOption,
  MercadoLibreOrderItem,
  MercadoLibreOrderNotification,
  MercadoLibreUserProduct,
  PayloadPreviewState,
  PricingInput,
  PricingResult,
  PublishJob,
  PublishJobListItem,
  PublishLogItem,
  PublishPrecheck,
  UnknownRecord,
} from '@/types/workflow'

export const useWorkflowPublishingStore = defineStore('workflow-publishing', () => {
  const pricingInput = ref<PricingInput>(createDefaultPricingInput())
  const pricingResult = ref<PricingResult | null>(null)
  const category = ref<CategorySelection | null>(null)
  const categoryQuery = ref('')
  const categoryResults = ref<CategorySearchResult[]>([])
  const categoryRecommendations = ref<Record<string, { query: string; results: CategorySearchResult[]; error: string }>>({})
  const categoryAutoMatching = ref(false)
  const categoryAutoMatchMessage = ref('')
  const categoryAutoMatchCurrent = ref(0)
  const categoryAutoMatchTotal = ref(0)
  const categoryAutoMatchProductName = ref('')
  const categoryAttributeTranslations = ref<CategoryAttributeTranslations>({})
  const categoryAttributeTranslationsSource = ref('')
  const categoryAttributeTranslating = ref(false)
  const categoryAttributeLoading = ref(false)
  const categoryAttributeError = ref('')
  const categoryResultTranslations = ref<CategoryResultTranslations>({})
  const categoryResultTranslationsSource = ref('')
  const categoryResultTranslating = ref(false)
  const categoryPrecheck = ref<CategoryPrecheckResult | null>(null)
  const precheck = ref<PublishPrecheck | null>(null)
  const precheckResults = ref<UnknownRecord>({})
  const payloadPreview = ref<PayloadPreviewState | null>(null)
  const copyGenerating = ref(false)
  const publishJob = ref<PublishJob | null>(null)
  const publishJobStatus = ref<UnknownRecord | null>(null)
  const publishJobs = ref<PublishJobListItem[]>([])
  const selectedPublishJobId = ref('')
  const publishJobsNextCursor = ref('')
  const publishJobsLoading = ref(false)
  const publishJobsLastUpdated = ref('')
  const publishLogs = ref<PublishLogItem[]>([])
  const mercadoLibreOrders = ref<MercadoLibreOrderItem[]>([])
  const mercadoLibreOrderNotifications = ref<MercadoLibreOrderNotification[]>([])
  const mercadoLibreOrdersTotal = ref(0)
  const mercadoLibreOrdersCheckedAt = ref('')
  const mercadoLibreUserProducts = ref<MercadoLibreUserProduct[]>([])
  const mercadoLibreUserProductStatus = ref('active')
  const mercadoLibreUserProductPage = ref(1)
  const mercadoLibreUserProductPerPage = ref(50)
  const mercadoLibreUserProductTotal = ref(0)
  const mercadoLibreUserProductTotalPages = ref(1)
  const mercadoLibreUserProductRefreshErrors = ref<UnknownRecord[]>([])
  const mercadoLibreUserProductsRefreshScope = ref('')
  const mercadoLibreUserProductsCheckedAt = ref('')
  const activeMarketplace = ref<Marketplace>('mercadolibre')
  const platformOptions = ref<MarketplaceOption[]>([])
  const publishResult = ref<UnknownRecord | null>(null)
  const activePublishTargetKey = ref('')
  const activity = useWorkflowActivityStore()

  async function fetchSelectedPublishJob(quiet = false) {
    const jobId = selectedPublishJobId.value || publishJob.value?.jobId || ''
    if (!jobId) return
    try {
      const detail = await fetchPublishJob(jobId)
      if (selectedPublishJobId.value === jobId || !selectedPublishJobId.value) {
        selectedPublishJobId.value = jobId
        publishJobStatus.value = detail
      }
      if (!quiet) activity.addLog(`发布任务状态已刷新：${jobId}`)
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '刷新发布任务失败')
    }
  }

  async function refreshPublishJob(options: { quiet?: boolean } = {}) {
    if (!selectedPublishJobId.value && !publishJob.value?.jobId) return
    publishJobsLoading.value = true
    if (!options.quiet) activity.setError('')
    try {
      await fetchSelectedPublishJob(Boolean(options.quiet))
    } finally {
      publishJobsLoading.value = false
    }
  }

  async function refreshPublishJobs(options: { quiet?: boolean } = {}) {
    if (publishJobsLoading.value) return
    publishJobsLoading.value = true
    if (!options.quiet) activity.setError('')
    try {
      const page = await fetchPublishJobs({ limit: 50 })
      publishJobs.value = page.items
      publishJobsNextCursor.value = page.nextCursor
      publishJobsLastUpdated.value = new Date().toLocaleString('sv-SE')
      const preferredId = selectedPublishJobId.value || publishJob.value?.jobId || ''
      selectedPublishJobId.value = page.items.some((item) => item.jobId === preferredId)
        ? preferredId
        : page.items[0]?.jobId || ''
      if (selectedPublishJobId.value) await fetchSelectedPublishJob(true)
      else publishJobStatus.value = null
      if (!options.quiet) activity.addLog(`发布任务已刷新：${page.items.length} 条。`)
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '读取发布任务失败')
    } finally {
      publishJobsLoading.value = false
    }
  }

  async function loadMorePublishJobs() {
    if (!publishJobsNextCursor.value || publishJobsLoading.value) return
    publishJobsLoading.value = true
    activity.setError('')
    try {
      const page = await fetchPublishJobs({
        limit: 50,
        cursor: publishJobsNextCursor.value,
      })
      const known = new Set(publishJobs.value.map((item) => item.jobId))
      publishJobs.value.push(...page.items.filter((item) => !known.has(item.jobId)))
      publishJobsNextCursor.value = page.nextCursor
      publishJobsLastUpdated.value = new Date().toLocaleString('sv-SE')
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '加载更多发布任务失败')
    } finally {
      publishJobsLoading.value = false
    }
  }

  async function selectPublishJob(jobId: string) {
    const selectedId = String(jobId || '').trim()
    if (!selectedId || selectedId === selectedPublishJobId.value) return
    selectedPublishJobId.value = selectedId
    publishJobStatus.value = null
    await refreshPublishJob()
  }

  async function reconcileSelectedPublishJob(jobId: string, platform: Marketplace) {
    const normalizedJobId = String(jobId || selectedPublishJobId.value || '').trim()
    const normalizedPlatform = String(platform || '').trim().toLowerCase() as Marketplace
    if (!normalizedJobId || !normalizedPlatform || publishJobsLoading.value) return
    publishJobsLoading.value = true
    activity.setError('')
    let shouldRefresh = false
    try {
      const result = await reconcilePublishJob(normalizedJobId, normalizedPlatform)
      const resolution = String(result.resolution || '').trim()
      activity.addLog(
        resolution === 'applied'
          ? `发布任务 ${normalizedJobId} 已对账：远端变更已生效。`
          : resolution === 'partially_applied'
            ? `发布任务 ${normalizedJobId} 已对账：远端仅部分生效，请按市场错误处理。`
          : resolution === 'not_applied'
            ? `发布任务 ${normalizedJobId} 已对账：远端变更未生效。`
            : `发布任务 ${normalizedJobId} 仍在处理中，保持结果待对账且不会重放发布。`,
      )
      shouldRefresh = true
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '发布结果对账失败')
    } finally {
      publishJobsLoading.value = false
    }
    if (shouldRefresh) await refreshPublishJobs({ quiet: true })
  }

  async function refreshPublishLogs() {
    activity.loading = true
    activity.setError('')
    try {
      publishLogs.value = await fetchPublishLogs()
      activity.addLog(`发布日志已刷新：${publishLogs.value.length} 条。`)
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '刷新发布日志失败')
    } finally {
      activity.loading = false
    }
  }

  async function refreshMercadoLibreUserProducts(
    status: string = mercadoLibreUserProductStatus.value,
    page?: number,
    perPage?: number,
    refreshIdentityMapping = false,
  ) {
    activity.loading = true
    activity.setError('')
    try {
      const nextStatus = status || mercadoLibreUserProductStatus.value
      const nextPerPage = perPage || mercadoLibreUserProductPerPage.value
      const nextPage = page || (nextStatus === mercadoLibreUserProductStatus.value ? mercadoLibreUserProductPage.value : 1)
      const result = await fetchMercadoLibreUserProducts(nextStatus, nextPage, nextPerPage, refreshIdentityMapping)
      if (!result.items.length && result.pagination.total > 0 && nextPage > 1) {
        // 第一次请求已完成全量 identity mapping 对账；回退页只读取本地快照，避免重复远端调用。
        const previous = await fetchMercadoLibreUserProducts(nextStatus, nextPage - 1, nextPerPage, false)
        mercadoLibreUserProducts.value = previous.items
        mercadoLibreUserProductPage.value = previous.pagination.page
        mercadoLibreUserProductPerPage.value = previous.pagination.perPage
        mercadoLibreUserProductTotal.value = previous.pagination.total
        mercadoLibreUserProductTotalPages.value = previous.pagination.totalPages
        mercadoLibreUserProductRefreshErrors.value = previous.refreshErrors
        mercadoLibreUserProductsRefreshScope.value = previous.refreshScope
        mercadoLibreUserProductsCheckedAt.value = previous.checkedAt
      } else {
        mercadoLibreUserProducts.value = result.items
        mercadoLibreUserProductPage.value = result.pagination.page
        mercadoLibreUserProductPerPage.value = result.pagination.perPage
        mercadoLibreUserProductTotal.value = result.pagination.total
        mercadoLibreUserProductTotalPages.value = result.pagination.totalPages
        mercadoLibreUserProductRefreshErrors.value = result.refreshErrors
        mercadoLibreUserProductsRefreshScope.value = result.refreshScope
        mercadoLibreUserProductsCheckedAt.value = result.checkedAt
      }
      mercadoLibreUserProductStatus.value = nextStatus
      const scopeNote = mercadoLibreUserProductsRefreshScope.value === 'identity_mapping_only'
        ? '身份映射已对账；状态与价格仍来自本地 publication 快照。'
        : '已读取本地 publication 快照。'
      activity.addLog(`Mercado Libre User Products ${scopeNote}第 ${mercadoLibreUserProductPage.value}/${mercadoLibreUserProductTotalPages.value} 页，当前 ${mercadoLibreUserProducts.value.length} 条，共 ${mercadoLibreUserProductTotal.value} 条。`)
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '读取 Mercado Libre User Products 失败')
    } finally {
      activity.loading = false
    }
  }

  async function refreshMercadoLibreOrders() {
    activity.loading = true
    activity.setError('')
    try {
      const result = await fetchMercadoLibreOrders(10, 0)
      mercadoLibreOrders.value = result.items
      mercadoLibreOrderNotifications.value = result.notifications
      mercadoLibreOrdersTotal.value = result.total
      mercadoLibreOrdersCheckedAt.value = result.checkedAt
      activity.addLog(`Mercado Libre 订单已刷新：${result.items.length} 条，通知 ${result.notifications.length} 条。`)
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : '读取 Mercado Libre 订单失败'
      activity.addLog(`Mercado Libre 订单暂不可用：${message}`)
    } finally {
      activity.loading = false
    }
  }

  async function pauseMercadoLibreUserProductById(sitelessUserProductId: string) {
    activity.loading = true
    activity.setError('')
    try {
      const result = await pauseMercadoLibreUserProduct(sitelessUserProductId)
      activity.addLog(String(result.message || `${sitelessUserProductId} 已暂停。`))
      await refreshMercadoLibreUserProducts(mercadoLibreUserProductStatus.value, mercadoLibreUserProductPage.value, mercadoLibreUserProductPerPage.value)
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '暂停 Mercado Libre User Product 失败')
    } finally {
      activity.loading = false
    }
  }

  return {
    pricingInput,
    pricingResult,
    category,
    categoryQuery,
    categoryResults,
    categoryRecommendations,
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
    precheckResults,
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
    activeMarketplace,
    platformOptions,
    publishResult,
    activePublishTargetKey,
    refreshPublishJob,
    refreshPublishJobs,
    loadMorePublishJobs,
    selectPublishJob,
    reconcileSelectedPublishJob,
    refreshPublishLogs,
    refreshMercadoLibreUserProducts,
    refreshMercadoLibreOrders,
    pauseMercadoLibreUserProductById,
  }
})
