import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  fetchMercadoLibreOrders,
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

  async function checkJobsOnView(jobs: PublishJobListItem[]) {
    // 同一任务的平台依次确认，避免较早的摘要覆盖较新的平台结果。
    const pendingJobs = jobs.filter((job) => job.platforms.some((item) => (
      ['pending_confirmation', 'outcome_unknown'].includes(item.status)
    )))
    for (let offset = 0; offset < pendingJobs.length; offset += 3) {
      await Promise.all(pendingJobs.slice(offset, offset + 3).map(async (job) => {
        for (const item of job.platforms) {
          if (!['pending_confirmation', 'outcome_unknown'].includes(item.status)) continue
          try {
            const result = await reconcilePublishJob(job.jobId, item.platform, 'view')
            if (result.summary) {
              const summary = result.summary as unknown as PublishJobListItem
              publishJobs.value = publishJobs.value.map((current) => current.jobId === job.jobId ? summary : current)
            }
          } catch (exc) {
            activity.setError(exc instanceof Error ? exc.message : '查询发布结果失败')
          }
        }
      }))
    }
  }

  async function refreshPublishJobs(options: { quiet?: boolean; checkOnView?: boolean } = {}) {
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
      if (options.checkOnView) await checkJobsOnView(page.items)
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
    if (!selectedId || publishJobsLoading.value) return
    selectedPublishJobId.value = selectedId
    publishJobStatus.value = null
    publishJobsLoading.value = true
    try {
      const selected = publishJobs.value.find((job) => job.jobId === selectedId)
      if (selected) await checkJobsOnView([selected])
      await fetchSelectedPublishJob(true)
    } finally {
      publishJobsLoading.value = false
    }
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
      const messages: Record<string, string> = {
        applied: '平台已确认发布成功', partially_applied: '平台仅部分发布成功，请查看具体结果',
        not_applied: '平台已确认发布失败', pending: '平台仍在处理，可稍后查询',
        query_failed: '本次查询失败，保留上次发布状态，请查看查询错误',
        cooldown: '刚刚查询过，请稍后再查', checking: '已有查询正在执行',
        terminal_or_submitting: '任务已结束或仍在提交，请查看当前状态',
      }
      activity.addLog(`发布任务 ${normalizedJobId}：${messages[resolution] || '结果仍待核实'}`)
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
    refreshMercadoLibreOrders,
  }
})
