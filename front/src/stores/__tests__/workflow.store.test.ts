import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createEmptyDraftDetail, createEmptyDraftProductContext, createEmptyProduct } from '@/constants/initialState'
import { useWorkflowStore } from '@/stores/workflow'
import * as catalogApi from '@/api/workflow/catalog'
import type { AppStateResponse, DraftMutationResponse, ProductMutationResponse } from '@/api/workflow/normalizers'
import * as publishingApi from '@/api/workflow/publishing'
import * as settingsApi from '@/api/workflow/settings'
import * as stateApi from '@/api/workflow/state'
import * as translationApi from '@/api/workflow/translation'
import type { DraftDetail, DraftIndexItem, PricingResult, Product } from '@/types/workflow'

vi.mock('@/api/workflow/state', () => ({
  fetchState: vi.fn(),
}))

vi.mock('@/api/workflow/catalog', () => ({
  saveProduct: vi.fn(),
  collectProduct: vi.fn(),
  importManualProduct: vi.fn(),
  saveCollectSettings: vi.fn(),
  uploadImages: vi.fn(),
  generateCopy: vi.fn(),
  imageEdit: vi.fn(),
  imageTranslate: vi.fn(),
  openBrowserProfile: vi.fn(),
  open1688Browser: vi.fn(),
  loadProduct: vi.fn(),
  generateImagePrompts: vi.fn(),
  fetchProductsIndex: vi.fn(),
  fetchDraftsIndex: vi.fn(),
  fetchBrowserDebugStatus: vi.fn(),
  deleteProducts: vi.fn(),
  clean1688Text: vi.fn(),
  saveImagePool: vi.fn(),
  imagePoolAction: vi.fn(),
  generateCopyBatch: vi.fn(),
  collectFromBrowserTab: vi.fn(),
  collectBatch: vi.fn(),
  claimProducts: vi.fn(),
  loadDraft: vi.fn(),
  saveDraft: vi.fn(),
  deleteDraft: vi.fn(),
}))

vi.mock('@/api/workflow/publishing', () => ({
  calculatePrice: vi.fn(),
  publishPrecheck: vi.fn(),
  enqueuePublish: vi.fn(),
  fetchCategoryAttrs: vi.fn(),
  matchCategory: vi.fn(),
  searchCategories: vi.fn(),
  previewPublishPayload: vi.fn(),
  fillCategoryAttributes: vi.fn(),
  fetchPublishLogs: vi.fn(),
  fetchPublishJob: vi.fn(),
  fetchMercadoLibreOrders: vi.fn(),
  fetchMercadoLibrePublishedItems: vi.fn(),
  closeMercadoLibrePublishedItem: vi.fn(),
  runCategoryPrecheck: vi.fn(),
  confirmMercadoLibreRealPublish: vi.fn(),
  publishProductDirect: vi.fn(),
}))

vi.mock('@/api/workflow/translation', () => ({
  translateText: vi.fn(),
}))

vi.mock('@/api/workflow/settings', () => ({
  assignUpc: vi.fn(),
  testStoreAuth: vi.fn(),
  testAiModel: vi.fn(),
  saveStoreSettings: vi.fn(),
  saveAiConfig: vi.fn(),
  openAuthLink: vi.fn(),
  fetchAiConfig: vi.fn(),
  fetchMercadoLibreAuthChecklist: vi.fn(),
  refreshMercadoLibreToken: vi.fn(),
  runMercadoLibreRealAuthTest: vi.fn(),
  buildMercadoLibreAuthLink: vi.fn(),
  exchangeMercadoLibreCode: vi.fn(),
  clearStoreAuth: vi.fn(),
  testApiConfig: vi.fn(),
}))

const workflowApi = {
  ...catalogApi,
  ...publishingApi,
  ...settingsApi,
  ...stateApi,
  ...translationApi,
}

function collectedProduct(): Product {
  const product = createEmptyProduct()
  product.productId = 'real-product-1'
  product.name = 'Collected product'
  product.source.title = 'Collected product'
  product.source.sourceUrl = 'https://detail.1688.com/offer/real.html'
  product.source.sourcePlatform = '1688'
  product.source.price = '30'
  product.source.currency = 'CNY'
  product.sellingPoints = ['Point A', 'Point B']
  product.source.imagePool = [
    {
      id: 'img_1',
      url: 'https://example.com/1.jpg',
      path: '',
      previewUrl: 'https://example.com/1.jpg',
      origin: '1688',
      usage: 'main',
      platforms: ['mercadolibre'],
      isMain: true,
      selected: true,
      status: 'ready',
      width: 1200,
      height: 1200,
    },
  ]
  return product
}

function mutation(product: Product): ProductMutationResponse {
  return { ok: true, product, imagePool: product.source.imagePool, productsIndex: [] }
}

function draftMutation(draft: DraftDetail, draftsIndex: DraftIndexItem[] = []): DraftMutationResponse {
  return {
    ok: true,
    draft,
    productContext: createEmptyDraftProductContext(),
    productsIndex: [],
    draftsIndex,
    raw: {},
  }
}

