import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  closeMercadoLibrePublishedItem,
  fetchMercadoLibreOrders,
  fetchMercadoLibrePublishedItems,
  fetchPublishJob,
  fetchPublishJobs,
  fetchPublishLogs,
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
  MercadoLibreRemoteItem,
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
  const mercadoLibreRemoteItems = ref<MercadoLibreRemoteItem[]>([])
  const mercadoLibreRemoteStatus = ref('active')
  const mercadoLibreRemotePage = ref(1)
  const mercadoLibreRemotePerPage = ref(50)
  const mercadoLibreRemoteTotal = ref(0)
  const mercadoLibreRemoteTotalPages = ref(1)
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

  async function refreshMercadoLibreRemoteItems(status: string = mercadoLibreRemoteStatus.value, page?: number, perPage?: number) {
    activity.loading = true
    activity.setError('')
    try {
      const nextStatus = status || mercadoLibreRemoteStatus.value
      const nextPerPage = perPage || mercadoLibreRemotePerPage.value
      const nextPage = page || (nextStatus === mercadoLibreRemoteStatus.value ? mercadoLibreRemotePage.value : 1)
      const result = await fetchMercadoLibrePublishedItems(nextStatus, nextPage, nextPerPage)
      if (!result.items.length && result.pagination.total > 0 && nextPage > 1) {
        const previous = await fetchMercadoLibrePublishedItems(nextStatus, nextPage - 1, nextPerPage)
        mercadoLibreRemoteItems.value = previous.items
        mercadoLibreRemotePage.value = previous.pagination.page
        mercadoLibreRemotePerPage.value = previous.pagination.perPage
        mercadoLibreRemoteTotal.value = previous.pagination.total
        mercadoLibreRemoteTotalPages.value = previous.pagination.totalPages
      } else {
        mercadoLibreRemoteItems.value = result.items
        mercadoLibreRemotePage.value = result.pagination.page
        mercadoLibreRemotePerPage.value = result.pagination.perPage
        mercadoLibreRemoteTotal.value = result.pagination.total
        mercadoLibreRemoteTotalPages.value = result.pagination.totalPages
      }
      mercadoLibreRemoteStatus.value = nextStatus
      activity.addLog(`Mercado Libre 远程商品已刷新：第 ${mercadoLibreRemotePage.value}/${mercadoLibreRemoteTotalPages.value} 页，当前 ${mercadoLibreRemoteItems.value.length} 条，共 ${mercadoLibreRemoteTotal.value} 条。`)
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '读取 Mercado Libre 已发布商品失败')
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

  async function closeMercadoLibreRemoteItem(itemId: string) {
    activity.loading = true
    activity.setError('')
    try {
      const result = await closeMercadoLibrePublishedItem(itemId)
      activity.addLog(String(result.message || `${itemId} 已下架。`))
      await refreshMercadoLibreRemoteItems(mercadoLibreRemoteStatus.value, mercadoLibreRemotePage.value, mercadoLibreRemotePerPage.value)
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '下架 Mercado Libre 商品失败')
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
    mercadoLibreRemoteItems,
    mercadoLibreRemoteStatus,
    mercadoLibreRemotePage,
    mercadoLibreRemotePerPage,
    mercadoLibreRemoteTotal,
    mercadoLibreRemoteTotalPages,
    activeMarketplace,
    platformOptions,
    publishResult,
    activePublishTargetKey,
    refreshPublishJob,
    refreshPublishJobs,
    loadMorePublishJobs,
    selectPublishJob,
    refreshPublishLogs,
    refreshMercadoLibreRemoteItems,
    refreshMercadoLibreOrders,
    closeMercadoLibreRemoteItem,
  }
})
