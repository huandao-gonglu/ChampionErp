import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  assignUpc as assignUpcApi,
  buildMercadoLibreAuthLink,
  calculatePrice as calculatePriceApi,
  claimProducts as claimProductsApi,
  closeMercadoLibrePublishedItem,
  clearStoreAuth,
  clean1688Text,
  collectBatch as collectBatchApi,
  collectFromBrowserTab as collectFromBrowserTabApi,
  collectProduct as collectProductApi,
  confirmMercadoLibreRealPublish,
  diagnosticsToCollectDiagnostics,
  enqueuePublish as enqueuePublishApi,
  exchangeMercadoLibreCode,
  deleteProducts as deleteProductsApi,
  fetchAiConfig,
  fetchBrowserDebugStatus,
  fetchCategoryCacheRefreshJob,
  fetchCategoryAttrs,
  fetchDraftsIndex,
  fetchMercadoLibreOrders,
  fetchMercadoLibreAuthChecklist,
  fetchMercadoLibrePublishedItems,
  fetchProductsIndex,
  fetchPublishJob,
  fetchPublishLogs,
  fetchState,
  fillCategoryAttributes,
  generateCopy as generateCopyApi,
  generateCopyBatch,
  generateImagePrompts,
  imagePoolAction,
  imageTranslate as imageTranslateApi,
  importManualProduct,
  loadDraft as loadDraftApi,
  loadProduct as loadProductApi,
  open1688Browser as open1688BrowserApi,
  openAuthLink,
  openBrowserProfile,
  previewPublishPayload,
  publishProductDirect,
  publishPrecheck,
  refreshMercadoLibreToken,
  runCategoryPrecheck,
  runMercadoLibreRealAuthTest,
  saveAiConfig,
  saveCollectSettings as saveCollectSettingsApi,
  saveImagePool,
  saveProduct as saveProductApi,
  saveStoreSettings,
  searchCategories,
  startCategoryCacheRefresh,
  suggestCategories,
  syncGeneratedImages,
  testAiChannel,
  testStoreAuth,
  uploadImages,
} from '@/api/workflow'
import { createDefaultCollectDiagnostics, createDefaultCollectForm, createDefaultPricingInput, createEmptyProduct, marketplaces } from '@/constants/initialState'
import { useAppStore } from '@/stores/app'
import type {
  AuthResult,
  BrowserDebugStatus,
  CategoryPrecheckResult,
  CategorySearchResult,
  CategorySelection,
  CollectBatchRow,
  CollectDiagnostics,
  CollectForm,
  DraftIndexItem,
  Marketplace,
  MercadoLibreAuthChecklist,
  MercadoLibreOrderItem,
  MercadoLibreOrderNotification,
  MercadoLibreRemoteItem,
  MercadoLibreTestMode,
  PrecheckIssue,
  PricingInput,
  PricingResult,
  Product,
  ProductIndexItem,
  PublishJob,
  PublishLogItem,
  PublishPrecheck,
  UnknownRecord,
  WorkflowStep,
} from '@/types/workflow'

function asStoreAuthSummary(raw: UnknownRecord): UnknownRecord | null {
  const value = raw.storeAuthSummary
  return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : null
}

function authResultError(result: AuthResult, fallback: string): string {
  return result.error || result.message || result.nextAction || fallback
}

function isRecord(value: unknown): value is UnknownRecord {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function mergeAiConfigWithSubmitted(publicConfig: UnknownRecord, submittedConfig: UnknownRecord): UnknownRecord {
  const merged: UnknownRecord = { ...publicConfig }
  for (const section of ['text_ai', 'image_ai']) {
    const publicSection = isRecord(publicConfig[section]) ? publicConfig[section] as UnknownRecord : {}
    const submittedSection = isRecord(submittedConfig[section]) ? submittedConfig[section] as UnknownRecord : {}
    merged[section] = { ...publicSection, ...submittedSection }
  }
  return merged
}

function collectStats(product: Product) {
  return {
    downloadedImages: product.source.imagePool.length,
    extractedBullets: product.sellingPoints.length,
  }
}

function hasProductForPublish(product: Product) {
  return Boolean(product.productId || product.name || product.source.title)
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(new Error(`读取文件失败：${file.name}`))
    reader.readAsDataURL(file)
  })
}