describe('workflow store live API flow', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: vi.fn(() => null),
        setItem: vi.fn(),
        removeItem: vi.fn(),
        clear: vi.fn(),
      },
    })
    vi.clearAllMocks()
  })

  it('loads backend state without seeded sample data', async () => {
    const product = createEmptyProduct()
    vi.mocked(workflowApi.fetchState).mockResolvedValue({
      schemaVersion: 1,
      product,
      imagePool: [],
      appConfig: {},
      storeConfig: {},
      storeAuthSummary: {},
      outputDir: '',
      platformOptions: [],
    } satisfies AppStateResponse)

    const store = useWorkflowStore()
    await store.loadState()

    expect(store.product.productId).toBe('')
    expect(store.collectDiagnostics.status).toBe('idle')
    expect(workflowApi.fetchState).toHaveBeenCalledOnce()
  })

  it('does not hydrate public credential fields into Pinia state', async () => {
    const product = createEmptyProduct()
    vi.mocked(workflowApi.fetchState).mockResolvedValue({
      schemaVersion: 1,
      product,
      imagePool: [],
      appConfig: {
        alibaba_cookie: 'cook...cret',
        '1688_api': {
          app_key: 'key...cret',
          app_secret: 'app...cret',
          access_token: 'toke...cret',
          masked_app_key: 'key...cret',
          masked_app_secret: 'app...cret',
          masked_access_token: 'toke...cret',
          status: '已配置',
        },
        yunexpress: {
          app_id: 'yun-...p-id',
          app_secret: 'yun-...cret',
          source_key: 'yun-...-key',
          masked_app_id: 'yun-...p-id',
          masked_app_secret: 'yun-...cret',
          masked_source_key: 'yun-...-key',
          status: '已配置',
        },
      },
      storeConfig: {},
      storeAuthSummary: {},
      outputDir: '',
      platformOptions: [],
    } satisfies AppStateResponse)

    const store = useWorkflowStore()
    await store.loadState()

    const api1688 = store.appConfig['1688_api'] as Record<string, unknown>
    const yunexpress = store.appConfig.yunexpress as Record<string, unknown>
    expect(store.appConfig.alibaba_cookie).toBe('')
    expect(api1688.app_key).toBe('')
    expect(api1688.app_secret).toBe('')
    expect(api1688.access_token).toBe('')
    expect(api1688.masked_app_secret).toBe('app...cret')
    expect(yunexpress.app_id).toBe('')
    expect(yunexpress.app_secret).toBe('')
    expect(yunexpress.source_key).toBe('')
    expect(yunexpress.masked_app_secret).toBe('yun-...cret')
    expect(store.collectForm).not.toHaveProperty('alibabaCookie')
    expect(store.collectForm).not.toHaveProperty('alibabaAppSecret')
  })

  it('does not retain submitted 1688 or YunExpress plaintext credentials', async () => {
    vi.mocked(workflowApi.saveAiConfig).mockResolvedValue({
      raw: {
        '1688_api': {
          app_key: 'subm...-key',
          app_secret: 'subm...cret',
          access_token: 'subm...oken',
          masked_app_key: 'subm...-key',
          masked_app_secret: 'subm...cret',
          masked_access_token: 'subm...oken',
          status: '已配置',
        },
        yunexpress: {
          app_id: 'subm...p-id',
          app_secret: 'subm...cret',
          source_key: 'subm...-key',
          masked_app_id: 'subm...p-id',
          masked_app_secret: 'subm...cret',
          masked_source_key: 'subm...-key',
          status: '已配置',
        },
      },
    })
    const submitted = {
      '1688_api': {
        app_key: 'submitted-1688-app-key',
        app_secret: 'submitted-1688-app-secret',
        access_token: 'submitted-1688-access-token',
      },
      yunexpress: {
        app_id: 'submitted-yun-app-id',
        app_secret: 'submitted-yun-app-secret',
        source_key: 'submitted-yun-source-key',
      },
    }

    const store = useWorkflowStore()
    await store.saveAiSettings(submitted)

    const serialized = JSON.stringify({
      appConfig: store.appConfig,
      aiConfig: store.aiConfig,
      collectForm: store.collectForm,
    })
    for (const secret of [
      'submitted-1688-app-key',
      'submitted-1688-app-secret',
      'submitted-1688-access-token',
      'submitted-yun-app-id',
      'submitted-yun-app-secret',
      'submitted-yun-source-key',
    ]) {
      expect(serialized).not.toContain(secret)
    }
    expect((store.appConfig['1688_api'] as Record<string, unknown>).app_secret).toBe('')
    expect((store.appConfig.yunexpress as Record<string, unknown>).source_key).toBe('')
  })

  it('keeps a newly saved API key configured when the provider model is still blank', async () => {
    vi.mocked(workflowApi.saveAiConfig).mockResolvedValue({
      raw: {
        ai_models: [{
          id: 'openai_text',
          connection_type: 'api',
          provider_id: 'openai',
          model: '',
          api_key_configured: true,
          api_key_masked: 'sk-t...-key',
        }],
      },
    })
    const submitted = {
      ai_models: [{
        id: 'openai_text',
        connection_type: 'api',
        provider_id: 'openai',
        model: '',
        api_key: 'sk-transient-api-key',
        api_key_configured: false,
      }],
    }

    const store = useWorkflowStore()
    await store.saveAiSettings(submitted)

    for (const config of [store.aiConfig, store.appConfig]) {
      const model = (config.ai_models as Array<Record<string, unknown>>)[0]
      expect(model.model).toBe('')
      expect(model.api_key_configured).toBe(true)
      expect(model.api_key).toBe('')
      expect(model.api_key_masked).toBe('sk-t...-key')
    }
    expect(JSON.stringify({ aiConfig: store.aiConfig, appConfig: store.appConfig })).not.toContain('sk-transient-api-key')
  })

  it('keeps submitted store credentials out of global state', async () => {
    vi.mocked(workflowApi.saveStoreSettings).mockResolvedValue({
      storeConfig: {
        mercadolibre: {
          app_id: 'public-app-id',
          app_secret: 'secr...alue',
        },
      },
      storeAuthSummary: {
        mercadolibre: { configured: true },
      },
    })
    vi.mocked(workflowApi.fetchMercadoLibreAuthChecklist).mockResolvedValue({
      platform: 'mercadolibre',
      readyForAuthLink: true,
      tokenReady: false,
      missingCodes: [],
      fields: [],
      nextAction: '',
      copyText: '',
      raw: {},
    })

    const store = useWorkflowStore()
    await store.saveStoreConfig({
      mercadolibre: {
        app_id: 'public-app-id',
        app_secret: 'plain-secret-value',
      },
    })

    expect(JSON.stringify(store.storeConfig)).not.toContain('plain-secret-value')
    expect(store.storeAuthSummary).toEqual({
      mercadolibre: { configured: true },
    })
  })

  it('hydrates dashboard domain data after the bootstrap state is split', async () => {
    vi.mocked(workflowApi.fetchProductsIndex).mockResolvedValue([])
    vi.mocked(workflowApi.fetchPublishLogs).mockResolvedValue([])
    vi.mocked(workflowApi.fetchMercadoLibreOrders).mockResolvedValue({
      items: [],
      notifications: [],
      total: 0,
      checkedAt: '',
    })
    vi.mocked(workflowApi.fetchMercadoLibrePublishedItems).mockResolvedValue({
      items: [],
      pagination: {
        page: 1,
        perPage: 50,
        offset: 0,
        total: 0,
        totalPages: 0,
        hasPrev: false,
        hasNext: false,
      },
    })

    const store = useWorkflowStore()
    store.mercadolibreAuthChecklist = {
      platform: 'mercadolibre',
      readyForAuthLink: true,
      tokenReady: true,
      missingCodes: [],
      fields: [],
      nextAction: '',
      copyText: '',
      raw: {},
    }
    await store.hydrateTab('dashboard')

    expect(workflowApi.fetchProductsIndex).toHaveBeenCalledOnce()
    expect(workflowApi.fetchPublishLogs).toHaveBeenCalledOnce()
    expect(workflowApi.fetchMercadoLibreOrders).toHaveBeenCalledOnce()
    expect(workflowApi.fetchMercadoLibrePublishedItems).toHaveBeenCalledOnce()
  })

  it('collects product through backend API and updates diagnostics', async () => {
    const product = collectedProduct()
    vi.mocked(workflowApi.collectProduct).mockResolvedValue(mutation(product))

    const store = useWorkflowStore()
    store.collectForm.productUrl = product.source.sourceUrl
    await store.collectProduct()

    expect(store.product.productId).toBe('real-product-1')
    expect(store.collectDiagnostics.status).toBe('success')
    expect(store.collectDiagnostics.downloadedImages).toBe(1)
    expect(store.progressPercent).toBeGreaterThan(0)
  })

  it('uses the active non-Mercado Libre draft when calculating workflow progress', () => {
    const draft = createEmptyDraftDetail('yandex')
    draft.draftId = 'draft-yandex'
    draft.platform = 'yandex'
    draft.platforms = ['yandex']
    draft.price = '7990'
    draft.categoryId = 'yandex-category-1'
    draft.status = 'published'

    const store = useWorkflowStore()
    store.activeMarketplace = 'yandex'
    store.currentDraft = draft

    const statuses = Object.fromEntries(store.workflowSteps.map((step) => [step.key, step.status]))
    expect(statuses.pricing).toBe('done')
    expect(statuses.category).toBe('done')
    expect(statuses.precheck).toBe('done')
    expect(statuses.publish).toBe('done')
  })

  it('does not reuse completed Mercado Libre progress after switching to a pending Yandex draft', () => {
    const product = createEmptyProduct()
    product.productId = 'product-1'
    product.name = 'Cross-platform product'
    product.drafts.mercadolibre.draftId = 'draft-ml'
    product.drafts.mercadolibre.title = 'Mercado Libre copy'
    product.drafts.mercadolibre.description = 'Completed Mercado Libre description'
    product.drafts.mercadolibre.images = [{ assetId: 'ml-image', role: 'main', order: 0 }]
    product.drafts.mercadolibre.status = 'published'

    const yandexDraft = createEmptyDraftDetail('yandex')
    yandexDraft.draftId = 'draft-yandex'
    yandexDraft.site = 'global'
    yandexDraft.status = 'pending'
    product.drafts.yandex.draftId = yandexDraft.draftId

    const store = useWorkflowStore()
    store.product = product
    store.currentDraft = yandexDraft
    store.publishJob = {
      jobId: 'job-ml',
      status: 'completed',
      platforms: ['mercadolibre'],
      createdAt: '2026-07-29T00:00:00Z',
      draftId: 'draft-ml',
      targetKey: 'mercadolibre:cbt',
    }

    const mercadoLibreStatuses = Object.fromEntries(store.workflowSteps.map((step) => [step.key, step.status]))
    expect(mercadoLibreStatuses.copy).toBe('done')
    expect(mercadoLibreStatuses.images).toBe('done')
    expect(mercadoLibreStatuses.publish).toBe('done')

    store.setMarketplace('yandex')

    expect(store.publishJob).toBeNull()
    const yandexStatuses = Object.fromEntries(store.workflowSteps.map((step) => [step.key, step.status]))
    expect(yandexStatuses.copy).not.toBe('done')
    expect(yandexStatuses.images).not.toBe('done')
    expect(yandexStatuses.publish).not.toBe('done')

    store.publishJob = {
      jobId: 'stale-ml-job',
      status: 'completed',
      platforms: ['mercadolibre'],
      createdAt: '2026-07-29T00:00:00Z',
      draftId: 'draft-ml',
      targetKey: 'mercadolibre:cbt',
    }
    expect(store.workflowSteps.find((step) => step.key === 'publish')?.status).not.toBe('done')

    store.publishJob = {
      jobId: 'wrong-yandex-draft',
      status: 'completed',
      platforms: ['yandex'],
      createdAt: '2026-07-29T00:00:00Z',
      draftId: 'another-yandex-draft',
      targetKey: 'yandex:global',
    }
    expect(store.workflowSteps.find((step) => step.key === 'publish')?.status).not.toBe('done')

    store.publishJob = {
      jobId: 'wrong-yandex-target',
      status: 'completed',
      platforms: ['yandex'],
      createdAt: '2026-07-29T00:00:00Z',
      draftId: 'draft-yandex',
      targetKey: 'yandex:another-site',
    }
    expect(store.workflowSteps.find((step) => step.key === 'publish')?.status).not.toBe('done')
  })

  it('stores precheck result returned by backend', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-1'
    draft.productId = 'real-product-1'
    draft.sourceProductId = 'real-product-1'
    draft.site = 'CBT'
    draft.targetSites = [{ platform: 'mercadolibre', site: 'CBT', language: 'es', currency: 'USD' }]
    draft.status = 'ready_to_publish'
    vi.mocked(workflowApi.saveDraft).mockResolvedValue(draftMutation(draft))
    vi.mocked(workflowApi.publishPrecheck).mockResolvedValue({
      draft,
      precheck: { ok: true, errors: [], warnings: [], errorItems: [], warningItems: [], checkedAt: '2026-06-02T00:00:00Z' },
      platformResults: {},
      productContext: createEmptyDraftProductContext(),
    })

    const store = useWorkflowStore()
    store.currentDraft = draft
    await store.runPrecheck()

    expect(store.precheck?.ok).toBe(true)
    expect(store.currentDraft.status).toBe('ready_to_publish')
    expect(workflowApi.publishPrecheck).toHaveBeenCalledWith(
      expect.objectContaining({ draftId: 'draft-1', productId: 'real-product-1' }),
      expect.objectContaining({ platform: 'mercadolibre', site: 'CBT', categoryId: '', attributes: {} }),
    )
  })

  it('uses category.match as the only automatic matching path and still requires manual selection', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-pr3'
    draft.productId = 'real-product-1'
    draft.sourceProductId = 'real-product-1'
    draft.site = 'MLM'
    draft.title = 'Ventilador portátil'
    draft.targetSites = [
      { platform: 'mercadolibre', site: 'MLM', language: 'es-MX', currency: 'MXN' },
    ]
    vi.mocked(workflowApi.saveDraft).mockResolvedValue(draftMutation(draft))
    vi.mocked(workflowApi.matchCategory).mockResolvedValue({
      ok: true,
      status: 'completed',
      selectedCategoryId: 'MLM-FAN',
      candidates: [{
        id: 'MLM-FAN',
        name: 'Ventiladores',
        path: 'Hogar / Ventiladores',
        raw: { category_id: 'MLM-FAN' },
      }],
      query: 'ventilador',
      decision: {
        confidenceBand: 'high',
        modelConfidence: 0.95,
        decisionScore: 0.88,
        abstained: false,
        evidence: ['主体一致'],
        searchCount: 1,
      },
      failure: null,
      trace: { conversationId: 'aic-1', taskRunId: 'task-1' },
    })

    const store = useWorkflowStore()
    store.currentDraft = draft
    await store.autoSuggestCategoriesForDraft()

    expect(workflowApi.matchCategory).toHaveBeenCalledOnce()
    expect(workflowApi.searchCategories).not.toHaveBeenCalled()
    expect(store.categoryResults[0]?.id).toBe('MLM-FAN')
    expect(store.currentDraft.categoryId).toBe('')
  })

  it('translates category candidates through the generic flat text contract', async () => {
    vi.mocked(workflowApi.translateText).mockResolvedValue({
      'category.0.path': '家居 / 风扇',
    })
    const store = useWorkflowStore()
    store.categoryResults = [{
      id: 'MLM-FAN',
      name: 'Ventiladores',
      path: 'Hogar / Ventiladores',
      raw: {},
    }]

    await store.translateCategoryResults()

    expect(workflowApi.translateText).toHaveBeenCalledWith('zh-CN', {
      'category.0.path': 'Hogar / Ventiladores',
    })
    expect(store.categoryResultTranslations).toEqual({
      'MLM-FAN': '家居 / 风扇',
    })
  })

  it('translates loaded platform attributes without reloading the category', async () => {
    vi.mocked(workflowApi.translateText).mockResolvedValue({
      'attribute.0.label': '品牌',
      'attribute.0.description': '填写商品品牌',
      'attribute.0.option.0': '通用品牌',
    })
    const store = useWorkflowStore()
    store.currentDraft.categoryId = 'MLM-FAN'
    store.category = {
      platform: 'mercadolibre',
      categoryId: 'MLM-FAN',
      categoryPath: 'Hogar / Ventiladores',
      requiredAttributes: [{
        id: 'BRAND',
        name: 'Marca',
        description: 'Indica la marca del producto',
        required: true,
        options: ['Generic'],
      }],
      optionalAttributes: [],
      raw: {},
    }

    await store.translateCategoryAttributes()

    expect(workflowApi.translateText).toHaveBeenCalledWith('zh-CN', {
      'attribute.0.label': 'Marca',
      'attribute.0.description': 'Indica la marca del producto',
      'attribute.0.option.0': 'Generic',
    })
    expect(workflowApi.fetchCategoryAttrs).not.toHaveBeenCalled()
    expect(store.categoryAttributeTranslations).toEqual({
      BRAND: {
        label: '品牌',
        help: '填写商品品牌',
        values: { Generic: '通用品牌' },
      },
    })
  })

  it('saves the selected category to the active target before loading attributes', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-1'
    draft.productId = 'real-product-1'
    draft.sourceProductId = 'real-product-1'
    draft.site = 'MLM'
    draft.categoryId = 'MLM-NEW'
    draft.categoryPath = '家居 / 新类目'
    draft.attributes = { OLD_ATTRIBUTE: '旧值' }
    draft.validationErrors = ['旧类目校验错误']
    draft.status = 'ready_to_publish'
    draft.publishStatus = 'ready'
    draft.targetSites = [
      {
        platform: 'mercadolibre',
        site: 'MLM',
        language: 'es-MX',
        currency: 'MXN',
        categoryId: 'MLM-OLD',
        categoryPath: '旧类目',
        attributes: { OLD_ATTRIBUTE: '旧值' },
        categoryPrecheck: { ok: true },
      },
      {
        platform: 'mercadolibre',
        site: 'CBT',
        language: 'en-US',
        currency: 'USD',
        categoryId: 'CBT-UNCHANGED',
        categoryPath: 'Unchanged',
        attributes: { BRAND: 'Keep' },
      },
    ]
    const savedDrafts: DraftDetail[] = []
    vi.mocked(workflowApi.saveDraft).mockImplementation(async (draftToSave) => {
      const saved = JSON.parse(JSON.stringify(draftToSave)) as DraftDetail
      savedDrafts.push(saved)
      return draftMutation(JSON.parse(JSON.stringify(saved)) as DraftDetail)
    })
    vi.mocked(workflowApi.fetchCategoryAttrs).mockResolvedValue({
      platform: 'mercadolibre',
      categoryId: 'MLM-NEW',
      categoryPath: '家居 / 新类目',
      requiredAttributes: [{ id: 'BRAND', name: 'Brand', required: true, options: [] }],
      optionalAttributes: [],
      source: 'mercadolibre_live',
      fetchedAt: '2026-07-25T12:00:00Z',
      raw: {},
    })

    const store = useWorkflowStore()
    store.currentDraft = draft
    await store.selectCategory({
      id: 'MLM-NEW',
      name: '新类目',
      path: '家居 / 新类目',
      raw: {},
    })

    expect(savedDrafts).toHaveLength(2)
    expect(savedDrafts[0]).toEqual(expect.objectContaining({
      categoryId: 'MLM-NEW',
      categoryPath: '家居 / 新类目',
      attributes: {},
      validationErrors: [],
      status: 'category_ready',
      publishStatus: '',
    }))
    expect(savedDrafts[0].targetSites[0]).toEqual(expect.objectContaining({
      site: 'MLM',
      categoryId: 'MLM-NEW',
      categoryPath: '家居 / 新类目',
      categoryAttributeSchema: null,
      attributes: {},
      categoryPrecheck: {},
    }))
    expect(savedDrafts[0].targetSites[1]).toEqual(expect.objectContaining({
      site: 'CBT',
      categoryId: 'CBT-UNCHANGED',
      categoryPath: 'Unchanged',
      attributes: { BRAND: 'Keep' },
    }))
    expect(vi.mocked(workflowApi.saveDraft).mock.invocationCallOrder[0])
      .toBeLessThan(vi.mocked(workflowApi.fetchCategoryAttrs).mock.invocationCallOrder[0])
    expect(vi.mocked(workflowApi.fetchCategoryAttrs).mock.invocationCallOrder[0])
      .toBeLessThan(vi.mocked(workflowApi.saveDraft).mock.invocationCallOrder[1])
    expect(workflowApi.fetchCategoryAttrs).toHaveBeenCalledWith('mercadolibre', 'MLM-NEW', 'MLM', {})
    expect(savedDrafts[1].targetSites[0]?.categoryAttributeSchema).toEqual(expect.objectContaining({
      version: 1,
      platform: 'mercadolibre',
      site: 'MLM',
      categoryId: 'MLM-NEW',
      categoryPath: '家居 / 新类目',
      source: 'mercadolibre_live',
      fetchedAt: '2026-07-25T12:00:00Z',
      required: [expect.objectContaining({ id: 'BRAND', name: 'Brand', required: true })],
      optional: [],
    }))
    expect(store.category?.requiredAttributes[0]?.id).toBe('BRAND')
    expect(store.loading).toBe(false)
    expect(store.categoryAttributeLoading).toBe(false)
    expect(store.categoryAttributeError).toBe('')

    store.selectPublishTarget(store.currentDraft.targetSites[1])
    expect(store.category).toBeNull()
    store.selectPublishTarget(store.currentDraft.targetSites[0])
    expect(store.category?.requiredAttributes[0]?.id).toBe('BRAND')
  })

  it('keeps a selected category saved when loading its attributes fails', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-1'
    draft.productId = 'real-product-1'
    draft.sourceProductId = 'real-product-1'
    draft.site = 'MLM'
    draft.targetSites = [{ platform: 'mercadolibre', site: 'MLM', language: 'es-MX', currency: 'MXN' }]
    vi.mocked(workflowApi.saveDraft).mockImplementation(async (draftToSave) => draftMutation(draftToSave))
    vi.mocked(workflowApi.fetchCategoryAttrs).mockRejectedValue(new Error('平台类目属性接口超时'))

    const store = useWorkflowStore()
    store.currentDraft = draft
    await store.selectCategory({
      id: 'MLM-NEW',
      name: '新类目',
      path: '家居 / 新类目',
      raw: {},
    })

    expect(workflowApi.saveDraft).toHaveBeenCalledOnce()
    expect(store.currentDraft.categoryId).toBe('MLM-NEW')
    expect(store.currentDraft.targetSites[0]?.categoryId).toBe('MLM-NEW')
    expect(store.error).toBe('平台类目属性接口超时')
    expect(store.categoryAttributeLoading).toBe(false)
    expect(store.categoryAttributeError).toBe('平台类目属性接口超时')
  })

  it('restores category attributes from the saved target schema when reopening a draft', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-1'
    draft.productId = 'real-product-1'
    draft.sourceProductId = 'real-product-1'
    draft.site = 'MLM'
    draft.categoryId = 'MLM-NEW'
    draft.categoryPath = '家居 / 新类目'
    draft.targetSites = [{
      platform: 'mercadolibre',
      site: 'MLM',
      language: 'es-MX',
      currency: 'MXN',
      categoryId: 'MLM-NEW',
      categoryPath: '家居 / 新类目',
      categoryAttributeSchema: {
        version: 1,
        platform: 'mercadolibre',
        site: 'MLM',
        categoryId: 'MLM-NEW',
        categoryPath: '家居 / 新类目',
        source: 'mercadolibre_live',
        fetchedAt: '2026-07-25T12:00:00Z',
        required: [{ id: 'BRAND', name: 'Brand', required: true, options: [] }],
        optional: [],
      },
      attributes: {},
    }]
    vi.mocked(workflowApi.loadDraft).mockResolvedValue(draftMutation(draft))
    const item: DraftIndexItem = {
      draftId: 'draft-1',
      productId: 'real-product-1',
      sourceProductId: 'real-product-1',
      platform: 'mercadolibre',
      platforms: ['mercadolibre'],
      targetSites: draft.targetSites,
      site: 'MLM',
      language: 'es-MX',
      status: 'category_ready',
      title: '测试草稿',
      productTitle: '测试商品',
      mainImage: '',
      sourcePlatform: '1688',
      sourceUrl: '',
      categoryId: 'MLM-NEW',
      categoryPath: '家居 / 新类目',
      price: '',
      publishStatus: '',
      createdAt: '',
      updatedAt: '',
      productFilePath: '',
      raw: {},
    }

    const store = useWorkflowStore()
    await store.loadDraft(item)

    expect(store.category?.categoryId).toBe('MLM-NEW')
    expect(store.category?.requiredAttributes).toEqual([
      expect.objectContaining({ id: 'BRAND', name: 'Brand', required: true }),
    ])
    expect(workflowApi.fetchCategoryAttrs).not.toHaveBeenCalled()
  })

  it('surfaces Mercado Libre refresh token failures instead of logging them as complete', async () => {
    vi.mocked(workflowApi.refreshMercadoLibreToken).mockResolvedValue({
      ok: false,
      message: '失败',
      error: '请先填写 App ID、App Secret 和 Refresh Token。',
      errorCode: '',
      nextAction: '先用 code 换 token，或检查已保存的 refresh token。',
      raw: { ok: false, platform: 'mercadolibre' },
    })
    vi.mocked(workflowApi.fetchMercadoLibreAuthChecklist).mockResolvedValue({
      platform: 'mercadolibre',
      readyForAuthLink: true,
      tokenReady: false,
      missingCodes: [],
      fields: [],
      nextAction: '生成授权链接，用 code 换 token。',
      copyText: '',
      raw: {},
    })

    const store = useWorkflowStore()
    await store.refreshMercadoLibreAuthToken()

    expect(store.lastAuthResult?.ok).toBe(false)
    expect(store.error).toBe('请先填写 App ID、App Secret 和 Refresh Token。')
    expect(workflowApi.fetchMercadoLibreAuthChecklist).toHaveBeenCalledOnce()
  })

  it('copies selected products to the draft box for the active platform', async () => {
    vi.mocked(workflowApi.claimProducts).mockResolvedValue({ ok: true })
    vi.mocked(workflowApi.fetchProductsIndex).mockResolvedValue([])
    vi.mocked(workflowApi.fetchDraftsIndex).mockResolvedValue([])

    const store = useWorkflowStore()
    store.selectedProductIds = ['product-1', 'product-2']
    await store.claimSelectedProducts()

    expect(workflowApi.claimProducts).toHaveBeenCalledWith(['product-1', 'product-2'], 'mercadolibre')
  })

  it('pushes the current product to the draft box for the active platform', async () => {
    const product = collectedProduct()
    vi.mocked(workflowApi.claimProducts).mockResolvedValue({ ok: true })
    vi.mocked(workflowApi.loadProduct).mockResolvedValue(mutation(product))
    vi.mocked(workflowApi.fetchProductsIndex).mockResolvedValue([])
    vi.mocked(workflowApi.fetchDraftsIndex).mockResolvedValue([])

    const store = useWorkflowStore()
    store.product = product
    store.setMarketplace('yandex')
    await store.claimCurrentProduct()

    expect(workflowApi.claimProducts).toHaveBeenCalledWith(['real-product-1'], 'yandex')
  })

  it('updates one draft to a selected secondary site', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-1'
    draft.productId = 'product-1'
    draft.sourceProductId = 'product-1'
    draft.title = 'Draft title'
    draft.platform = 'yandex'
    draft.platforms = ['yandex']
    draft.site = 'global'
    draft.language = 'ru-RU'
    draft.currency = 'RUB'
    draft.targetSites = [{ platform: 'yandex', site: 'global', language: 'ru-RU', currency: 'RUB' }]
    const item: DraftIndexItem = {
      draftId: 'draft-1',
      productId: 'product-1',
      sourceProductId: 'product-1',
      platform: 'yandex',
      platforms: ['yandex'],
      targetSites: [{ platform: 'yandex', site: 'global', language: 'ru-RU', currency: 'RUB' }],
      site: 'global',
      language: 'ru-RU',
      status: 'claimed',
      title: 'Draft title',
      productTitle: 'Source title',
      mainImage: '',
      sourcePlatform: '1688',
      sourceUrl: 'https://example.com/source',
      categoryId: '',
      categoryPath: '',
      price: '',
      publishStatus: '',
      createdAt: '',
      updatedAt: '',
      productFilePath: '',
      raw: {},
    }
    const savedTargets = [{ platform: 'yandex', site: 'global', language: 'ru-RU', currency: 'RUB' }, { platform: 'ozon', site: 'global', language: 'ru-RU', currency: 'RUB' }]
    const savedDraft = { ...draft, platforms: ['yandex', 'ozon'], targetSites: savedTargets }
    const sibling = { ...item, draftId: 'draft-2', title: 'Sibling draft', platforms: ['yandex'] as DraftIndexItem['platforms'] }
    const savedIndex = [{ ...item, targetSites: savedDraft.targetSites, site: savedDraft.site, platforms: savedDraft.platforms }, sibling]
    vi.mocked(workflowApi.loadDraft).mockResolvedValue(draftMutation(draft, [item, sibling]))
    vi.mocked(workflowApi.saveDraft).mockResolvedValue(draftMutation(savedDraft, savedIndex))

    const store = useWorkflowStore()
    store.platformOptions = [
      { key: 'yandex', label: 'Yandex', sites: [{ key: 'global', code: 'global', label: '俄罗斯', language: 'ru-RU', currency: 'RUB' }] },
      { key: 'ozon', label: 'Ozon', sites: [{ key: 'global', code: 'global', label: '俄罗斯', language: 'ru-RU', currency: 'RUB' }] },
    ]
    await store.updateDraftTargets(item, savedTargets)

    expect(workflowApi.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      draftId: 'draft-1',
      platform: 'yandex',
      platforms: ['yandex', 'ozon'],
      site: 'global',
      language: 'ru-RU',
      currency: 'RUB',
      targetSites: savedTargets.map((target) => expect.objectContaining(target)),
    }))
    expect(store.currentDraft.draftId).toBe('')
    expect(store.draftsIndex[0].targetSites).toEqual(savedTargets)
    expect(store.draftsIndex[0].platforms).toEqual(['yandex', 'ozon'])
    expect(store.draftsIndex[1].platforms).toEqual(['yandex'])
  })

  it('updates draft language from configured target market languages only', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-1'
    draft.productId = 'product-1'
    draft.sourceProductId = 'product-1'
    draft.platform = 'yandex'
    draft.platforms = ['yandex']
    draft.site = 'global'
    draft.language = 'ru-RU'
    draft.currency = 'RUB'
    draft.targetSites = [{ platform: 'yandex', site: 'global', language: 'ru-RU', currency: 'RUB' }]
    const item: DraftIndexItem = {
      draftId: 'draft-1',
      productId: 'product-1',
      sourceProductId: 'product-1',
      platform: 'yandex',
      platforms: ['yandex'],
      targetSites: [{ platform: 'yandex', site: 'global', language: 'ru-RU', currency: 'RUB' }],
      site: 'global',
      language: 'ru-RU',
      status: 'claimed',
      title: 'Draft title',
      productTitle: 'Source title',
      mainImage: '',
      sourcePlatform: '1688',
      sourceUrl: 'https://example.com/source',
      categoryId: '',
      categoryPath: '',
      price: '',
      publishStatus: '',
      createdAt: '',
      updatedAt: '',
      productFilePath: '',
      raw: {},
    }
    const selectedTarget = { platform: 'mercadolibre', site: 'CBT', language: 'es', currency: 'USD' }
    const savedDraft = { ...draft, platform: 'mercadolibre', platforms: ['mercadolibre'], site: 'CBT', language: 'es', currency: 'USD', targetSites: [selectedTarget] }
    vi.mocked(workflowApi.loadDraft).mockResolvedValue(draftMutation(draft, [item]))
    vi.mocked(workflowApi.saveDraft).mockResolvedValue(draftMutation(savedDraft, [{ ...item, ...savedDraft }]))

    const store = useWorkflowStore()
    store.platformOptions = [
      { key: 'mercadolibre', label: '美客多', sites: [
        { key: 'CBT', code: 'CBT', label: '全局', language: 'es', currency: 'USD' },
        { key: 'MLB', code: 'MLB', label: '巴西', language: 'pt-BR', currency: 'BRL' },
      ] },
      { key: 'yandex', label: 'Yandex', sites: [{ key: 'global', code: 'global', label: '俄罗斯', language: 'ru-RU', currency: 'RUB' }] },
    ]
    await store.updateDraftLanguage(item, 'es')

    expect(workflowApi.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      draftId: 'draft-1',
      platform: 'mercadolibre',
      platforms: ['mercadolibre'],
      site: 'CBT',
      language: 'es',
      currency: 'USD',
      targetSites: [expect.objectContaining(selectedTarget)],
    }))
  })

  it('does not reuse stale draft price as pricing applied price', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-1'
    draft.productId = 'product-1'
    draft.sourceProductId = 'product-1'
    draft.platform = 'mercadolibre'
    draft.platforms = ['mercadolibre']
    draft.site = 'CBT'
    draft.language = 'es'
    draft.currency = 'USD'
    draft.price = '94'
    draft.targetSites = [{ platform: 'mercadolibre', site: 'CBT', language: 'es', currency: 'USD' }]
    draft.pricing = {}
    vi.mocked(workflowApi.loadDraft).mockResolvedValue(draftMutation(draft))

    const store = useWorkflowStore()
    store.platformOptions = [
      { key: 'mercadolibre', label: '美客多', sites: [{ key: 'CBT', code: 'CBT', label: '全局', language: 'es', currency: 'USD' }] },
    ]
    await store.loadDraftForPricing('draft-1')

    expect(store.pricingInput.targets).toHaveLength(1)
    expect(store.pricingInput.targets[0].targetKey).toBe('mercadolibre:cbt')
    expect(store.pricingInput.targets[0].appliedPrice).toBe(0)
    expect(store.currentDraft.price).toBe('94')
  })

  it('类目预检前会把核价页尺寸同步到草稿', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-1'
    draft.productId = 'product-1'
    draft.sourceProductId = 'product-1'
    draft.site = 'MLM'
    draft.language = 'es'
    draft.currency = 'MXN'
    draft.categoryId = 'MLM123'
    draft.targetSites = [{
      platform: 'mercadolibre',
      site: 'MLM',
      language: 'es',
      currency: 'MXN',
      categoryId: 'MLM123',
    }]
    const productContext = createEmptyDraftProductContext()
    productContext.weightKg = '0.419'
    productContext.dimensions = { lengthCm: '21', widthCm: '15.5', heightCm: '12' }
    vi.mocked(workflowApi.loadDraft).mockResolvedValue({
      ...draftMutation(draft),
      productContext,
    })
    vi.mocked(workflowApi.saveDraft).mockImplementation(async (savedDraft) => ({
      ...draftMutation(savedDraft),
      productContext,
    }))
    vi.mocked(workflowApi.runCategoryPrecheck).mockResolvedValue({
      ok: true,
      errors: [],
      missingFields: [],
      checkedAt: '2026-08-04T00:00:00Z',
      raw: { ok: true, missing_fields: [] },
    })

    const store = useWorkflowStore()
    store.platformOptions = [{
      key: 'mercadolibre',
      label: '美客多',
      sites: [{ key: 'MLM', code: 'MLM', label: '墨西哥', language: 'es', currency: 'MXN' }],
    }]
    await store.loadDraftForPricing('draft-1')

    await store.runCategoryOnlyPrecheck()

    expect(workflowApi.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      packageDimensions: {
        lengthCm: '21',
        widthCm: '15.5',
        heightCm: '12',
        weightKg: '0.419',
      },
    }))
    expect(workflowApi.runCategoryPrecheck).toHaveBeenCalledOnce()
  })

  it('syncs calculated applied prices back into pricing inputs', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-1'
    draft.productId = 'product-1'
    draft.sourceProductId = 'product-1'
    draft.platform = 'mercadolibre'
    draft.platforms = ['mercadolibre']
    draft.site = 'CBT'
    draft.language = 'es'
    draft.currency = 'USD'
    draft.targetSites = [
      { platform: 'mercadolibre', site: 'CBT', language: 'es', currency: 'USD' },
      { platform: 'mercadolibre', site: 'MLM', language: 'es', currency: 'MXN' },
    ]
    draft.pricing = {}
    const pricingResult: PricingResult = {
      results: [
        {
          targetKey: 'mercadolibre:cbt',
          platform: 'mercadolibre',
          site: 'CBT',
          currency: 'USD',
          suggestedPrice: 23.45,
          suggestedPriceUsd: 23.45,
          suggestedPriceCny: 159.2,
          appliedPrice: 23.45,
          shippingCostUsd: 2.7,
          shippingCostCny: 18.33,
          totalCostCny: 112.33,
          netRevenueCny: 159.2,
          profitCny: 47.76,
          marginPercent: 30,
          commissionPercent: 16,
          paymentFeePercent: 0,
          targetMarginPercent: 30,
          usdCnyRate: 6.7892,
          mxnUsdRate: 17.521375,
          rubCnyRate: 11.489603,
          isLoss: false,
          errors: [],
          raw: {},
        },
        {
          targetKey: 'mercadolibre:mlm',
          platform: 'mercadolibre',
          site: 'MLM',
          currency: 'MXN',
          suggestedPrice: 410.88,
          suggestedPriceUsd: 23.45,
          suggestedPriceCny: 159.2,
          appliedPrice: 410.88,
          shippingCostUsd: 2.7,
          shippingCostCny: 18.33,
          totalCostCny: 112.33,
          netRevenueCny: 159.2,
          profitCny: 47.76,
          marginPercent: 30,
          commissionPercent: 16,
          paymentFeePercent: 0,
          targetMarginPercent: 30,
          usdCnyRate: 6.7892,
          mxnUsdRate: 17.521375,
          rubCnyRate: 11.489603,
          isLoss: false,
          errors: [],
          raw: {},
        },
      ],
      suggestedPriceMxn: 0,
      suggestedPriceUsd: 23.45,
      suggestedPriceCny: 159.2,
      wbPriceRub: 0,
      shippingCostUsd: 2.7,
      shippingCostCny: 18.33,
      totalCostCny: 112.33,
      netRevenueCny: 159.2,
      profitCny: 47.76,
      marginPercent: 30,
      usdCnyRate: 6.7892,
      mxnUsdRate: 17.521375,
      rubUsdRate: 77.999985,
      rubCnyRate: 11.489603,
      exchangeRateMode: 'live',
      exchangeRateSource: 'test://rates',
      exchangeRateFetchedAt: '2026-07-19T00:00:00Z',
      exchangeRateCached: false,
    }
    vi.mocked(workflowApi.loadDraft).mockResolvedValue(draftMutation(draft))
    vi.mocked(workflowApi.calculatePrice).mockResolvedValue(pricingResult)
    vi.mocked(workflowApi.saveDraft).mockImplementation(async (savedDraft) => draftMutation(savedDraft))

    const store = useWorkflowStore()
    store.platformOptions = [
      {
        key: 'mercadolibre',
        label: '美客多',
        sites: [
          { key: 'CBT', code: 'CBT', label: '全局', language: 'es', currency: 'USD' },
          { key: 'MLM', code: 'MLM', label: '墨西哥', language: 'es', currency: 'MXN' },
        ],
      },
    ]
    await store.loadDraftForPricing('draft-1')
    expect(store.pricingInput.targets.map((target) => target.appliedPrice)).toEqual([0, 0])

    await store.calculatePrice()

    expect(store.pricingInput.targets.map((target) => target.appliedPrice)).toEqual([23.45, 410.88])
    expect(store.pricingInput.targets.map((target) => target.shippingCostUsd)).toEqual([2.7, 2.7])
    expect(store.currentDraft.price).toBe('23.45')
    expect(workflowApi.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      pricing: expect.objectContaining({
        targets: expect.objectContaining({
          'mercadolibre:cbt': expect.objectContaining({ applied_price: 23.45 }),
          'mercadolibre:mlm': expect.objectContaining({ applied_price: 410.88 }),
        }),
        exchange_rates: expect.objectContaining({ source: 'test://rates' }),
        updated_at: expect.any(String),
      }),
    }))
    const savedPricing = vi.mocked(workflowApi.saveDraft).mock.calls[0][0].pricing
    expect(savedPricing).not.toHaveProperty('suggestedPrice')
    expect(savedPricing).not.toHaveProperty('appliedPrice')
    expect(savedPricing).not.toHaveProperty('targetKey')
    expect(savedPricing).not.toHaveProperty('exchangeRates')
    expect(savedPricing).not.toHaveProperty('updatedAt')
  })
})