function parseNumber(value: string | number): number {
  const parsed = typeof value === 'number' ? value : Number.parseFloat(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function precheckIssueFromRaw(value: unknown, fallbackSeverity: 'error' | 'warning'): PrecheckIssue {
  const record = isRecord(value) ? value : {}
  return {
    code: String(record.code || ''),
    field: String(record.field || ''),
    message: String(record.message || value || ''),
    severity: String(record.severity || fallbackSeverity) as PrecheckIssue['severity'],
    nextAction: String(record.next_action || record.nextAction || ''),
  }
}

function precheckMessages(items: unknown): string[] {
  if (!Array.isArray(items)) return []
  return items.map((item) => isRecord(item) ? String(item.message || item.code || '') : String(item || '')).filter(Boolean)
}

function categorySelectionFromProduct(product: Product, platform: Marketplace): CategorySelection | null {
  const categories = isRecord(product.raw.local_platform_categories) ? product.raw.local_platform_categories : {}
  const record = isRecord(categories[platform]) ? categories[platform] as UnknownRecord : null
  if (!record) return null
  const attrs = isRecord(record.attributes_cache) ? record.attributes_cache : {}
  const normalizeAttr = (item: unknown, requiredFallback: boolean) => {
    const attr = isRecord(item) ? item : {}
    return {
      id: String(attr.id || attr.attribute_id || ''),
      name: String(attr.name || attr.label || attr.id || attr.attribute_id || ''),
      required: typeof attr.required === 'boolean' ? attr.required : requiredFallback,
      options: Array.isArray(attr.options) ? attr.options.map(String) : [],
    }
  }
  const requiredAttributes = Array.isArray(attrs.required)
    ? attrs.required.map((item) => normalizeAttr(item, true)).filter((item) => item.id && item.required)
    : []
  const optionalAttributes = Array.isArray(attrs.optional)
    ? attrs.optional.map((item) => normalizeAttr(item, false)).filter((item) => item.id)
    : []
  const categoryId = String(record.category_id || record.subject_id || record.type_id || product.drafts[platform].categoryId || '')
  if (!categoryId && !requiredAttributes.length && !optionalAttributes.length) return null
  return {
    platform,
    categoryId,
    categoryPath: String(record.category_path || record.path || record.name_original || product.drafts[platform].categoryPath || ''),
    requiredAttributes,
    optionalAttributes,
  }
}

export const useWorkflowStore = defineStore('workflow', () => {
  const product = ref<Product>(createEmptyProduct())
  const collectForm = ref<CollectForm>(createDefaultCollectForm())
  const collectDiagnostics = ref<CollectDiagnostics>(createDefaultCollectDiagnostics())
  const collectBatchRows = ref<CollectBatchRow[]>([])
  const browserDebugStatus = ref<BrowserDebugStatus | null>(null)
  const productsIndex = ref<ProductIndexItem[]>([])
  const draftsIndex = ref<DraftIndexItem[]>([])
  const selectedProductIds = ref<string[]>([])
  const pricingInput = ref<PricingInput>(createDefaultPricingInput())
  const pricingResult = ref<PricingResult | null>(null)
  const category = ref<CategorySelection | null>(null)
  const categoryQuery = ref('')
  const categoryResults = ref<CategorySearchResult[]>([])
  const categoryCacheStatus = ref<UnknownRecord>({})
  const categoryPrecheck = ref<CategoryPrecheckResult | null>(null)
  const precheck = ref<PublishPrecheck | null>(null)
  const precheckResults = ref<UnknownRecord>({})
  const payloadPreview = ref<UnknownRecord | null>(null)
  const publishJob = ref<PublishJob | null>(null)
  const publishJobStatus = ref<UnknownRecord | null>(null)
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
  const logs = ref<string[]>(['等待读取后端状态。'])
  const appConfig = ref<UnknownRecord>({})
  const aiConfig = ref<UnknownRecord>({})
  const storeConfig = ref<UnknownRecord>({})
  const storeAuthSummary = ref<UnknownRecord>({})
  const mercadolibreAuthChecklist = ref<MercadoLibreAuthChecklist | null>(null)
  const lastAuthResult = ref<AuthResult | null>(null)
  const authLink = ref('')
  const publishResult = ref<UnknownRecord | null>(null)
  const imagePrompt = ref('')
  const currentStage = ref(0)
  const loading = ref(false)
  const error = ref('')

  const draft = computed(() => product.value.drafts[activeMarketplace.value])
  const imagePool = computed(() => product.value.source.imagePool)
  const selectedImages = computed(() => imagePool.value.filter((image) => image.selected))
  const selectedProducts = computed(() => productsIndex.value.filter((item) => selectedProductIds.value.includes(item.productId)))

  function applyMutationIndexes(result: { productsIndex?: ProductIndexItem[]; draftsIndex?: DraftIndexItem[] }) {
    if (result.productsIndex?.length) productsIndex.value = result.productsIndex
    if (result.draftsIndex) draftsIndex.value = result.draftsIndex
  }

  const workflowSteps = computed<WorkflowStep[]>(() => {
    const hasCollected = Boolean(product.value.source.title || product.value.name)
    const hasLibrary = productsIndex.value.length > 0
    const hasCopy = Object.values(product.value.drafts).some((item) => ['copy_ready', 'images_ready', 'ready_to_publish', 'published'].includes(item.status) || Boolean(item.title && item.description))
    const hasImages = product.value.source.imagePool.length > 0 || Object.values(product.value.drafts).some((item) => item.images.length > 0)
    const hasEdit = Boolean(product.value.productId && (product.value.sku || product.value.stock || product.value.upc || product.value.brand || product.value.model))
    const hasPrice = Boolean(product.value.drafts.mercadolibre.price || pricingResult.value)
    const hasCategory = Boolean(product.value.drafts.mercadolibre.categoryId || category.value)
    const hasPrecheck = Boolean(precheck.value?.ok || product.value.drafts.mercadolibre.status === 'ready_to_publish')
    const hasPublished = publishJob.value?.status === 'completed' || product.value.drafts.mercadolibre.status === 'published'
    const flags = [hasCollected, hasLibrary, hasCopy, hasImages, hasEdit, hasPrice, hasCategory, hasPrecheck, hasPublished]
    return [
      ['collect', '采集商品', '链接、Cookie、浏览器标签、手动导入'],
      ['library', '商品库', 'SQLite 本地商品库和批量认领'],
      ['copy', 'AI 文案', '生成目标平台标题、描述、卖点'],
      ['images', '图片处理', '上传、图片池、图片翻译'],
      ['edit', '商品编辑', '基础信息、SKU、UPC、库存'],
      ['pricing', '核价', '成本、运费、汇率、佣金'],
      ['category', '类目属性', '搜索类目并补齐必填属性'],
      ['precheck', '发布预检', '生成 payload 并检查缺项'],
      ['publish', '发布队列', '入队、状态、发布日志'],
    ].map(([key, title, description], index) => ({
      key,
      title,
      description,
      status: flags[index] ? 'done' : index === currentStage.value ? 'active' : index < currentStage.value ? 'blocked' : 'pending',
    } satisfies WorkflowStep))
  })

  const progressPercent = computed(() => {
    const done = workflowSteps.value.filter((step) => step.status === 'done').length
    return Math.round((done / workflowSteps.value.length) * 100)
  })

  function restorePrecheckFromProduct() {
    const previews = isRecord(product.value.raw.publish_preview) ? product.value.raw.publish_preview : {}
    const raw = isRecord(previews[activeMarketplace.value]) ? previews[activeMarketplace.value] as UnknownRecord : null
    if (!raw) {
      precheck.value = null
      return
    }
    const errorItems = Array.isArray(raw.errors) ? raw.errors.map((item) => precheckIssueFromRaw(item, 'error')) : []
    const warningItems = Array.isArray(raw.warnings) ? raw.warnings.map((item) => precheckIssueFromRaw(item, 'warning')) : []
    precheck.value = {
      ok: raw.ok === true,
      errors: precheckMessages(raw.errors),
      warnings: precheckMessages(raw.warnings),
      errorItems,
      warningItems,
      checkedAt: String(raw.checked_at || raw.checkedAt || ''),
    }
  }

  function restoreCategoryFromProduct() {
    category.value = categorySelectionFromProduct(product.value, activeMarketplace.value)
  }

  function addLog(message: string) {
    logs.value.unshift(`${new Date().toLocaleTimeString()} ${message}`)
  }

  function setError(message: string) {
    error.value = message
    if (message) {
      addLog(`错误：${message}`)
      useAppStore().pushToast(message, 'error')
    }
  }

  function syncCollectDiagnosticsFromProduct(message = '已读取后端商品状态。', raw?: UnknownRecord) {
    if (raw && Object.keys(raw).length) {
      collectDiagnostics.value = diagnosticsToCollectDiagnostics(raw, product.value, message)
      return
    }
    const stats = collectStats(product.value)
    collectDiagnostics.value = {
      ...createDefaultCollectDiagnostics(),
      status: product.value.source.title || product.value.name ? 'success' : 'idle',
      progress: product.value.source.title || product.value.name ? 100 : 0,
      message,
      downloadedImages: stats.downloadedImages,
      extractedBullets: stats.extractedBullets,
      antiBotWarning: false,
      lastSourceUrl: product.value.source.sourceUrl,
      raw: product.value.source.collectDiagnostics,
      errorCode: String(product.value.source.collectDiagnostics.error_code || ''),
      nextAction: String(product.value.source.collectDiagnostics.next_action || ''),
      htmlSnapshotPath: String(product.value.source.collectDiagnostics.html_snapshot_path || ''),
      screenshotPath: String(product.value.source.collectDiagnostics.screenshot_path || ''),
    }
  }

  function fillFormFromState(nextAppConfig: UnknownRecord, outputDir = '') {
    collectForm.value.alibabaCookie = String(nextAppConfig.alibaba_cookie || collectForm.value.alibabaCookie || '')
    collectForm.value.autoAiRecognition = String(nextAppConfig.auto_ai_recognition ?? '1') !== '0'
    collectForm.value.outputDir = String(nextAppConfig.collect_output_dir || outputDir || collectForm.value.outputDir || '')
    collectForm.value.productUrl = product.value.source.sourceUrl || collectForm.value.productUrl
    collectForm.value.platform = product.value.source.sourcePlatform || collectForm.value.platform || '1688'
    collectForm.value.manualTitle = product.value.source.title || product.value.name || ''
    collectForm.value.manualPrice = product.value.source.price || ''
    collectForm.value.manualDescription = product.value.source.description || ''
    collectForm.value.manualWeight = product.value.source.weightKg || ''
    const dims = product.value.source.dimensions
    collectForm.value.manualDimensions = [dims.lengthCm, dims.widthCm, dims.heightCm].filter(Boolean).join(' x ')
  }

  function syncPricingInputFromProduct() {
    pricingInput.value.purchaseCostCny = parseNumber(product.value.cost || product.value.source.price || pricingInput.value.purchaseCostCny)
    pricingInput.value.weightKg = parseNumber(product.value.source.weightKg || pricingInput.value.weightKg)
    pricingInput.value.lengthCm = parseNumber(product.value.source.dimensions.lengthCm || pricingInput.value.lengthCm)
    pricingInput.value.widthCm = parseNumber(product.value.source.dimensions.widthCm || pricingInput.value.widthCm)
    pricingInput.value.heightCm = parseNumber(product.value.source.dimensions.heightCm || pricingInput.value.heightCm)
  }

  async function loadState() {
    loading.value = true
    setError('')
    try {
      const state = await fetchState()
      product.value = state.product
      restoreCategoryFromProduct()
      restorePrecheckFromProduct()
      productsIndex.value = state.productsIndex
      draftsIndex.value = state.draftsIndex || []
      publishLogs.value = state.publishLogs
      appConfig.value = state.appConfig
      storeConfig.value = state.storeConfig
      storeAuthSummary.value = state.storeAuthSummary
      mercadolibreAuthChecklist.value = state.mercadolibreAuthChecklist || null
      mercadoLibreOrderNotifications.value = state.mercadolibreOrderNotifications || []
      aiConfig.value = state.appConfig
      fillFormFromState(state.appConfig, state.outputDir)
      syncCollectDiagnosticsFromProduct('后端状态已加载。')
      syncPricingInputFromProduct()
      addLog('已读取后端商品、商品库、图片池和配置。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '读取后端状态失败')
    } finally {
      loading.value = false
    }
  }

  function resetForm() {
    collectForm.value = createDefaultCollectForm()
    collectDiagnostics.value = createDefaultCollectDiagnostics()
    setError('')
    addLog('已清空采集表单。')
  }

  async function collectProduct() {
    if (!collectForm.value.productUrl.trim() && !collectForm.value.manualTitle.trim() && !collectForm.value.rawText.trim()) {
      setError('请先输入产品网址，或填写手动标题/粘贴原始文本。')
      return
    }
    loading.value = true
    setError('')
    collectDiagnostics.value = {
      ...collectDiagnostics.value,
      status: 'running',
      progress: 20,
      message: '正在提交采集任务...',
      lastSourceUrl: collectForm.value.productUrl,
    }
    try {
      const result = await collectProductApi(collectForm.value)
      product.value = result.product
      applyMutationIndexes(result)
      syncCollectDiagnosticsFromProduct('采集完成。', result.diagnostics)
      syncPricingInputFromProduct()
      currentStage.value = 1
      addLog(`采集完成：${product.value.source.title || product.value.name || product.value.productId || '未命名商品'}`)
      if (collectForm.value.autoAiRecognition) addLog('已开启自动 AI 识别：请进入“文案”页面生成平台文案。')
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : '采集失败'
      collectDiagnostics.value = {
        ...collectDiagnostics.value,
        status: 'failed',
        progress: 0,
        message,
        antiBotWarning: message.includes('安全验证') || message.includes('Cookie') || message.includes('反爬') || message.includes('验证码'),
      }
      setError(message)
    } finally {
      loading.value = false
    }
  }

  async function collectBatch() {
    if (!collectForm.value.productUrls.trim()) {
      setError('请先输入多链接，每行一个商品链接。')
      return
    }
    loading.value = true
    setError('')
    try {
      const result = await collectBatchApi(collectForm.value)
      collectBatchRows.value = result.rows
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      const firstOk = result.rows.find((row) => row.product)
      if (firstOk?.product) product.value = firstOk.product
      syncCollectDiagnosticsFromProduct(`批量采集完成：${result.rows.filter((row) => row.ok).length}/${result.rows.length} 成功。`)
      currentStage.value = 1
      addLog(`批量采集完成：${result.rows.length} 条。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '批量采集失败')
    } finally {
      loading.value = false
    }
  }

  async function collectFromBrowserTab(saveOnly = false) {
    loading.value = true
    setError('')
    try {
      const result = await collectFromBrowserTabApi(collectForm.value, saveOnly)
      product.value = result.product
      applyMutationIndexes(result)
      if (result.browserStatus) browserDebugStatus.value = result.browserStatus
      syncCollectDiagnosticsFromProduct(saveOnly ? 'HTML 快照已保存。' : '已从浏览器标签采集。', result.diagnostics)
      syncPricingInputFromProduct()
      currentStage.value = 1
      addLog(saveOnly ? '已保存当前浏览器标签 HTML 快照。' : '已从当前浏览器标签采集商品。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '从浏览器标签采集失败')
    } finally {
      loading.value = false
    }
  }

  async function open1688Browser() {
    loading.value = true
    setError('')
    try {
      const message = await open1688BrowserApi()
      addLog(message)
      await checkBrowserDebugStatus()
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '打开 1688 浏览器失败')
    } finally {
      loading.value = false
    }
  }

  async function checkBrowserDebugStatus() {
    try {
      browserDebugStatus.value = await fetchBrowserDebugStatus()
      addLog(browserDebugStatus.value.connected ? '浏览器调试端口已连接。' : `浏览器未连接：${browserDebugStatus.value.errorMessage}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '检测浏览器失败')
    }
  }

  async function openDebugProfile() {
    try {
      lastAuthResult.value = await openBrowserProfile()
      addLog(lastAuthResult.value.message || '已请求打开浏览器 profile。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '打开浏览器 profile 失败')
    }
  }

  async function importManual() {
    loading.value = true
    setError('')
    try {
      const result = await importManualProduct(collectForm.value)
      product.value = result.product
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      syncCollectDiagnosticsFromProduct('手动导入完成。', result.diagnostics)
      syncPricingInputFromProduct()
      currentStage.value = 1
      addLog(`手动导入完成：${product.value.source.title || product.value.name || '未命名商品'}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '手动导入失败')
    } finally {
      loading.value = false
    }
  }

  async function previewClean1688Text() {
    const raw = `${collectForm.value.rawText || collectForm.value.manualDescription || ''}`.trim()
    if (!raw) {
      setError('请先在“原始文本 / HTML 导入”里粘贴 1688 文本或 HTML。')
      return
    }
    loading.value = true
    setError('')
    try {
      const cleaned = await clean1688Text(raw, collectForm.value.productUrl)
      collectForm.value.platform = '1688'
      collectForm.value.manualTitle = String(cleaned.title || collectForm.value.manualTitle || '')
      collectForm.value.manualPrice = String(cleaned.source_price_cny || cleaned.source_price_cny_for_cost || collectForm.value.manualPrice || '')
      collectForm.value.manualDimensions = String(cleaned.dimensions || collectForm.value.manualDimensions || '')
      collectForm.value.manualWeight = String(cleaned.source_weight_kg || collectForm.value.manualWeight || '')
      collectForm.value.manualDescription = String(cleaned.clean_source_text || cleaned.source_text || collectForm.value.manualDescription || '')
      if (Array.isArray(cleaned.images) && cleaned.images.length) collectForm.value.manualImages = cleaned.images.map(String).join('\n')
      collectDiagnostics.value = {
        ...collectDiagnostics.value,
        status: cleaned.ok === false ? 'failed' : 'success',
        progress: cleaned.ok === false ? 0 : 100,
        message: String(cleaned.message || '1688 文本已清洗，可检查字段后导入商品库。'),
        downloadedImages: Array.isArray(cleaned.images) ? cleaned.images.length : collectDiagnostics.value.downloadedImages,
        extractedBullets: Array.isArray(cleaned.package_includes) ? cleaned.package_includes.length : collectDiagnostics.value.extractedBullets,
        antiBotWarning: Boolean(cleaned.manual_required),
        raw: cleaned,
      }
      addLog(`1688 文本清洗完成：${collectForm.value.manualTitle || '未识别标题'}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '1688 文本清洗失败')
    } finally {
      loading.value = false
    }
  }

  async function clearCollectedProduct() {
    product.value = createEmptyProduct()
    pricingResult.value = null
    category.value = null
    precheck.value = null
    publishJob.value = null
    payloadPreview.value = null
    collectDiagnostics.value = {
      ...createDefaultCollectDiagnostics(),
      message: '已清空当前商品，等待重新采集。',
    }
    currentStage.value = 0
    setError('')
    addLog('已清空当前商品。')
  }

  async function saveCollectSettings() {
    loading.value = true
    setError('')
    try {
      await saveCollectSettingsApi(collectForm.value)
      addLog(`采集设置已保存：模式 ${collectForm.value.mode}，输出目录 ${collectForm.value.outputDir || '默认目录'}。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '保存采集设置失败')
    } finally {
      loading.value = false
    }
  }

  async function refreshProductsIndex() {
    loading.value = true
    setError('')
    try {
      productsIndex.value = await fetchProductsIndex()
      addLog(`商品库已刷新：${productsIndex.value.length} 条。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '刷新商品库失败')
    } finally {
      loading.value = false
    }
  }

  async function refreshDraftsIndex(scope = 'active') {
    loading.value = true
    setError('')
    try {
      draftsIndex.value = await fetchDraftsIndex(scope)
      addLog(`草稿箱已刷新：${draftsIndex.value.length} 条。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '刷新草稿箱失败')
    } finally {
      loading.value = false
    }
  }

  async function loadProduct(item: ProductIndexItem) {
    loading.value = true
    setError('')
    try {
      const result = await loadProductApi(item.productId, item.productFilePath)
      product.value = result.product
      restoreCategoryFromProduct()
      restorePrecheckFromProduct()
      applyMutationIndexes(result)
      fillFormFromState(appConfig.value)
      syncCollectDiagnosticsFromProduct('已加载商品库商品。')
      syncPricingInputFromProduct()
      addLog(`已加载商品：${product.value.source.title || product.value.name || item.productId}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '加载商品失败')
    } finally {
      loading.value = false
    }
  }

  async function loadDraft(item: DraftIndexItem) {
    loading.value = true
    setError('')
    try {
      const result = await loadDraftApi(item.draftId)
      product.value = result.product
      activeMarketplace.value = item.platform
      restoreCategoryFromProduct()
      restorePrecheckFromProduct()
      applyMutationIndexes(result)
      fillFormFromState(appConfig.value)
      syncCollectDiagnosticsFromProduct('已加载平台草稿。')
      syncPricingInputFromProduct()
      addLog(`已加载草稿：${item.title || item.productTitle || item.draftId}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '加载草稿失败')
    } finally {
      loading.value = false
    }
  }

  async function deleteProductsByIds(productIds: string[]) {
    const ids = Array.from(new Set(productIds.map((id) => String(id || '').trim()).filter(Boolean)))
    if (!ids.length) {
      setError('请先选择要删除的商品。')
      return
    }
    loading.value = true
    setError('')
    try {
      const result = await deleteProductsApi(ids)
      productsIndex.value = result.productsIndex
      selectedProductIds.value = selectedProductIds.value.filter((id) => !result.deletedIds.includes(id))
      if (result.product && result.deletedIds.includes(product.value.productId)) {
        product.value = result.product
        syncCollectDiagnosticsFromProduct('当前商品已删除。')
        syncPricingInputFromProduct()
      }
      addLog(result.message || `已删除 ${result.deleted} 个商品。`)
      if (result.missingIds.length) addLog(`未找到商品：${result.missingIds.join('、')}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '删除商品失败')
    } finally {
      loading.value = false
    }
  }

  async function deleteProduct(item: ProductIndexItem) {
    await deleteProductsByIds([item.productId])
  }

  async function deleteSelectedProducts() {
    await deleteProductsByIds(selectedProductIds.value)
  }

  function toggleProductSelection(productId: string, checked?: boolean) {
    const exists = selectedProductIds.value.includes(productId)
    const shouldAdd = checked ?? !exists
    if (shouldAdd && !exists) selectedProductIds.value.push(productId)
    if (!shouldAdd) selectedProductIds.value = selectedProductIds.value.filter((id) => id !== productId)
  }

  function selectAllProducts(checked: boolean, productIds?: string[]) {
    const targetIds = (productIds?.length ? productIds : productsIndex.value.map((item) => item.productId)).filter(Boolean)
    if (!checked) {
      selectedProductIds.value = selectedProductIds.value.filter((id) => !targetIds.includes(id))
      return
    }
    selectedProductIds.value = Array.from(new Set([...selectedProductIds.value, ...targetIds]))
  }

  async function claimSelectedProducts() {
    const ids = selectedProductIds.value.length
      ? selectedProductIds.value
      : product.value.productId
        ? [product.value.productId]
        : productsIndex.value.map((item) => item.productId).filter(Boolean)
    if (!ids.length) {
      setError('请先选择商品。')
      return false
    }
    loading.value = true
    setError('')
    try {
      const platforms = collectForm.value.selectedClaimPlatforms.length ? collectForm.value.selectedClaimPlatforms : [activeMarketplace.value]
      await claimProductsApi(ids, platforms)
      productsIndex.value = await fetchProductsIndex()
      draftsIndex.value = await fetchDraftsIndex()
      addLog(`已认领 ${ids.length} 个商品到平台草稿。`)
      return true
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '认领商品失败')
      return false
    } finally {
      loading.value = false
    }
  }

  async function claimCurrentProduct() {
    const id = product.value.productId
    if (!id) {
      setError('请先从商品库加载一个商品。')
      return false
    }
    loading.value = true
    setError('')
    try {
      await claimProductsApi([id], [activeMarketplace.value])
      const loaded = await loadProductApi(id, '')
      product.value = loaded.product
      productsIndex.value = await fetchProductsIndex()
      draftsIndex.value = await fetchDraftsIndex()
      addLog(`已推到 ${activeMarketplace.value} 平台草稿箱。`)
      return true
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '推到平台草稿箱失败')
      return false
    } finally {
      loading.value = false
    }
  }

  async function generateCopyForSelectedProducts() {
    const ids = selectedProductIds.value.length ? selectedProductIds.value : product.value.productId ? [product.value.productId] : []
    if (!ids.length) {
      setError('请先选择要批量生成文案的商品。')
      return
    }
    loading.value = true
    setError('')
    try {
      const result = await generateCopyBatch(ids, activeMarketplace.value)
      productsIndex.value = await fetchProductsIndex()
      addLog(`批量 AI 文案完成：${ids.length} 个商品，平台 ${activeMarketplace.value}。${String(result.message || '')}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '批量生成文案失败')
    } finally {
      loading.value = false
    }
  }

  async function enqueueSelectedProducts() {
    const ids = selectedProductIds.value.length ? selectedProductIds.value : product.value.productId ? [product.value.productId] : []
    if (!ids.length) {
      setError('请先选择要发布入队的商品。')
      return
    }
    loading.value = true
    setError('')
    try {
      let success = 0
      const failures: string[] = []
      for (const id of ids) {
        const item = productsIndex.value.find((entry) => entry.productId === id)
        if (!item) continue
        try {
          const loaded = await loadProductApi(item.productId, item.productFilePath)
          await enqueuePublishApi(loaded.product, [activeMarketplace.value])
          success += 1
        } catch (exc) {
          failures.push(`${item.title || id}: ${exc instanceof Error ? exc.message : '入队失败'}`)
        }
      }
      productsIndex.value = await fetchProductsIndex()
      addLog(`选中商品发布入队完成：成功 ${success}/${ids.length}。${failures.length ? `失败：${failures.join('；')}` : ''}`)
      if (failures.length) setError(failures[0])
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '选中商品发布入队失败')
    } finally {
      loading.value = false
    }
  }

  async function uploadReferenceImages(files: File[]) {
    if (!files.length) return
    loading.value = true
    setError('')
    try {
      const uploads = await Promise.all(
        files.map(async (file) => ({
          filename: file.name,
          data_url: await readFileAsDataUrl(file),
        })),
      )
      const result = await uploadImages(product.value, uploads)
      product.value = result.product
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      syncCollectDiagnosticsFromProduct('参考图片已上传。')
      addLog(`已上传 ${files.length} 张参考图片。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '上传参考图片失败')
    } finally {
      loading.value = false
    }
  }

  async function clearSourceImages() {
    loading.value = true
    setError('')
    try {
      const nextProduct: Product = { ...product.value, source: { ...product.value.source, imagePool: [] } }
      const result = await saveProductApi(nextProduct)
      product.value = result.product
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      syncCollectDiagnosticsFromProduct('已清除参考图片。')
      addLog('已清除参考图片并保存商品。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '清除参考图片失败')
    } finally {
      loading.value = false
    }
  }

  async function saveCurrentImagePool() {
    loading.value = true
    setError('')
    try {
      const result = await saveImagePool(product.value, product.value.source.imagePool)
      product.value = result.product
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      addLog('图片池变更已保存。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '保存图片池失败')
    } finally {
      loading.value = false
    }
  }

  async function setMainImage(imageId: string) {
    loading.value = true
    setError('')
    try {
      const result = await imagePoolAction(product.value, 'set_main', { image_id: imageId })
      product.value = result.product
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      addLog(`已设置主图：${imageId}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '设置主图失败')
    } finally {
      loading.value = false
    }
  }

  async function deleteImages(imageIds: string[]) {
    if (!imageIds.length) return
    loading.value = true
    setError('')
    try {
      const result = await imagePoolAction(product.value, 'delete', { image_ids: imageIds })
      product.value = result.product
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      addLog(`已删除 ${imageIds.length} 张图片。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '删除图片失败')
    } finally {
      loading.value = false
    }
  }

  async function syncGeneratedImagePool() {
    loading.value = true
    setError('')
    try {
      const result = await syncGeneratedImages(product.value)
      product.value = result.product
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      addLog('已同步生成图到当前商品图片池。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '同步生成图失败')
    } finally {
      loading.value = false
    }
  }

  async function saveCurrentProduct() {
    loading.value = true
    setError('')
    try {
      const result = await saveProductApi(product.value)
      product.value = result.product
      applyMutationIndexes(result)
      syncPricingInputFromProduct()
      addLog('商品草稿已保存。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '保存商品失败')
    } finally {
      loading.value = false
    }
  }

  async function assignUpc() {
    loading.value = true
    setError('')
    try {
      const result = await assignUpcApi()
      const assignedUpc = String(result.raw?.upc || result.product.upc || '')
      if (result.product.productId || result.product.name || result.product.source.title) {
        product.value = result.product
      } else if (assignedUpc) {
        product.value.upc = assignedUpc
      }
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      addLog(`UPC 已分配：${assignedUpc || product.value.upc || '已写入商品'}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '分配 UPC 失败')
    } finally {
      loading.value = false
    }
  }

  async function generateCopy() {
    loading.value = true
    setError('')
    try {
      const result = await generateCopyApi(product.value, activeMarketplace.value)
      product.value = result.product
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      currentStage.value = 2
      addLog(`${activeMarketplace.value} 文案已生成。${result.warning ? `提示：${result.warning}` : ''}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '生成文案失败')
    } finally {
      loading.value = false
    }
  }

  async function generateImagePromptPack(targetLanguage?: string) {
    loading.value = true
    setError('')
    try {
      const language = String(targetLanguage || '').trim()
      imagePrompt.value = await generateImagePrompts(product.value, activeMarketplace.value, language)
      addLog(`生图提示词已生成。${language ? `目标语言：${language}` : ''}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '生成图片提示词失败')
    } finally {
      loading.value = false
    }
  }

  async function translateImages(targetLanguage?: string) {
    loading.value = true
    setError('')
    try {
      const language = String(targetLanguage || '').trim() || (activeMarketplace.value === 'mercadolibre' ? 'Spanish (Mexico)' : 'Russian')
      const result = await imageTranslateApi(product.value, activeMarketplace.value, language)
      product.value = result.product
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      currentStage.value = 3
      addLog(`图片翻译/重绘完成：${language}。${result.message ? `提示：${result.message}` : ''}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '图片翻译/重绘失败')
    } finally {
      loading.value = false
    }
  }

  async function calculatePrice() {
    loading.value = true
    setError('')
    try {
      pricingResult.value = await calculatePriceApi(pricingInput.value)
      if (pricingResult.value.usdCnyRate > 0) pricingInput.value.usdCnyRate = pricingResult.value.usdCnyRate
      if (pricingResult.value.mxnUsdRate > 0) pricingInput.value.mxnUsdRate = pricingResult.value.mxnUsdRate
      product.value.drafts.mercadolibre.price = String(pricingResult.value.suggestedPriceMxn || '')
      const saved = await saveProductApi(product.value)
      product.value = saved.product
      if (saved.productsIndex.length) productsIndex.value = saved.productsIndex
      currentStage.value = 5
      addLog(`核价完成：建议美客多售价 ${pricingResult.value.suggestedPriceMxn} MXN。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '核价失败')
    } finally {
      loading.value = false
    }
  }

  async function searchCategory() {
    if (!categoryQuery.value.trim()) {
      setError('请输入类目搜索关键词。')
      return
    }
    loading.value = true
    setError('')
    try {
      const result = await searchCategories(activeMarketplace.value, categoryQuery.value)
      categoryResults.value = result.results
      categoryCacheStatus.value = result.cacheStatus
      addLog(`类目搜索完成：${result.results.length} 条。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '类目搜索失败')
    } finally {
      loading.value = false
    }
  }

  async function suggestCategoryByAi() {
    if (!hasProductForPublish(product.value)) {
      setError('请先从商品库选择要建议类目的商品。')
      return
    }
    loading.value = true
    setError('')
    try {
      const result = await suggestCategories(product.value, activeMarketplace.value)
      categoryResults.value = result.results
      categoryCacheStatus.value = result.cacheStatus
      categoryQuery.value = result.terms.slice(0, 6).join(' / ')
      addLog(`AI 类目建议完成：${result.results.length} 条候选。`)
      if (!result.results.length) setError('没有找到合适类目，请先刷新官方类目缓存，或换关键词搜索。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'AI 建议类目失败')
    } finally {
      loading.value = false
    }
  }

  async function selectCategory(item: CategorySearchResult) {
    product.value.drafts[activeMarketplace.value].categoryId = item.id
    product.value.drafts[activeMarketplace.value].categoryPath = item.path || item.name
    await loadCategoryAttributes()
  }

  async function loadCategoryAttributes() {
    const categoryId = product.value.drafts[activeMarketplace.value].categoryId.trim()
    if (!categoryId) {
      setError('请先填写或选择类目 ID。')
      return
    }
    loading.value = true
    setError('')
    try {
      category.value = await fetchCategoryAttrs(activeMarketplace.value, categoryId)
      currentStage.value = 6
      addLog(`已读取类目属性：${categoryId}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '读取类目属性失败')
    } finally {
      loading.value = false
    }
  }

  async function fillAttributesByAi() {
    const categoryId = product.value.drafts[activeMarketplace.value].categoryId.trim()
    if (!categoryId) {
      setError('请先选择类目。')
      return
    }
    loading.value = true
    setError('')
    try {
      if (!category.value || category.value.categoryId !== categoryId || category.value.platform !== activeMarketplace.value) {
        category.value = await fetchCategoryAttrs(activeMarketplace.value, categoryId)
      }
      const before = { ...product.value.drafts[activeMarketplace.value].attributes }
      const result = await fillCategoryAttributes(product.value, activeMarketplace.value, categoryId, category.value)
      product.value = result.product
      const after = product.value.drafts[activeMarketplace.value].attributes
      const filledCount = Object.keys(after).filter((key) => String(after[key] || '').trim() && String(before[key] || '').trim() !== String(after[key] || '').trim()).length
      addLog(`属性已填充：新增/更新 ${filledCount} 项，需要复核 ${result.needReview.length} 项。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'AI 填充属性失败')
    } finally {
      loading.value = false
    }
  }

  async function runCategoryOnlyPrecheck() {
    if (!hasProductForPublish(product.value)) {
      setError('请先从商品库选择要预检的商品。')
      return
    }
    const categoryId = product.value.drafts[activeMarketplace.value].categoryId.trim()
    if (!categoryId) {
      setError('请先选择或填写类目 ID。')
      return
    }
    loading.value = true
    setError('')
    try {
      categoryPrecheck.value = await runCategoryPrecheck(product.value, activeMarketplace.value, categoryId)
      addLog(categoryPrecheck.value.ok ? '类目预检通过。' : `类目预检发现缺项：${categoryPrecheck.value.missingFields.join('、') || categoryPrecheck.value.errors.join('、')}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '类目预检失败')
    } finally {
      loading.value = false
    }
  }

  async function refreshCategories() {
    loading.value = true
    setError('')
    try {
      let job = await startCategoryCacheRefresh(activeMarketplace.value)
      categoryCacheStatus.value = job
      addLog(`类目缓存刷新已启动：${job.job_id || job.jobId || ''}`)
      const jobId = String(job.job_id || job.jobId || '')
      for (let attempt = 0; jobId && attempt < 900; attempt += 1) {
        if (['completed', 'failed'].includes(String(job.status || ''))) break
        await wait(1000)
        job = await fetchCategoryCacheRefreshJob(jobId)
        categoryCacheStatus.value = job
      }
      if (String(job.status || '') === 'failed') {
        throw new Error(String(job.error || '类目缓存刷新失败'))
      }
      const result = job.result && typeof job.result === 'object' ? job.result as Record<string, unknown> : {}
      const cacheStatus = result.cache_status && typeof result.cache_status === 'object' ? result.cache_status as Record<string, unknown> : {}
      const imported = job.imported ?? result.imported
      const records = job.records ?? cacheStatus.records
      const site = job.site ? ` / ${job.site}` : ''
      addLog(`类目缓存刷新完成${site}：本次 ${imported ?? 0} 条，本地 ${records ?? '-'} 条。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '刷新类目缓存失败')
    } finally {
      loading.value = false
    }
  }

  async function runPrecheck() {
    if (!hasProductForPublish(product.value)) {
      setError('请先从商品库选择要预检的商品。')
      return
    }
    loading.value = true
    setError('')
    try {
      const result = await publishPrecheck(product.value, [activeMarketplace.value])
      product.value = result.product
      precheck.value = result.precheck
      precheckResults.value = result.platformResults
      applyMutationIndexes(result)
      if (result.precheck.ok) currentStage.value = 7
      addLog(result.precheck.ok ? '预检通过，商品可进入发布队列。' : `预检未通过：${result.precheck.errors.join('、')}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '上架预检失败')
    } finally {
      loading.value = false
    }
  }

  async function previewPayload() {
    if (!hasProductForPublish(product.value)) {
      setError('请先从商品库选择要预检的商品。')
      return
    }
    loading.value = true
    setError('')
    try {
      const result = await previewPublishPayload(product.value, activeMarketplace.value)
      payloadPreview.value = result.payload
      addLog(`Payload 已生成：${result.path || result.status}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '生成 Payload 失败')
    } finally {
      loading.value = false
    }
  }

  async function enqueuePublish(targetProduct: Product = product.value, targetPlatforms: Marketplace[] = [activeMarketplace.value]) {
    if (!hasProductForPublish(targetProduct)) {
      setError('请先从商品库选择要发布的商品。')
      return
    }
    loading.value = true
    setError('')
    try {
      publishJob.value = await enqueuePublishApi(targetProduct, targetPlatforms)
      draftsIndex.value = await fetchDraftsIndex()
      currentStage.value = 8
      addLog(`发布任务已入队：${publishJob.value.jobId}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '发布入队失败')
    } finally {
      loading.value = false
    }
  }

  async function publishDirect() {
    loading.value = true
    setError('')
    try {
      const result = await publishProductDirect(product.value, activeMarketplace.value)
      publishResult.value = result.raw
      if (result.product) product.value = result.product
      applyMutationIndexes(result)
      draftsIndex.value = result.draftsIndex?.length ? result.draftsIndex : await fetchDraftsIndex()
      publishLogs.value = await fetchPublishLogs()
      addLog(`直接发布返回：${result.status || (result.ok ? 'success' : 'failed')} ${result.message || result.error || ''}`)
      if (!result.ok && result.error) setError(result.error)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '直接发布失败')
    } finally {
      loading.value = false
    }
  }

  async function confirmRealPublish() {
    loading.value = true
    setError('')
    try {
      const result = await confirmMercadoLibreRealPublish(product.value, true)
      publishResult.value = result.raw
      if (result.product) product.value = result.product
      applyMutationIndexes(result)
      draftsIndex.value = result.draftsIndex?.length ? result.draftsIndex : await fetchDraftsIndex()
      publishLogs.value = await fetchPublishLogs()
      addLog(`Mercado Libre 真实发布返回：${result.status || (result.ok ? 'success' : 'failed')} ${result.message || result.error || ''}`)
      if (!result.ok && result.error) setError(result.error)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Mercado Libre 真实发布失败')
    } finally {
      loading.value = false
    }
  }

  async function refreshPublishJob() {
    if (!publishJob.value?.jobId) return
    loading.value = true
    setError('')
    try {
      publishJobStatus.value = await fetchPublishJob(publishJob.value.jobId)
      addLog(`发布任务状态已刷新：${publishJob.value.jobId}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '刷新发布任务失败')
    } finally {
      loading.value = false
    }
  }

  async function refreshPublishLogs() {
    loading.value = true
    setError('')
    try {
      publishLogs.value = await fetchPublishLogs()
      addLog(`发布日志已刷新：${publishLogs.value.length} 条。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '刷新发布日志失败')
    } finally {
      loading.value = false
    }
  }

  async function refreshMercadoLibreRemoteItems(status: string = mercadoLibreRemoteStatus.value, page?: number, perPage?: number) {
    loading.value = true
    setError('')
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
      addLog(`Mercado Libre 远程商品已刷新：第 ${mercadoLibreRemotePage.value}/${mercadoLibreRemoteTotalPages.value} 页，当前 ${mercadoLibreRemoteItems.value.length} 条，共 ${mercadoLibreRemoteTotal.value} 条。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '读取 Mercado Libre 已发布商品失败')
    } finally {
      loading.value = false
    }
  }

  async function refreshMercadoLibreOrders() {
    loading.value = true
    setError('')
    try {
      const result = await fetchMercadoLibreOrders(10, 0)
      mercadoLibreOrders.value = result.items
      mercadoLibreOrderNotifications.value = result.notifications
      mercadoLibreOrdersTotal.value = result.total
      mercadoLibreOrdersCheckedAt.value = result.checkedAt
      addLog(`Mercado Libre 订单已刷新：${result.items.length} 条，通知 ${result.notifications.length} 条。`)
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : '读取 Mercado Libre 订单失败'
      addLog(`Mercado Libre 订单暂不可用：${message}`)
    } finally {
      loading.value = false
    }
  }

  async function closeMercadoLibreRemoteItem(itemId: string) {
    loading.value = true
    setError('')
    try {
      const result = await closeMercadoLibrePublishedItem(itemId)
      addLog(String(result.message || `${itemId} 已下架。`))
      await refreshMercadoLibreRemoteItems(mercadoLibreRemoteStatus.value, mercadoLibreRemotePage.value, mercadoLibreRemotePerPage.value)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '下架 Mercado Libre 商品失败')
    } finally {
      loading.value = false
    }
  }

  async function loadAiConfig() {
    loading.value = true
    setError('')
    try {
      const result = await fetchAiConfig()
      aiConfig.value = result.raw
      addLog('AI 配置已读取。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '读取 AI 配置失败')
    } finally {
      loading.value = false
    }
  }

  async function saveAiSettings(config: UnknownRecord) {
    loading.value = true
    setError('')
    try {
      const result = await saveAiConfig(config)
      aiConfig.value = mergeAiConfigWithSubmitted(result.raw, config)
      appConfig.value = mergeAiConfigWithSubmitted(appConfig.value, config)
      addLog('平台授权设置已保存。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '保存平台授权设置失败')
    } finally {
      loading.value = false
    }
  }

  async function testAiSettings(channel: 'text' | 'image', config: UnknownRecord) {
    loading.value = true
    setError('')
    try {
      lastAuthResult.value = await testAiChannel(channel, config)
      addLog(`${channel} AI 测试：${lastAuthResult.value.message || lastAuthResult.value.error || '完成'}`)
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : '测试 AI 失败'
      lastAuthResult.value = {
        ok: false,
        message: '',
        error: message,
        errorCode: '',
        nextAction: '请检查 API Key、Base URL 和模型名，然后再试一次。',
        raw: { ok: false, error: message, channel },
      }
      setError(message)
    } finally {
      loading.value = false
    }
  }

  async function saveStoreConfig(config: UnknownRecord) {
    loading.value = true
    setError('')
    try {
      storeAuthSummary.value = await saveStoreSettings(config)
      storeConfig.value = { ...storeConfig.value, ...config }
      mercadolibreAuthChecklist.value = await fetchMercadoLibreAuthChecklist()
      addLog('平台授权配置已保存。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '保存平台授权失败')
    } finally {
      loading.value = false
    }
  }

  async function testAuth(platform: Marketplace, scope = '') {
    loading.value = true
    setError('')
    try {
      lastAuthResult.value = await testStoreAuth(platform, scope)
      if (!lastAuthResult.value.ok) throw new Error(authResultError(lastAuthResult.value, '测试授权失败'))
      addLog(`${platform} 授权测试：${lastAuthResult.value.message || lastAuthResult.value.error || '完成'}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '测试授权失败')
    } finally {
      loading.value = false
    }
  }

  async function loadMercadoLibreChecklist() {
    loading.value = true
    setError('')
    try {
      mercadolibreAuthChecklist.value = await fetchMercadoLibreAuthChecklist()
      addLog('Mercado Libre 授权检查清单已刷新。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '读取 Mercado Libre 授权清单失败')
    } finally {
      loading.value = false
    }
  }

  async function generateMercadoLibreAuthLink(appId: string, redirectUri: string) {
    loading.value = true
    setError('')
    try {
      authLink.value = await buildMercadoLibreAuthLink(appId, redirectUri)
      mercadolibreAuthChecklist.value = await fetchMercadoLibreAuthChecklist()
      addLog('Mercado Libre 授权链接已生成。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '生成 Mercado Libre 授权链接失败')
    } finally {
      loading.value = false
    }
  }

  async function openMercadoLibreAuth(url: string, browser = 'default') {
    loading.value = true
    setError('')
    try {
      lastAuthResult.value = await openAuthLink(url, browser)
      if (!lastAuthResult.value.ok) throw new Error(authResultError(lastAuthResult.value, '打开授权链接失败'))
      addLog(lastAuthResult.value.message || '已打开授权链接。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '打开授权链接失败')
    } finally {
      loading.value = false
    }
  }

  async function refreshMercadoLibreAuthToken(params: UnknownRecord = {}) {
    loading.value = true
    setError('')
    try {
      lastAuthResult.value = await refreshMercadoLibreToken(params)
      const summary = asStoreAuthSummary(lastAuthResult.value.raw)
      if (summary) storeAuthSummary.value = summary
      mercadolibreAuthChecklist.value = await fetchMercadoLibreAuthChecklist()
      if (!lastAuthResult.value.ok) throw new Error(authResultError(lastAuthResult.value, '刷新 Mercado Libre token 失败'))
      addLog(`Mercado Libre token 刷新：${lastAuthResult.value.message || lastAuthResult.value.error || '完成'}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '刷新 Mercado Libre token 失败')
    } finally {
      loading.value = false
    }
  }

  async function runMercadoLibreAuthTest(mode: MercadoLibreTestMode, categoryId = '') {
    loading.value = true
    setError('')
    try {
      lastAuthResult.value = await runMercadoLibreRealAuthTest(product.value, mode, categoryId)
      const summary = asStoreAuthSummary(lastAuthResult.value.raw)
      if (summary) storeAuthSummary.value = summary
      mercadolibreAuthChecklist.value = await fetchMercadoLibreAuthChecklist()
      if (!lastAuthResult.value.ok) throw new Error(authResultError(lastAuthResult.value, 'Mercado Libre 真实接口测试失败'))
      addLog(`Mercado Libre 真实接口测试 ${mode}：${lastAuthResult.value.message || lastAuthResult.value.error || lastAuthResult.value.raw.status || '完成'}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Mercado Libre 真实接口测试失败')
    } finally {
      loading.value = false
    }
  }

  async function exchangeMlCode(codeOrUrl: string, params: UnknownRecord = {}) {
    loading.value = true
    setError('')
    try {
      lastAuthResult.value = await exchangeMercadoLibreCode(codeOrUrl, params)
      mercadolibreAuthChecklist.value = await fetchMercadoLibreAuthChecklist()
      if (!lastAuthResult.value.ok) throw new Error(authResultError(lastAuthResult.value, 'Mercado Libre 换 token 失败'))
      addLog(`Mercado Libre 换 token：${lastAuthResult.value.message || '完成'}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Mercado Libre 换 token 失败')
    } finally {
      loading.value = false
    }
  }

  async function clearPlatformAuth(platform: Marketplace) {
    loading.value = true
    setError('')
    try {
      storeAuthSummary.value = await clearStoreAuth(platform)
      storeConfig.value = { ...storeConfig.value, [platform]: {} }
      if (platform === 'mercadolibre') mercadolibreAuthChecklist.value = await fetchMercadoLibreAuthChecklist()
      addLog(`${platform} 授权已清除。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '清除授权失败')
    } finally {
      loading.value = false
    }
  }

  function setMarketplace(value: Marketplace) {
    if (marketplaces.includes(value)) {
      activeMarketplace.value = value
      restoreCategoryFromProduct()
      restorePrecheckFromProduct()
    }
  }

  return {
    product,
    collectForm,
    collectDiagnostics,
    collectBatchRows,
    browserDebugStatus,
    productsIndex,
    draftsIndex,
    selectedProductIds,
    selectedProducts,
    pricingInput,
    pricingResult,
    category,
    categoryQuery,
    categoryResults,
    categoryCacheStatus,
    categoryPrecheck,
    precheck,
    precheckResults,
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
    activeMarketplace,
    logs,
    appConfig,
    aiConfig,
    storeConfig,
    storeAuthSummary,
    mercadolibreAuthChecklist,
    lastAuthResult,
    authLink,
    publishResult,
    imagePrompt,
    currentStage,
    loading,
    error,
    draft,
    imagePool,
    selectedImages,
    workflowSteps,
    progressPercent,
    loadState,
    resetForm,
    collectProduct,
    collectBatch,
    collectFromBrowserTab,
    open1688Browser,
    checkBrowserDebugStatus,
    openDebugProfile,
    importManual,
    previewClean1688Text,
    clearCollectedProduct,
    saveCollectSettings,
    refreshProductsIndex,
    refreshDraftsIndex,
    loadProduct,
    loadDraft,
    deleteProduct,
    deleteSelectedProducts,
    toggleProductSelection,
    selectAllProducts,
    claimSelectedProducts,
    claimCurrentProduct,
    generateCopyForSelectedProducts,
    enqueueSelectedProducts,
    uploadReferenceImages,
    clearSourceImages,
    saveCurrentImagePool,
    setMainImage,
    deleteImages,
    syncGeneratedImagePool,
    saveCurrentProduct,
    assignUpc,
    generateCopy,
    generateImagePromptPack,
    translateImages,
    calculatePrice,
    searchCategory,
    suggestCategoryByAi,
    selectCategory,
    loadCategoryAttributes,
    fillAttributesByAi,
    runCategoryOnlyPrecheck,
    refreshCategories,
    runPrecheck,
    previewPayload,
    enqueuePublish,
    publishDirect,
    confirmRealPublish,
    refreshPublishJob,
    refreshPublishLogs,
    refreshMercadoLibreOrders,
    refreshMercadoLibreRemoteItems,
    closeMercadoLibreRemoteItem,
    loadAiConfig,
    saveAiSettings,
    testAiSettings,
    saveStoreConfig,
    testAuth,
    loadMercadoLibreChecklist,
    generateMercadoLibreAuthLink,
    openMercadoLibreAuth,
    refreshMercadoLibreAuthToken,
    runMercadoLibreAuthTest,
    exchangeMlCode,
    clearPlatformAuth,
    setMarketplace,
  }
})
