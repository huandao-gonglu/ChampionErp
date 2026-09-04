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
import { jsonProbeMessages, JSON_PROBE_USER_MESSAGE } from '@/constants/aiCapabilityProbe'
import { withAiForeground } from '@/services/withAiForeground'
import type { AuthResult, DraftDetail, DraftIndexItem, PricingResult, Product } from '@/types/workflow'

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
  duplicateDraft: vi.fn(),
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
  fetchPublishJobs: vi.fn(),
  fetchMercadoLibreOrders: vi.fn(),
  fetchMercadoLibreUserProducts: vi.fn(),
  pauseMercadoLibreUserProduct: vi.fn(),
  reconcilePublishJob: vi.fn(),
  runCategoryPrecheck: vi.fn(),
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
  saveStoreCurrency: vi.fn(),
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

vi.mock('@/services/withAiForeground', () => ({
  withAiForeground: vi.fn(async (
    _options: unknown,
    operation: (context: { presentationId: string }) => Promise<unknown>,
  ) => (
    operation({ presentationId: 'presentation-store-test' })
  )),
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

  it('通过 AI Work presentation 运行模型能力测试', async () => {
    const result: AuthResult = {
      ok: false,
      message: '接口连接正常，但对话能力未通过。',
      error: '',
      errorCode: '',
      nextAction: '检查模型实际输出。',
      raw: {
        channel: 'ai_model',
        model_id: 'model-a',
        unsupported_capabilities: ['chat'],
      },
    }
    vi.mocked(workflowApi.testAiModel).mockResolvedValue(result)

    const store = useWorkflowStore()
    await store.testAiSettings({
      id: 'model-a',
      name: '模型 A',
      model: 'provider-model-a',
      probe_only_capability: 'chat',
      probe_capabilities: true,
    })

    expect(withAiForeground).toHaveBeenCalledWith(
      expect.objectContaining({
        displayTitle: '测试 AI 模型 · 模型 A',
        initialUserMessage: 'hello',
      }),
      expect.any(Function),
    )
    expect(workflowApi.testAiModel).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'model-a' }),
      'presentation-store-test',
    )
    expect(store.lastAuthResult).toEqual(result)
  })

  it('JSON 能力测试在 presentation 中显示实际 user 消息', async () => {
    const result: AuthResult = {
      ok: true,
      message: 'JSON 能力测试通过。',
      error: '',
      errorCode: '',
      nextAction: '',
      raw: { channel: 'ai_model', supported_capabilities: ['json'] },
    }
    vi.mocked(workflowApi.testAiModel).mockResolvedValue(result)

    const store = useWorkflowStore()
    await store.testAiSettings({
      id: 'model-json',
      name: 'JSON 模型',
      probe_only_capability: 'json',
      probe_capabilities: true,
      probe_messages: jsonProbeMessages(),
    })

    expect(withAiForeground).toHaveBeenCalledWith(
      expect.objectContaining({ initialUserMessage: JSON_PROBE_USER_MESSAGE }),
      expect.any(Function),
    )
  })

  it('自动加载模型列表时不占用 AI Work presentation', async () => {
    const result: AuthResult = {
      ok: true,
      message: '模型列表加载完成。',
      error: '',
      errorCode: '',
      nextAction: '',
      raw: { channel: 'ai_model', available_models: [] },
    }
    vi.mocked(workflowApi.testAiModel).mockResolvedValue(result)

    const store = useWorkflowStore()
    await store.testAiSettings({
      id: 'model-list-a',
      name: '模型列表 A',
      probe_capabilities: false,
      test_trigger: 'auto_model_list',
    })

    expect(withAiForeground).not.toHaveBeenCalled()
    expect(workflowApi.testAiModel).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'model-list-a' }),
    )
  })

  it('从商品 publish_preview 恢复父级与市场分层预检并失败关闭', async () => {
    const product = createEmptyProduct()
    product.productId = 'product-persisted-layered-precheck'
    product.drafts.mercadolibre.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
      sitesToSell: [{ siteId: 'MLC', logisticType: 'remote' }],
    }]
    product.raw.publish_preview = {
      mercadolibre: {
        ok: true,
        errors: [],
        warnings: [],
        checked_at: '2026-08-31T00:00:00Z',
        parent: {
          ok: true,
          status: 'passed',
          errors: [],
          warnings: [],
        },
        markets: [{
          site_id: 'MLC',
          logistic_type: 'remote',
          ok: true,
          status: 'passed',
          errors: [{ code: 'MARKET_LIMIT', field: 'shipping', message: '智利物流限制不通过' }],
          warnings: [],
        }],
      },
    }
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

    expect(store.precheck).toEqual(expect.objectContaining({
      ok: false,
      checkedAt: '2026-08-31T00:00:00Z',
      parent: expect.objectContaining({ status: 'passed', ok: true }),
      marketChecks: [expect.objectContaining({
        siteId: 'MLC',
        logisticType: 'remote',
        status: 'blocked',
        ok: false,
      })],
    }))
    expect(store.precheck?.errorItems).not.toEqual(expect.arrayContaining([
      expect.objectContaining({ code: 'LAYERED_PRECHECK_MARKETS_MISMATCH' }),
    ]))
    expect(store.workflowSteps.find((step) => step.key === 'precheck')?.status).not.toBe('done')
  })

  it('Mercado 旧式持久化 preview 缺少分层 scope 时不接受 stale ready 状态', async () => {
    const product = createEmptyProduct()
    product.productId = 'product-old-flat-precheck'
    product.drafts.mercadolibre.status = 'ready_to_publish'
    product.drafts.mercadolibre.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
      sitesToSell: [{ siteId: 'MLA', logisticType: 'remote' }],
    }]
    product.raw.publish_preview = {
      mercadolibre: {
        ok: true,
        errors: [],
        warnings: [],
        checked_at: '2026-08-30T00:00:00Z',
      },
    }
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

    expect(store.precheck?.ok).toBe(false)
    expect(store.precheck?.errorItems).toEqual([
      expect.objectContaining({ code: 'LAYERED_PRECHECK_REQUIRED' }),
    ])
    expect(store.workflowSteps.find((step) => step.key === 'precheck')?.status).not.toBe('done')
  })

  it('恢复持久化 Mercado preview 时按当前 target 的完整市场集合验收', async () => {
    const product = createEmptyProduct()
    product.productId = 'product-persisted-exact-markets'
    product.drafts.mercadolibre.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
      sitesToSell: [
        { siteId: 'MLA', logisticType: 'remote' },
        { siteId: 'MLC', logisticType: 'remote' },
      ],
    }]
    product.raw.publish_preview = {
      mercadolibre: {
        ok: true,
        errors: [],
        warnings: [],
        checked_at: '2026-08-31T01:00:00Z',
        parent: { ok: true, status: 'passed', errors: [], warnings: [] },
        markets: [
          { site_id: 'MLC', logistic_type: 'remote', ok: true, status: 'passed', errors: [], warnings: [] },
          { site_id: 'MLA', logistic_type: 'remote', ok: true, status: 'passed', errors: [], warnings: [] },
        ],
      },
    }
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

    expect(store.precheck).toEqual(expect.objectContaining({
      ok: true,
      checkedAt: '2026-08-31T01:00:00Z',
      errorItems: [],
    }))
    expect(store.workflowSteps.find((step) => step.key === 'precheck')?.status).toBe('done')
  })

  it('持久化 Mercado preview 缺少当前 target 的任一市场时失败关闭', async () => {
    const product = createEmptyProduct()
    product.productId = 'product-persisted-missing-market'
    product.drafts.mercadolibre.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
      sitesToSell: [
        { siteId: 'MLA', logisticType: 'remote' },
        { siteId: 'MLC', logisticType: 'remote' },
      ],
    }]
    product.raw.publish_preview = {
      mercadolibre: {
        ok: true,
        errors: [],
        warnings: [],
        parent: { ok: true, status: 'passed', errors: [], warnings: [] },
        markets: [
          { site_id: 'MLA', logistic_type: 'remote', ok: true, status: 'passed', errors: [], warnings: [] },
        ],
      },
    }
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

    expect(store.precheck?.ok).toBe(false)
    expect(store.precheck?.errorItems).toEqual([
      expect.objectContaining({ code: 'LAYERED_PRECHECK_MARKETS_MISMATCH' }),
    ])
    expect(store.workflowSteps.find((step) => step.key === 'precheck')?.status).not.toBe('done')
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

  it('保存凭据不触发任何在线测试；可信状态只来自测试结果', async () => {
    vi.mocked(workflowApi.saveStoreSettings).mockResolvedValue({
      storeConfig: { yandex: { campaign_id: '111', api_token: 'tok...ken' } },
      storeAuthSummary: { yandex: { platform: 'yandex', status: '已保存，未测试' } },
    })
    vi.mocked(workflowApi.fetchMercadoLibreAuthChecklist).mockResolvedValue({
      platform: 'mercadolibre',
      readyForAuthLink: false,
      tokenReady: false,
      missingCodes: [],
      fields: [],
      nextAction: '',
      copyText: '',
      raw: {},
    })

    const store = useWorkflowStore()
    await store.saveStoreConfig({
      yandex: { api_token: 'secret-token-value', campaign_id: '111' },
    })

    // 删除“仅 Yandex 保存后自动复测”特殊分支：所有平台共用同一授权完成流程。
    expect(workflowApi.testStoreAuth).not.toHaveBeenCalled()
    expect(store.storeAuthSummary).toEqual({
      yandex: { platform: 'yandex', status: '已保存，未测试' },
    })
  })

  it('已保存配置的授权测试结果刷新 storeConfig 与币种状态', async () => {
    vi.mocked(workflowApi.testStoreAuth).mockResolvedValue({
      ok: true,
      message: '测试成功：授权可用。',
      error: '',
      errorCode: '',
      nextAction: '',
      raw: {
        ok: true,
        platform: 'ozon',
        publish_ready: true,
        currency_configuration: {
          listing_currency: 'CNY',
          currency_mode: 'locked',
          currency_status: 'ready',
        },
        storeConfig: {
          ozon: {
            client_id: 'client-1',
            listing_currency: 'CNY',
            currency_mode: 'locked',
            currency_status: 'ready',
          },
        },
        storeAuthSummary: { ozon: { platform: 'ozon', status: '测试成功' } },
      },
    })

    const store = useWorkflowStore()
    await store.testAuth('ozon')

    expect(workflowApi.testStoreAuth).toHaveBeenCalledWith('ozon', '', {})
    expect(store.storeAuthSummary).toEqual({ ozon: { platform: 'ozon', status: '测试成功' } })
    expect(store.storeConfig.ozon).toMatchObject({
      listing_currency: 'CNY',
      currency_status: 'ready',
    })
  })

  it('未保存 preview 测试不更新持久化展示', async () => {
    vi.mocked(workflowApi.testStoreAuth).mockResolvedValue({
      ok: true,
      message: '测试成功：授权可用。',
      error: '',
      errorCode: '',
      nextAction: '',
      raw: {
        ok: true,
        platform: 'yandex',
        preview: true,
        currency_configuration: { listing_currency: 'RUB', currency_status: 'ready' },
        storeConfig: { yandex: { listing_currency: 'RUB' } },
        storeAuthSummary: { yandex: { platform: 'yandex', status: '测试成功' } },
      },
    })

    const store = useWorkflowStore()
    await store.testAuth('yandex', '', { yandex: { api_token: 'unsaved', campaign_id: '111' } })

    expect(store.storeAuthSummary).toEqual({})
    expect(store.storeConfig).toEqual({})
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
    vi.mocked(workflowApi.fetchMercadoLibreUserProducts).mockResolvedValue({
      items: [],
      refreshErrors: [],
      refreshScope: 'identity_mapping_only',
      checkedAt: '',
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
    expect(workflowApi.fetchMercadoLibreUserProducts).toHaveBeenCalledOnce()
    expect(workflowApi.fetchMercadoLibreUserProducts).toHaveBeenCalledWith('active', 1, 50, false)
    expect(store.mercadoLibreUserProductsRefreshScope).toBe('identity_mapping_only')
  })

  it('Mercado Libre 不允许绕过发布队列调用直接发布', async () => {
    const store = useWorkflowStore()
    store.activeMarketplace = 'mercadolibre'

    await store.publishDirect()

    expect(workflowApi.publishProductDirect).not.toHaveBeenCalled()
    expect(store.error).toContain('发布队列')
  })

  it('restores the latest persisted publish job when the queue opens', async () => {
    vi.mocked(workflowApi.fetchPublishJobs).mockResolvedValue({
      items: [{
        jobId: 'job-new',
        productId: 'product-1',
        productName: '测试商品',
        draftId: 'draft-1',
        status: 'failed',
        rawStatus: 'completed',
        stage: 'failed',
        attempts: 1,
        error: '合同币种不匹配',
        errorCode: '',
        nextAction: '',
        platforms: [{
          platform: 'ozon',
          draftId: 'draft-1',
          site: 'global',
          sitesToSell: [],
          status: 'failed',
          stage: 'failed',
          attempts: 1,
          error: '合同币种不匹配',
          errorCode: '',
          nextAction: '',
          updatedAt: '2026-08-06 22:01:44',
        }],
        createdAt: '2026-08-06 22:01:40',
        updatedAt: '2026-08-06 22:01:44',
      }],
      nextCursor: '',
    })
    vi.mocked(workflowApi.fetchPublishJob).mockResolvedValue({
      job_id: 'job-new',
      display_status: 'failed',
    })

    const store = useWorkflowStore()
    await store.hydrateTab('publish')

    expect(store.selectedPublishJobId).toBe('job-new')
    expect(store.publishJobs[0]?.status).toBe('failed')
    expect(store.publishJobStatus).toEqual({
      job_id: 'job-new',
      display_status: 'failed',
    })
    expect(workflowApi.fetchPublishJob).toHaveBeenCalledWith('job-new')
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

  it('将手动文案生成绑定到 AI Work presentation', async () => {
    const product = collectedProduct()
    vi.mocked(workflowApi.generateCopy).mockResolvedValue(mutation(product))
    const store = useWorkflowStore()
    store.product = product

    await store.generateCopy()

    expect(withAiForeground).toHaveBeenCalledWith(
      expect.objectContaining({
        displayTitle: '生成 AI 文案',
        initialUserMessage: '生成 AI 文案：Collected product（mercadolibre）。',
      }),
      expect.any(Function),
    )
    expect(workflowApi.generateCopy).toHaveBeenCalledWith(
      product,
      'mercadolibre',
      {},
      { presentationId: 'presentation-store-test' },
    )
  })

  it('将手动图生图绑定到 AI Work presentation', async () => {
    const product = collectedProduct()
    vi.mocked(workflowApi.imageEdit).mockResolvedValue(mutation(product))
    const store = useWorkflowStore()
    store.product = product

    await store.editImagesWithPrompt('去除背景', { sourceImageIds: ['img_1'] })

    expect(withAiForeground).toHaveBeenCalledWith(
      expect.objectContaining({
        displayTitle: 'AI 图生图',
        initialUserMessage: '去除背景\n处理范围：1 张已选图片。',
      }),
      expect.any(Function),
    )
    expect(workflowApi.imageEdit).toHaveBeenCalledWith(
      product,
      'mercadolibre',
      '去除背景',
      { sourceImageIds: ['img_1'] },
      { presentationId: 'presentation-store-test' },
    )
  })

  it('将手动图片翻译绑定到 AI Work presentation', async () => {
    const product = collectedProduct()
    vi.mocked(workflowApi.imageTranslate).mockResolvedValue(mutation(product))
    const store = useWorkflowStore()
    store.product = product

    await store.translateImages('es-MX', { sourceImageIds: ['img_1'] })

    expect(withAiForeground).toHaveBeenCalledWith(
      expect.objectContaining({
        displayTitle: 'AI 翻译/重绘图片',
        initialUserMessage: '将所选 1 张图片翻译并重绘为 es-MX。',
      }),
      expect.any(Function),
    )
    expect(workflowApi.imageTranslate).toHaveBeenCalledWith(
      product,
      'mercadolibre',
      'es-MX',
      { sourceImageIds: ['img_1'] },
      { presentationId: 'presentation-store-test' },
    )
  })

  it('将手动批量文案生成绑定到 AI Work presentation', async () => {
    vi.mocked(workflowApi.generateCopyBatch).mockResolvedValue({ message: '完成' })
    vi.mocked(workflowApi.fetchProductsIndex).mockResolvedValue([])
    const store = useWorkflowStore()
    store.selectedProductIds = ['product-1', 'product-2']

    await store.generateCopyForSelectedProducts()

    expect(withAiForeground).toHaveBeenCalledWith(
      expect.objectContaining({
        displayTitle: '批量生成 AI 文案',
        initialUserMessage: '为已选择的 2 个商品批量生成 mercadolibre 平台文案。',
      }),
      expect.any(Function),
    )
    expect(workflowApi.generateCopyBatch).toHaveBeenCalledWith(
      ['product-1', 'product-2'],
      'mercadolibre',
      { presentationId: 'presentation-store-test' },
    )
  })

  it('uses the active non-Mercado Libre draft when calculating workflow progress', () => {
    const draft = createEmptyDraftDetail('yandex')
    draft.draftId = 'draft-yandex'
    draft.platform = 'yandex'
    draft.platforms = ['yandex']
    draft.pricing = { targets: { 'yandex:global': { applied_price: { amount: '7990', currency: 'RUB' } } } }
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
    draft.targetSites = [{ platform: 'mercadolibre', site: 'CBT', language: 'en-US', listingCurrency: 'USD' }]
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
    store.storeConfig = { mercadolibre: { listing_model: 'user_products' } }
    await store.runPrecheck()

    expect(store.precheck?.ok).toBe(true)
    expect(store.currentDraft.status).toBe('ready_to_publish')
    expect(workflowApi.publishPrecheck).toHaveBeenCalledWith(
      expect.objectContaining({ draftId: 'draft-1', productId: 'real-product-1' }),
      expect.objectContaining({ platform: 'mercadolibre', site: 'CBT', categoryId: '', attributes: {} }),
    )
  })

  it('预检前保存并提交界面当前的保修与 GTIN 豁免数据', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-current-form-data'
    draft.productId = 'product-current-form-data'
    draft.sourceProductId = 'product-current-form-data'
    draft.site = 'CBT'
    draft.upc = ''
    draft.allowGtinExemption = true
    draft.saleTerms = [
      { id: 'WARRANTY_TYPE', value_id: '6150835', value_name: 'Sin garantía' },
    ]
    draft.targetSites = [{ platform: 'mercadolibre', site: 'CBT', language: 'en-US', listingCurrency: 'USD' }]
    vi.mocked(workflowApi.saveDraft).mockImplementation(async (savedDraft) => draftMutation(savedDraft))
    vi.mocked(workflowApi.publishPrecheck).mockImplementation(async (savedDraft) => ({
      draft: savedDraft,
      precheck: { ok: true, errors: [], warnings: [], errorItems: [], warningItems: [], checkedAt: '2026-08-30T00:00:00Z' },
      platformResults: {},
      productContext: createEmptyDraftProductContext(),
    }))

    const store = useWorkflowStore()
    store.currentDraft = draft
    store.storeConfig = { mercadolibre: { listing_model: 'traditional_global_items' } }

    await store.runPrecheck()

    expect(workflowApi.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      upc: '',
      allowGtinExemption: true,
      saleTerms: [
        { id: 'WARRANTY_TYPE', value_id: '6150835', value_name: 'Sin garantía' },
      ],
    }))
    expect(workflowApi.publishPrecheck).toHaveBeenCalledWith(
      expect.objectContaining({
        upc: '',
        allowGtinExemption: true,
        saleTerms: [
          { id: 'WARRANTY_TYPE', value_id: '6150835', value_name: 'Sin garantía' },
        ],
      }),
      expect.objectContaining({ platform: 'mercadolibre', site: 'CBT' }),
    )
  })

  it('Payload 确认后直接入队，不再保存并改变已确认草稿', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-confirmed'
    draft.productId = 'product-confirmed'
    draft.sourceProductId = 'product-confirmed'
    draft.site = 'CBT'
    draft.status = 'ready_to_publish'
    draft.targetSites = [{ platform: 'mercadolibre', site: 'CBT', language: 'en-US', listingCurrency: 'USD' }]
    vi.mocked(workflowApi.enqueuePublish).mockResolvedValue({
      jobId: 'job-confirmed',
      status: 'queued',
      platforms: ['mercadolibre'],
      createdAt: '2026-08-30T00:00:00Z',
      draftId: draft.draftId,
      targetKey: 'mercadolibre:cbt',
    })
    vi.mocked(workflowApi.fetchPublishJobs).mockResolvedValue({ items: [], nextCursor: '' })
    vi.mocked(workflowApi.fetchDraftsIndex).mockResolvedValue([])

    const store = useWorkflowStore()
    store.currentDraft = draft
    store.storeConfig = { mercadolibre: { listing_model: 'traditional_global_items' } }
    store.precheck = { ok: true, errors: [], warnings: [], errorItems: [], warningItems: [], checkedAt: '2026-08-30T00:00:00Z' }
    store.payloadPreview = {
      platform: 'mercadolibre',
      site: 'CBT',
      targetKey: 'mercadolibre:cbt',
      status: 'preview_only',
      path: '/tmp/confirmed-payload.json',
      payload: {},
      warning: '',
      validationDigest: 'confirmed-digest',
      summary: null,
      warnings: [],
    }

    await store.enqueuePublish()

    expect(workflowApi.saveDraft).not.toHaveBeenCalled()
    expect(workflowApi.enqueuePublish).toHaveBeenCalledWith(
      draft,
      expect.objectContaining({ platform: 'mercadolibre', site: 'CBT' }),
      'confirmed-digest',
    )
  })

  it('重新预检时立即废弃上一次 Payload 确认', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-recheck'
    draft.site = 'CBT'
    draft.targetSites = [{ platform: 'mercadolibre', site: 'CBT', language: 'en-US', listingCurrency: 'USD' }]
    vi.mocked(workflowApi.saveDraft).mockResolvedValue(draftMutation(draft))
    vi.mocked(workflowApi.publishPrecheck).mockRejectedValue(new Error('预检请求失败'))

    const store = useWorkflowStore()
    store.currentDraft = draft
    store.storeConfig = { mercadolibre: { listing_model: 'traditional_global_items' } }
    store.payloadPreview = {
      platform: 'mercadolibre',
      site: 'CBT',
      targetKey: 'mercadolibre:cbt',
      status: 'preview_only',
      path: '/tmp/old-payload.json',
      payload: {},
      warning: '',
      validationDigest: 'old-digest',
      summary: null,
      warnings: [],
    }

    await store.runPrecheck()

    expect(store.payloadPreview).toBeNull()
    expect(store.error).toBe('预检请求失败')
  })

  it('Payload 预览失败时不会保留上一次确认指纹', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-preview-retry'
    draft.site = 'CBT'
    draft.targetSites = [{ platform: 'mercadolibre', site: 'CBT', language: 'en-US', listingCurrency: 'USD' }]
    vi.mocked(workflowApi.saveDraft).mockResolvedValue(draftMutation(draft))
    vi.mocked(workflowApi.previewPublishPayload).mockRejectedValue(new Error('生成 Payload 失败'))

    const store = useWorkflowStore()
    store.currentDraft = draft
    store.storeConfig = { mercadolibre: { listing_model: 'traditional_global_items' } }
    store.payloadPreview = {
      platform: 'mercadolibre',
      site: 'CBT',
      targetKey: 'mercadolibre:cbt',
      status: 'preview_only',
      path: '/tmp/old-payload.json',
      payload: {},
      warning: '',
      validationDigest: 'old-digest',
      summary: null,
      warnings: [],
    }

    await store.previewPayload()

    expect(store.payloadPreview).toBeNull()
    expect(store.error).toBe('生成 Payload 失败')
  })

  it('入队失败后撤销 Payload 确认，要求重新预览', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-stale-confirmation'
    draft.site = 'CBT'
    draft.targetSites = [{ platform: 'mercadolibre', site: 'CBT', language: 'en-US', listingCurrency: 'USD' }]
    vi.mocked(workflowApi.enqueuePublish).mockRejectedValue(new Error('商品或发布 payload 已变化，原发布确认已失效。'))

    const store = useWorkflowStore()
    store.currentDraft = draft
    store.storeConfig = { mercadolibre: { listing_model: 'traditional_global_items' } }
    store.payloadPreview = {
      platform: 'mercadolibre',
      site: 'CBT',
      targetKey: 'mercadolibre:cbt',
      status: 'preview_only',
      path: '/tmp/stale-payload.json',
      payload: {},
      warning: '',
      validationDigest: 'stale-digest',
      summary: null,
      warnings: [],
    }

    await store.enqueuePublish()

    expect(workflowApi.enqueuePublish).toHaveBeenCalledOnce()
    expect(store.payloadPreview).toBeNull()
    expect(store.error).toContain('原发布确认已失效')
  })

  it('保存 CBT 草稿后清除本地预检与 Payload 预览', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-marketplace-title-edited'
    draft.site = 'CBT'
    draft.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
      sitesToSell: [{ siteId: 'MLM', logisticType: 'remote' }],
    }]
    vi.mocked(workflowApi.saveDraft).mockResolvedValue(draftMutation(draft))
    const store = useWorkflowStore()
    store.currentDraft = draft
    store.precheck = { ok: true, errors: [], warnings: [], errorItems: [], warningItems: [], checkedAt: '2026-08-27T00:00:00Z' }
    store.precheckResults = { mercadolibre: { ok: true } }
    store.payloadPreview = {
      platform: 'mercadolibre',
      site: 'CBT',
      targetKey: 'mercadolibre:cbt',
      status: 'ready',
      path: '/tmp/old-payload.json',
      payload: {},
      warning: '',
      validationDigest: 'stale-digest',
      summary: null,
      warnings: [],
    }

    await store.saveCurrentDraft()

    expect(workflowApi.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      targetSites: [expect.objectContaining({
        sitesToSell: [{ siteId: 'MLM', logisticType: 'remote' }],
      })],
    }))
    expect(store.precheck).toBeNull()
    expect(store.precheckResults).toEqual({})
    expect(store.payloadPreview).toBeNull()
  })

  it('发布字段编辑会即时废弃 ready 状态、旧预检与 Payload 预览', () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-publish-field-edited'
    draft.site = 'CBT'
    draft.categoryId = 'CBT455865'
    draft.status = 'ready_to_publish'
    draft.publishStatus = 'ready'
    draft.lastPrecheck = { ok: true }
    draft.lastPrecheckTarget = { site: 'CBT' }
    draft.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
      categoryId: 'CBT455865',
      status: 'ready_to_publish',
      publishStatus: 'ready',
      lastPrecheck: { ok: true },
      lastPrecheckTarget: { site: 'CBT' },
    }]
    const store = useWorkflowStore()
    store.currentDraft = draft
    store.activePublishTargetKey = 'mercadolibre:cbt'
    store.precheck = { ok: true, errors: [], warnings: [], errorItems: [], warningItems: [], checkedAt: '2026-08-29T00:00:00Z' }
    store.precheckResults = { mercadolibre: { ok: true } }
    store.payloadPreview = {
      platform: 'mercadolibre',
      site: 'CBT',
      targetKey: 'mercadolibre:cbt',
      status: 'ready',
      path: '/tmp/stale-payload.json',
      payload: {},
      warning: '',
      validationDigest: 'stale-digest',
      summary: null,
      warnings: [],
    }

    store.invalidatePublishValidation()

    expect(store.precheck).toBeNull()
    expect(store.precheckResults).toEqual({})
    expect(store.payloadPreview).toBeNull()
    expect(store.currentDraft).toEqual(expect.objectContaining({
      status: 'category_ready',
      publishStatus: '',
      lastPrecheck: {},
      lastPrecheckTarget: {},
    }))
    expect(store.currentDraft.targetSites[0]).toEqual(expect.objectContaining({
      status: 'category_ready',
      publishStatus: '',
      lastPrecheck: {},
      lastPrecheckTarget: {},
    }))
  })

  it('发布成功后变更销售目的地会开启新流程并保留 Siteless 与既有市场投影', () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-cbt-destinations'
    draft.site = 'CBT'
    draft.status = 'published'
    draft.publishStatus = 'success'
    draft.validationErrors = ['旧发布校验']
    draft.lastPrecheck = { ok: true }
    draft.lastPrecheckTarget = { site: 'CBT', sites_to_sell: [{ site_id: 'MLM', logistic_type: 'remote' }] }
    draft.publication = {
      model: 'MODEL-1',
      accountUserId: 'account-user-1',
      sitelessUserProductId: 'UP-SITELESS-1',
      sitelessFamilyId: 'FAMILY-1',
      parentItemId: 'CBT-PARENT-1',
      parentUserProductId: 'UP-PARENT-1',
      sellerId: 'seller-global',
      status: 'active',
      familyName: '测试商品',
      markets: [{
        siteId: 'MLM',
        itemId: 'MLM-ITEM-1',
        userProductId: 'UP-MLM-1',
        sellerId: 'seller-mx',
        logisticType: 'remote',
        status: 'active',
        price: 399,
        netProceeds: null,
        freeShipping: null,
        saleTerms: [],
        currencyId: 'MXN',
        listingTypeId: 'gold_special',
        error: '',
        lastOperation: {},
        updatedAt: '2026-08-24T00:00:00Z',
      }],
      confirmedPayload: {},
      error: '',
      lastOperation: {},
      updatedAt: '2026-08-24T00:00:00Z',
    }
    draft.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
      sitesToSell: [{ siteId: 'MLM', logisticType: 'remote' }],
      status: 'real_publish_success',
      publishStatus: 'success',
      validationErrors: ['旧目标校验'],
      lastPrecheck: { ok: true },
      lastPrecheckTarget: { site: 'CBT' },
    }]

    const store = useWorkflowStore()
    store.currentDraft = draft
    store.activePublishTargetKey = 'mercadolibre:cbt'
    store.storeConfig = {
      mercadolibre: {
        account_site_id: 'CBT',
        listing_model: 'user_products',
        marketplace_bindings: [
          { seller_id: 'seller-mx', site_id: 'MLM', logistic_type: 'remote', business_model: 'cross_border', pricing_model: 'price', user_product: true },
          { seller_id: 'seller-br', site_id: 'MLB', logistic_type: 'fulfillment', business_model: 'cross_border', pricing_model: 'price', user_product: true },
        ],
      },
    }
    store.pricingInput.targets = [{
      targetKey: 'mercadolibre:cbt',
      platform: 'mercadolibre',
      site: 'CBT',
      sitesToSell: [{ siteId: 'MLM', logisticType: 'remote' }],
      listingCurrency: 'USD',
      commissionPercent: 16,
      paymentFeePercent: 0,
      otherFeePercent: 0,
      pricingMode: 'margin',
      targetMarginPercent: 30,
      markupPercent: 30,
      shippingQuoteMode: 'auto',
      shippingCurrency: 'USD',
      shippingAmount: 0,
      manualPrice: null,
    }]
    store.precheck = { ok: true, errors: [], warnings: [], errorItems: [], warningItems: [], checkedAt: '2026-08-24T00:00:00Z' }
    store.precheckResults = { mercadolibre: { ok: true } }
    store.payloadPreview = {
      platform: 'mercadolibre',
      site: 'CBT',
      targetKey: 'mercadolibre:cbt',
      status: 'ready',
      path: '/tmp/payload.json',
      payload: { sites_to_sell: [{ site_id: 'MLM', logistic_type: 'remote' }] },
      warning: '',
      validationDigest: 'old-digest',
      summary: null,
      warnings: [],
    }

    expect(store.updateDraftSitesToSell(
      draft,
      draft.targetSites[0]!,
      [{ siteId: 'MLB', logisticType: 'fulfillment' }],
    )).toBe(true)

    expect(store.currentDraft.targetSites[0]).toEqual(expect.objectContaining({
      sitesToSell: [{ siteId: 'MLB', logisticType: 'fulfillment' }],
      status: 'category_ready',
      publishStatus: '',
      validationErrors: [],
      lastPrecheck: {},
      lastPrecheckTarget: {},
    }))
    expect(store.currentDraft.publication).toEqual(expect.objectContaining({
      sitelessUserProductId: 'UP-SITELESS-1',
      markets: [expect.objectContaining({ siteId: 'MLM', itemId: 'MLM-ITEM-1' })],
    }))
    expect(store.currentDraft).toEqual(expect.objectContaining({
      status: 'category_ready',
      publishStatus: '',
      validationErrors: [],
      lastPrecheck: {},
      lastPrecheckTarget: {},
    }))
    expect(store.pricingInput.targets[0]?.sitesToSell).toEqual([{ siteId: 'MLB', logisticType: 'fulfillment' }])
    expect(store.precheck).toBeNull()
    expect(store.precheckResults).toEqual({})
    expect(store.payloadPreview).toBeNull()
  })

  it('store 允许 user_product 缺失或 null，仅拒绝显式 false', () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-cbt-fully-managed'
    draft.site = 'CBT'
    draft.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
      sitesToSell: [{ siteId: 'MLM', logisticType: 'remote' }],
    }]
    const store = useWorkflowStore()
    store.currentDraft = draft
    store.storeConfig = {
      mercadolibre: {
        account_site_id: 'CBT',
        listing_model: 'user_products',
        marketplace_bindings: [
          { seller_id: 'seller-mx', site_id: 'MLM', logistic_type: 'remote', business_model: 'cross_border' },
          { seller_id: 'seller-co', site_id: 'MCO', logistic_type: 'remote', business_model: 'cross_border', pricing_model: 'net_proceeds', user_product: null },
          { seller_id: 'seller-pe', site_id: 'MPE', logistic_type: 'remote', business_model: 'cross_border', user_product: false },
        ],
      },
    }

    expect(store.updateDraftSitesToSell(
      draft,
      draft.targetSites[0]!,
      [{
        siteId: 'MCO',
        logisticType: 'remote',
        netProceeds: '24.50',
        listingTypeId: 'gold_special',
        status: 'paused',
        freeShipping: true,
        saleTerms: [{ id: 'WARRANTY_TYPE', value_name: 'Sin garantía' }],
      }],
    )).toBe(true)
    expect(store.currentDraft.targetSites[0]?.sitesToSell).toEqual([{
      siteId: 'MCO',
      logisticType: 'remote',
      netProceeds: '24.50',
      listingTypeId: 'gold_special',
      status: 'paused',
      freeShipping: true,
      saleTerms: [{ id: 'WARRANTY_TYPE', value_name: 'Sin garantía' }],
    }])

    expect(store.updateDraftSitesToSell(
      draft,
      draft.targetSites[0]!,
      [{ siteId: 'MPE', logisticType: 'remote' }],
    )).toBe(false)
    expect(store.currentDraft.targetSites[0]?.sitesToSell).toEqual([expect.objectContaining({
      siteId: 'MCO',
      logisticType: 'remote',
      listingTypeId: 'gold_special',
      status: 'paused',
      freeShipping: true,
      netProceeds: '24.50',
    })])
    expect(store.error).toContain('未启用 User Products')
  })

  it('traditional_global_items 服从 binding pricing_model，并拒绝混合计价市场', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-cbt-traditional'
    draft.site = 'CBT'
    const bindings = [
      { seller_id: 'seller-mx', site_id: 'MLM', logistic_type: 'remote', pricing_model: 'price', user_product: false },
      { seller_id: 'seller-br', site_id: 'MLB', logistic_type: 'remote', business_model: 'cross_border', pricing_model: 'net_proceeds', user_product: false },
      { seller_id: 'seller-cl', site_id: 'MLC', logistic_type: 'remote', pricing_model: 'net_proceeds', user_product: false },
      { seller_id: 'seller-co', site_id: 'MCO', logistic_type: 'remote', pricing_model: 'price', user_product: false },
      { seller_id: 'seller-ar', site_id: 'MLA', logistic_type: 'remote', pricing_model: 'price', user_product: false },
      { seller_id: 'seller-uy', site_id: 'MLU', logistic_type: 'remote', pricing_model: 'net_proceeds', user_product: false },
      { seller_id: 'seller-pe', site_id: 'MPE', logistic_type: 'remote', pricing_model: 'price', user_product: false },
    ]
    const sitesToSell = bindings.map((binding, index) => ({
      siteId: binding.site_id,
      logisticType: binding.logistic_type,
      ...(binding.pricing_model === 'price'
        ? { price: `${20 + index}.00` }
        : { netProceeds: `${15 + index}.00` }),
    }))
    draft.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
      sitesToSell: [],
    }]
    const store = useWorkflowStore()
    store.currentDraft = draft
    store.storeConfig = {
      mercadolibre: {
        account_site_id: 'CBT',
        listing_model: 'traditional_global_items',
        marketplace_bindings: bindings,
      },
    }
    store.pricingInput.targets = [{
      targetKey: 'mercadolibre:cbt',
      platform: 'mercadolibre',
      site: 'CBT',
      sitesToSell: [],
      listingCurrency: 'USD',
      commissionPercent: 16,
      paymentFeePercent: 0,
      otherFeePercent: 0,
      pricingMode: 'margin',
      targetMarginPercent: 30,
      markupPercent: 30,
      shippingQuoteMode: 'auto',
      shippingCurrency: 'USD',
      shippingAmount: 0,
      manualPrice: null,
    }]

    expect(store.updateDraftSitesToSell(draft, draft.targetSites[0]!, sitesToSell)).toBe(false)
    expect(store.currentDraft.targetSites[0]?.sitesToSell).toEqual([])
    expect(store.error).toContain('不能混用 price 与 net_proceeds')

    store.error = ''
    expect(store.updateDraftSitesToSell(draft, draft.targetSites[0]!, [{
      siteId: 'MLC',
      logisticType: 'remote',
      netProceeds: '17.00',
    }])).toBe(true)
    expect(store.currentDraft.targetSites[0]?.sitesToSell).toEqual([{
      siteId: 'MLC',
      logisticType: 'remote',
      netProceeds: '17.00',
    }])
    expect(store.error).toBe('')

    await store.calculatePrice()
    expect(workflowApi.calculatePrice).toHaveBeenCalledOnce()

    vi.mocked(workflowApi.saveDraft).mockImplementation(async (savedDraft) => draftMutation(savedDraft))
    vi.mocked(workflowApi.publishPrecheck).mockImplementation(async (savedDraft) => ({
      draft: savedDraft,
      precheck: { ok: true, errors: [], warnings: [], errorItems: [], warningItems: [], checkedAt: '2026-08-27T00:00:00Z' },
      platformResults: {},
      productContext: createEmptyDraftProductContext(),
    }))
    await store.runPrecheck()
    expect(workflowApi.publishPrecheck).toHaveBeenCalledOnce()
  })

  it('缺失 listing_model 时 store fail closed，不从 user_product_seller 推断', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-cbt-missing-listing-model'
    draft.site = 'CBT'
    draft.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
      sitesToSell: [{ siteId: 'MLM', logisticType: 'remote' }],
    }]
    const store = useWorkflowStore()
    store.currentDraft = draft
    store.storeConfig = {
      mercadolibre: {
        account_site_id: 'CBT',
        user_product_seller: true,
        marketplace_bindings: [{ site_id: 'MLM', logistic_type: 'remote', user_product: true }],
      },
    }
    store.pricingInput.targets = [{
      targetKey: 'mercadolibre:cbt',
      platform: 'mercadolibre',
      site: 'CBT',
      sitesToSell: [{ siteId: 'MLM', logisticType: 'remote' }],
      listingCurrency: 'USD',
      commissionPercent: 16,
      paymentFeePercent: 0,
      otherFeePercent: 0,
      pricingMode: 'margin',
      targetMarginPercent: 30,
      markupPercent: 30,
      shippingQuoteMode: 'auto',
      shippingCurrency: 'USD',
      shippingAmount: 0,
      manualPrice: null,
    }]

    expect(store.updateDraftSitesToSell(
      draft,
      draft.targetSites[0]!,
      [{ siteId: 'MLM', logisticType: 'remote' }],
    )).toBe(false)
    expect(store.error).toContain('缺少 Mercado Libre listing_model')

    await store.calculatePrice()
    expect(store.error).toContain('缺少 Mercado Libre listing_model')
    expect(workflowApi.calculatePrice).not.toHaveBeenCalled()

    await store.runPrecheck()
    expect(store.error).toContain('缺少 Mercado Libre listing_model')
    expect(workflowApi.publishPrecheck).not.toHaveBeenCalled()
  })

  it('Fully Managed binding 按账号阻断市场选择、核价与标准发布', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-cbt-fully-managed'
    draft.site = 'CBT'
    draft.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
      sitesToSell: [{ siteId: 'MLM', logisticType: 'remote' }],
    }]
    const store = useWorkflowStore()
    store.currentDraft = draft
    store.storeConfig = {
      mercadolibre: {
        account_site_id: 'CBT',
        listing_model: 'traditional_global_items',
        marketplace_bindings: [
          { seller_id: 'seller-mx', site_id: 'MLM', logistic_type: 'remote', business_model: 'cross_border' },
          { seller_id: 'seller-co', site_id: 'MCO', logistic_type: 'remote', business_model: 'cross_border', user_product: null },
          { seller_id: 'seller-fm', site_id: 'MLB', logistic_type: 'fulfillment', business_model: 'CBT CN Fulfillment Managed', pricing_model: 'global_net_proceeds', user_product: true },
        ],
      },
    }
    const expectedError = '该账号需走 Fully Managed/global_net_proceeds 流程，当前尚未支持。'

    expect(store.updateDraftSitesToSell(
      draft,
      draft.targetSites[0]!,
      [{ siteId: 'MCO', logisticType: 'remote' }],
    )).toBe(false)
    expect(store.currentDraft.targetSites[0]?.sitesToSell).toEqual([{ siteId: 'MLM', logisticType: 'remote' }])
    expect(store.error).toBe(expectedError)

    await store.calculatePrice()
    expect(store.error).toBe(expectedError)
    expect(workflowApi.calculatePrice).not.toHaveBeenCalled()

    await store.runPrecheck()
    await store.previewPayload()
    await store.enqueuePublish()
    expect(store.error).toBe(expectedError)
    expect(workflowApi.publishPrecheck).not.toHaveBeenCalled()
    expect(workflowApi.previewPublishPayload).not.toHaveBeenCalled()
    expect(workflowApi.enqueuePublish).not.toHaveBeenCalled()
  })

  it('uses category.match as the only automatic matching path and still requires manual selection', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-pr3'
    draft.productId = 'real-product-1'
    draft.sourceProductId = 'real-product-1'
    draft.site = 'MLM'
    draft.title = 'Ventilador portátil'
    draft.targetSites = [
      { platform: 'mercadolibre', site: 'MLM', language: 'es-MX', listingCurrency: 'MXN' },
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
      trace: { taskRunId: 'task-1' },
    })

    const store = useWorkflowStore()
    store.currentDraft = draft
    await store.autoSuggestCategoriesForDraft()

    expect(workflowApi.matchCategory).toHaveBeenCalledOnce()
    expect(workflowApi.searchCategories).not.toHaveBeenCalled()
    expect(store.categoryResults[0]?.id).toBe('MLM-FAN')
    expect(store.currentDraft.categoryId).toBe('')
  })

  it('rejects a second AI business trigger while a foreground presentation is active', async () => {
    const { useAiWorkDisplayStore } = await import('@/stores/aiWorkDisplay')
    const display = useAiWorkDisplayStore()
    display.attachForegroundPresentation(
      {
        presentationId: 'presentation_existing',
        conversationId: 'conversation_existing',
        displayTitle: 'AI 匹配类目',
        status: 'running',
      },
      { id: 'presentation_existing', messages: [] } as never,
    )

    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-guard'
    draft.productId = 'real-product-1'
    draft.title = 'Ventilador'
    draft.targetSites = [
      { platform: 'mercadolibre', site: 'MLM', language: 'es-MX', listingCurrency: 'MXN' },
    ]

    const store = useWorkflowStore()
    store.currentDraft = draft
    const ok = await store.autoSuggestCategoriesForDraft()

    expect(ok).toBe(false)
    expect(store.error).toContain('已有前台 AI 任务运行')
    expect(workflowApi.matchCategory).not.toHaveBeenCalled()
    expect(workflowApi.fillCategoryAttributes).not.toHaveBeenCalled()

    await store.fillAttributesByAi()
    expect(store.error).toContain('已有前台 AI 任务运行')
    expect(workflowApi.fillCategoryAttributes).not.toHaveBeenCalled()
  })

  it('rejects AI business triggers while a foreground start is pending (atomic occupancy)', async () => {
    const { useAiWorkDisplayStore } = await import('@/stores/aiWorkDisplay')
    const display = useAiWorkDisplayStore()
    // 只有启动期同步占用（reserve POST 进行中），尚未 attach。
    display.beginForegroundStart()

    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-guard'
    draft.productId = 'real-product-1'
    draft.title = 'Ventilador'
    draft.targetSites = [
      { platform: 'mercadolibre', site: 'MLM', language: 'es-MX', listingCurrency: 'MXN' },
    ]

    const store = useWorkflowStore()
    store.currentDraft = draft
    const ok = await store.autoSuggestCategoriesForDraft()

    expect(ok).toBe(false)
    expect(store.error).toContain('已有前台 AI 任务运行')
    expect(workflowApi.matchCategory).not.toHaveBeenCalled()

    await store.fillAttributesByAi()
    expect(store.error).toContain('已有前台 AI 任务运行')
    expect(workflowApi.fillCategoryAttributes).not.toHaveBeenCalled()
  })

  it('keeps the Ozon target bound across save and AI attribute fill', async () => {
    const draft = createEmptyDraftDetail('yandex')
    draft.draftId = 'draft-ai-target-isolation'
    draft.productId = 'product-ai-target-isolation'
    draft.sourceProductId = 'product-ai-target-isolation'
    draft.platform = 'yandex'
    draft.platforms = ['yandex', 'ozon']
    draft.site = 'global'
    draft.language = 'ru-RU'
    draft.categoryId = '60996608'
    draft.categoryPath = 'Yandex / Pet houses'
    draft.attributes = { '43903290': { values: [{ value: 'кошки' }] } }
    draft.targetSites = [
      {
        platform: 'yandex',
        site: 'global',
        language: 'ru-RU',
        listingCurrency: 'RUB',
        categoryId: '60996608',
        categoryPath: 'Yandex / Pet houses',
        attributes: { '43903290': { values: [{ value: 'кошки' }] } },
        validationErrors: [],
      },
      {
        platform: 'ozon',
        site: 'global',
        language: 'ru-RU',
        listingCurrency: 'RUB',
        categoryId: '95196',
        descriptionCategoryId: '17028674',
        categoryPath: 'Ozon / Dog houses',
        attributes: {},
        validationErrors: [],
      },
    ]
    vi.mocked(workflowApi.saveDraft).mockImplementation(async (draftToSave) => {
      const saved = JSON.parse(JSON.stringify(draftToSave)) as DraftDetail
      const primary = saved.targetSites[0]!
      saved.platform = primary.platform
      saved.site = primary.site
      saved.categoryId = primary.categoryId || ''
      saved.descriptionCategoryId = primary.descriptionCategoryId || ''
      saved.categoryPath = primary.categoryPath || ''
      saved.attributes = JSON.parse(JSON.stringify(primary.attributes || {}))
      return draftMutation(saved)
    })
    vi.mocked(workflowApi.fillCategoryAttributes).mockImplementation(async (
      draftToFill,
      target,
    ) => {
      const filled = JSON.parse(JSON.stringify(draftToFill)) as DraftDetail
      const ozon = filled.targetSites.find((item) => item.platform === target.platform && item.site === target.site)!
      ozon.attributes = { '8229': { values: [{ dictionaryValueId: '95196', value: 'Будка для собак' }] } }
      filled.platform = 'yandex'
      filled.categoryId = '60996608'
      filled.categoryPath = 'Yandex / Pet houses'
      filled.attributes = { '43903290': { values: [{ value: 'кошки' }] } }
      return {
        ...draftMutation(filled),
        needReview: [],
        raw: { fill_source: 'ai_model' },
      }
    })

    const store = useWorkflowStore()
    store.currentDraft = draft
    store.activeMarketplace = 'yandex'
    store.activePublishTargetKey = 'yandex:global'
    store.selectPublishTarget(draft.targetSites[1]!)
    store.category = {
      platform: 'ozon',
      categoryId: '95196',
      categoryPath: 'Ozon / Dog houses',
      requiredAttributes: [{ id: '8229', name: 'Тип', required: true }],
      optionalAttributes: [],
      fetchedAt: '2026-09-02T12:00:00Z',
      raw: {},
    }

    await store.fillAttributesByAi()

    expect(workflowApi.fillCategoryAttributes).toHaveBeenCalledOnce()
    expect(workflowApi.fillCategoryAttributes).toHaveBeenCalledWith(
      expect.any(Object),
      expect.objectContaining({ platform: 'ozon', site: 'global', categoryId: '95196' }),
      '95196',
      expect.objectContaining({ platform: 'ozon', categoryId: '95196' }),
      { presentationId: 'presentation-store-test' },
    )
    expect(store.activePublishTargetKey).toBe('ozon:global')
    expect(store.currentDraft.attributes).toEqual({
      '8229': { values: [{ dictionaryValueId: '95196', value: 'Будка для собак' }] },
    })
    expect(store.currentDraft.targetSites.find((target) => target.platform === 'yandex')?.attributes).toEqual({
      '43903290': { values: [{ value: 'кошки' }] },
    })
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
    }, { presentationId: 'presentation-store-test' })
    expect(withAiForeground).toHaveBeenCalledWith(
      expect.objectContaining({
        displayTitle: '翻译候选类目',
        initialUserMessage: '将当前 1 个候选类目翻译为中文。',
      }),
      expect.any(Function),
    )
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
    }, { presentationId: 'presentation-store-test' })
    expect(withAiForeground).toHaveBeenCalledWith(
      expect.objectContaining({
        displayTitle: '翻译平台属性',
        initialUserMessage: '将类目 MLM-FAN 的平台属性名称、说明和选项翻译为中文。',
      }),
      expect.any(Function),
    )
    expect(workflowApi.fetchCategoryAttrs).not.toHaveBeenCalled()
    expect(store.categoryAttributeTranslations).toEqual({
      BRAND: {
        label: '品牌',
        help: '填写商品品牌',
        values: { Generic: '通用品牌' },
      },
    })
  })

  it('does not normalize root listing fields into missing or incomplete target sites', () => {
    const draft = createEmptyDraftDetail('yandex')
    draft.draftId = 'draft-no-root-target-fallback'
    draft.platform = 'yandex'
    draft.platforms = ['yandex']
    draft.site = 'global'
    draft.language = 'es-MX'
    draft.categoryId = '60996608'
    draft.descriptionCategoryId = '17028674'
    draft.categoryPath = 'Yandex / Бытовая техника'
    draft.attributes = { YANDEX_BRAND: 'Yandex brand' }
    draft.validationErrors = ['Yandex validation error']
    draft.publishStatus = 'ready'
    draft.status = 'ready_to_publish'

    const store = useWorkflowStore()
    store.currentDraft = draft
    expect(store.currentPublishTargets[0]).toEqual(expect.objectContaining({
      platform: 'yandex',
      site: 'global',
      language: '',
      categoryId: '',
      descriptionCategoryId: '',
      categoryPath: '',
      attributes: {},
      validationErrors: [],
      publishStatus: '',
      status: '',
    }))

    store.currentDraft.targetSites = [{
      platform: 'yandex',
      site: 'global',
      language: 'ru-RU',
      listingCurrency: 'RUB',
    }]
    expect(store.currentPublishTargets[0]).toEqual(expect.objectContaining({
      categoryId: '',
      descriptionCategoryId: '',
      categoryPath: '',
      attributes: {},
      validationErrors: [],
      publishStatus: '',
      status: '',
    }))
  })

  it('keeps Yandex and Ozon category edits isolated across target switches and draft saves', async () => {
    const draft = createEmptyDraftDetail('yandex')
    draft.draftId = 'draft-yandex-ozon-isolation'
    draft.productId = 'product-yandex-ozon-isolation'
    draft.sourceProductId = 'product-yandex-ozon-isolation'
    draft.platform = 'yandex'
    draft.platforms = ['yandex', 'ozon']
    draft.site = 'global'
    draft.language = 'ru-RU'
    draft.categoryId = '60996608'
    draft.categoryPath = 'Yandex / Бытовая техника'
    draft.attributes = { YANDEX_BRAND: 'Yandex brand' }
    draft.validationErrors = ['Yandex validation error']
    // targetSites[0] 是持久化 primary；当前编辑目标由 active key 单独决定。
    draft.targetSites = [
      {
        platform: 'yandex',
        site: 'global',
        language: 'ru-RU',
        listingCurrency: 'RUB',
        categoryId: '60996608',
        descriptionCategoryId: '',
        categoryPath: 'Yandex / Бытовая техника',
        attributes: { YANDEX_BRAND: 'Yandex brand' },
        validationErrors: ['Yandex validation error'],
      },
      {
        platform: 'ozon',
        site: 'global',
        language: 'ru-RU',
        listingCurrency: 'RUB',
        categoryId: '95199',
        descriptionCategoryId: '17028674',
        categoryPath: 'Ozon / Бытовая техника',
        attributes: { OZON_BRAND: 'Ozon brand' },
        validationErrors: ['Ozon validation error'],
      },
    ]
    const savedDrafts: DraftDetail[] = []
    vi.mocked(workflowApi.saveDraft).mockImplementation(async (draftToSave) => {
      const saved = JSON.parse(JSON.stringify(draftToSave)) as DraftDetail
      savedDrafts.push(saved)
      return draftMutation(JSON.parse(JSON.stringify(saved)) as DraftDetail)
    })

    const store = useWorkflowStore()
    store.currentDraft = draft
    store.activeMarketplace = 'yandex'
    store.activePublishTargetKey = 'yandex:global'

    store.selectPublishTarget(draft.targetSites[1]!)
    expect(store.currentDraft).toEqual(expect.objectContaining({
      categoryId: '95199',
      descriptionCategoryId: '17028674',
      categoryPath: 'Ozon / Бытовая техника',
      attributes: { OZON_BRAND: 'Ozon brand' },
      validationErrors: ['Ozon validation error'],
    }))

    // 显式清空 Ozon 类目编辑态，再切走；这些空值必须属于 Ozon 本身。
    store.currentDraft.categoryId = ''
    store.currentDraft.descriptionCategoryId = ''
    store.currentDraft.categoryPath = ''
    store.currentDraft.attributes = {}
    store.currentDraft.validationErrors = []
    store.selectPublishTarget(draft.targetSites[0]!)

    const clearedOzon = store.currentDraft.targetSites.find((target) => target.platform === 'ozon')
    expect(clearedOzon).toEqual(expect.objectContaining({
      categoryId: '',
      descriptionCategoryId: '',
      categoryPath: '',
      attributes: {},
      validationErrors: [],
    }))
    expect(store.currentDraft).toEqual(expect.objectContaining({
      categoryId: '60996608',
      categoryPath: 'Yandex / Бытовая техника',
      attributes: { YANDEX_BRAND: 'Yandex brand' },
      validationErrors: ['Yandex validation error'],
    }))

    store.selectPublishTarget(clearedOzon!)
    expect(store.currentDraft).toEqual(expect.objectContaining({
      categoryId: '',
      descriptionCategoryId: '',
      categoryPath: '',
      attributes: {},
      validationErrors: [],
    }))

    await store.saveCurrentDraft()

    const savedOzon = savedDrafts[0]?.targetSites.find((target) => target.platform === 'ozon')
    const savedYandex = savedDrafts[0]?.targetSites.find((target) => target.platform === 'yandex')
    expect(savedOzon).toEqual(expect.objectContaining({
      categoryId: '',
      descriptionCategoryId: '',
      categoryPath: '',
      attributes: {},
      validationErrors: [],
    }))
    expect(savedYandex).toEqual(expect.objectContaining({
      categoryId: '60996608',
      descriptionCategoryId: '',
      categoryPath: 'Yandex / Бытовая техника',
      attributes: { YANDEX_BRAND: 'Yandex brand' },
      validationErrors: ['Yandex validation error'],
    }))

    store.selectPublishTarget(store.currentDraft.targetSites.find((target) => target.platform === 'yandex')!)
    expect(store.currentDraft.categoryId).toBe('60996608')
    store.selectPublishTarget(store.currentDraft.targetSites.find((target) => target.platform === 'ozon')!)
    expect(store.currentDraft.categoryId).toBe('')
    expect(store.currentDraft.attributes).toEqual({})
  })

  it('does not seed a newly selected market from root listing fields', async () => {
    const draft = createEmptyDraftDetail('yandex')
    draft.draftId = 'draft-add-ozon-isolation'
    draft.productId = 'product-add-ozon-isolation'
    draft.sourceProductId = 'product-add-ozon-isolation'
    draft.platform = 'yandex'
    draft.platforms = ['yandex']
    draft.site = 'global'
    draft.language = 'ru-RU'
    draft.categoryId = '60996608'
    draft.categoryPath = 'Yandex / Бытовая техника'
    draft.attributes = { YANDEX_BRAND: 'Yandex brand' }
    draft.validationErrors = ['Yandex validation error']
    draft.targetSites = [{
      platform: 'yandex',
      site: 'global',
      language: 'ru-RU',
      listingCurrency: 'RUB',
      categoryId: '60996608',
      categoryPath: 'Yandex / Бытовая техника',
      attributes: { YANDEX_BRAND: 'Yandex brand' },
      validationErrors: ['Yandex validation error'],
    }]
    const savedDrafts: DraftDetail[] = []
    vi.mocked(workflowApi.saveDraft).mockImplementation(async (draftToSave) => {
      const saved = JSON.parse(JSON.stringify(draftToSave)) as DraftDetail
      savedDrafts.push(saved)
      return draftMutation(JSON.parse(JSON.stringify(saved)) as DraftDetail)
    })

    const store = useWorkflowStore()
    // Ozon 配置排在第一位，使新增 Ozon 成为新的 primary target。
    store.platformOptions = [
      { key: 'ozon', label: 'Ozon', sites: [{ key: 'global', code: 'global', label: '俄罗斯', language: 'ru-RU' }] },
      { key: 'yandex', label: 'Yandex', sites: [{ key: 'global', code: 'global', label: '俄罗斯', language: 'ru-RU' }] },
    ]
    store.currentDraft = draft

    await store.updateDraftTargets(draft, [
      { platform: 'ozon', site: 'global', language: 'ru-RU', listingCurrency: 'RUB' },
      { platform: 'yandex', site: 'global', language: 'ru-RU', listingCurrency: 'RUB' },
    ])

    const savedOzon = savedDrafts[0]?.targetSites.find((target) => target.platform === 'ozon')
    const savedYandex = savedDrafts[0]?.targetSites.find((target) => target.platform === 'yandex')
    expect(savedOzon).toEqual(expect.objectContaining({
      categoryId: '',
      descriptionCategoryId: '',
      categoryPath: '',
      attributes: {},
      validationErrors: [],
    }))
    expect(savedYandex).toEqual(expect.objectContaining({
      categoryId: '60996608',
      categoryPath: 'Yandex / Бытовая техника',
      attributes: { YANDEX_BRAND: 'Yandex brand' },
      validationErrors: ['Yandex validation error'],
    }))
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
        listingCurrency: 'MXN',
        categoryId: 'MLM-OLD',
        categoryPath: '旧类目',
        attributes: { OLD_ATTRIBUTE: '旧值' },
        categoryPrecheck: { ok: true },
      },
      {
        platform: 'mercadolibre',
        site: 'CBT',
        language: 'en-US',
        listingCurrency: 'USD',
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
      attributes: {},
      categoryPrecheck: {},
    }))
    expect(savedDrafts[0].targetSites[0]).not.toHaveProperty('categoryAttributeSchema')
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
    expect(workflowApi.fetchCategoryAttrs).toHaveBeenCalledWith('mercadolibre', 'MLM-NEW', 'MLM')
    // 属性定义只瞬时保存在编辑态，不写入保存 payload。
    expect(savedDrafts[1].targetSites[0]).not.toHaveProperty('categoryAttributeSchema')
    expect(savedDrafts[1].targetSites[0]).toEqual(expect.objectContaining({
      categoryId: 'MLM-NEW',
      categoryPath: '家居 / 新类目',
    }))
    expect(store.category?.requiredAttributes[0]?.id).toBe('BRAND')
    expect(store.loading).toBe(false)
    expect(store.categoryAttributeLoading).toBe(false)
    expect(store.categoryAttributeError).toBe('')

    // 切换目标站点只恢复类目身份，属性定义需重新从实时接口加载。
    store.selectPublishTarget(store.currentDraft.targetSites[1])
    expect(store.category?.categoryId).toBe('CBT-UNCHANGED')
    expect(store.category?.requiredAttributes).toEqual([])
    store.selectPublishTarget(store.currentDraft.targetSites[0])
    expect(store.category?.categoryId).toBe('MLM-NEW')
    expect(store.category?.requiredAttributes).toEqual([])
    expect(store.category?.fetchedAt).toBeUndefined()
  })

  it('keeps a selected category saved when loading its attributes fails', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-1'
    draft.productId = 'real-product-1'
    draft.sourceProductId = 'real-product-1'
    draft.site = 'MLM'
    draft.targetSites = [{ platform: 'mercadolibre', site: 'MLM', language: 'es-MX', listingCurrency: 'MXN' }]
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

  it('persists the Ozon description category id returned for a selected type', async () => {
    const draft = createEmptyDraftDetail('ozon')
    draft.draftId = 'draft-ozon'
    draft.productId = 'product-ozon'
    draft.sourceProductId = 'product-ozon'
    draft.site = 'global'
    draft.targetSites = [{ platform: 'ozon', site: 'global', language: 'ru-RU', listingCurrency: 'RUB' }]
    const savedDrafts: DraftDetail[] = []
    vi.mocked(workflowApi.saveDraft).mockImplementation(async (draftToSave) => {
      const saved = JSON.parse(JSON.stringify(draftToSave)) as DraftDetail
      savedDrafts.push(saved)
      return draftMutation(saved)
    })
    vi.mocked(workflowApi.fetchCategoryAttrs).mockResolvedValue({
      platform: 'ozon',
      categoryId: '91443',
      categoryPath: 'Бытовая техника / Климатическая техника / Вентилятор',
      requiredAttributes: [],
      optionalAttributes: [],
      source: 'ozon_live',
      fetchedAt: '2026-08-05T12:00:00Z',
      raw: {
        category_id: '91443',
        type_id: '91443',
        description_category_id: '17039635',
      },
    })

    const store = useWorkflowStore()
    store.currentDraft = draft
    await store.selectCategory({
      id: '91443',
      name: 'Вентилятор',
      path: 'Бытовая техника / Климатическая техника / Вентилятор',
      raw: {
        category_id: '91443',
        type_id: '91443',
        description_category_id: '17039635',
      },
    })

    expect(savedDrafts).toHaveLength(2)
    expect(savedDrafts[0]?.descriptionCategoryId).toBe('17039635')
    expect(savedDrafts[0]?.targetSites[0]?.descriptionCategoryId).toBe('17039635')
    expect(savedDrafts[1]?.targetSites[0]?.descriptionCategoryId).toBe('17039635')
    expect(store.currentDraft.descriptionCategoryId).toBe('17039635')
  })

  it('clears the Ozon description category id when type id is edited manually', () => {
    const draft = createEmptyDraftDetail('ozon')
    draft.draftId = 'draft-ozon'
    draft.categoryId = '91443'
    draft.descriptionCategoryId = '17039635'
    draft.targetSites = [{
      platform: 'ozon',
      site: 'global',
      language: 'ru-RU',
      listingCurrency: 'RUB',
      categoryId: '91443',
      descriptionCategoryId: '17039635',
    }]

    const store = useWorkflowStore()
    store.currentDraft = draft
    store.currentDraft.categoryId = '99999'
    store.invalidateCategoryPrecheck()

    expect(store.currentDraft.descriptionCategoryId).toBe('')
    expect(store.currentDraft.targetSites[0]?.descriptionCategoryId).toBe('')
  })

  it('restores the category identity from the draft and refetches attribute definitions live when reopening a draft', async () => {
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
      listingCurrency: 'MXN',
      categoryId: 'MLM-NEW',
      categoryPath: '家居 / 新类目',
      attributes: {},
    }]
    vi.mocked(workflowApi.loadDraft).mockResolvedValue(draftMutation(draft))
    vi.mocked(workflowApi.saveDraft).mockImplementation(async (draftToSave) => draftMutation(JSON.parse(JSON.stringify(draftToSave)) as DraftDetail))
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
      publishStatus: '',
      createdAt: '',
      updatedAt: '',
      productFilePath: '',
      raw: {},
    }

    const store = useWorkflowStore()
    await store.loadDraft(item)

    // 重开草稿只恢复类目身份，属性定义不再从持久化 schema 读取。
    expect(store.category?.categoryId).toBe('MLM-NEW')
    expect(store.category?.categoryPath).toBe('家居 / 新类目')
    expect(store.category?.requiredAttributes).toEqual([])
    expect(store.category?.fetchedAt).toBeUndefined()
    expect(workflowApi.fetchCategoryAttrs).not.toHaveBeenCalled()

    // 进入类目/属性页时经实时接口瞬时加载定义，只保留在编辑态。
    await store.loadCategoryAttributes()

    expect(workflowApi.fetchCategoryAttrs).toHaveBeenCalledWith('mercadolibre', 'MLM-NEW', 'MLM')
    expect(store.category?.requiredAttributes).toEqual([
      expect.objectContaining({ id: 'BRAND', name: 'Brand', required: true }),
    ])
    expect(store.category?.fetchedAt).toBe('2026-07-25T12:00:00Z')
    // 保存 payload 不包含已废弃的 category_attribute_schema。
    const saveCalls = vi.mocked(workflowApi.saveDraft).mock.calls
    const savedDraft = saveCalls[saveCalls.length - 1]?.[0]
    expect(savedDraft?.targetSites[0]).not.toHaveProperty('categoryAttributeSchema')
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

  it('duplicates a draft and replaces the draft index with the API result', async () => {
    const source: DraftIndexItem = {
      draftId: 'draft-1',
      productId: 'product-1',
      sourceProductId: 'product-1',
      platform: 'mercadolibre',
      platforms: ['mercadolibre'],
      targetSites: [{ platform: 'mercadolibre', site: 'MLM', language: 'es', listingCurrency: 'MXN' }],
      site: 'MLM',
      language: 'es',
      status: 'claimed',
      title: '原草稿',
      productTitle: '来源商品',
      mainImage: '',
      sourcePlatform: '1688',
      sourceUrl: 'https://example.com/source',
      categoryId: '',
      categoryPath: '',
      publishStatus: '',
      createdAt: '',
      updatedAt: '',
      productFilePath: '',
      raw: {},
    }
    const copy = { ...source, draftId: 'draft-2', title: '原草稿（副本）' }
    const copiedDraft = createEmptyDraftDetail('mercadolibre')
    copiedDraft.draftId = copy.draftId
    copiedDraft.productId = copy.productId
    copiedDraft.sourceProductId = copy.sourceProductId
    copiedDraft.title = copy.title
    vi.mocked(workflowApi.duplicateDraft).mockResolvedValue(draftMutation(copiedDraft, [source, copy]))

    const store = useWorkflowStore()
    store.draftsIndex = [source]
    await store.duplicateDraft(source)

    expect(workflowApi.duplicateDraft).toHaveBeenCalledWith('draft-1')
    expect(store.draftsIndex).toEqual([source, copy])
    expect(store.logs[0]).toContain('已复制草稿：原草稿')
    expect(store.error).toBe('')
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
    draft.targetSites = [{ platform: 'yandex', site: 'global', language: 'ru-RU', listingCurrency: 'RUB' }]
    const item: DraftIndexItem = {
      draftId: 'draft-1',
      productId: 'product-1',
      sourceProductId: 'product-1',
      platform: 'yandex',
      platforms: ['yandex'],
      targetSites: [{ platform: 'yandex', site: 'global', language: 'ru-RU', listingCurrency: 'RUB' }],
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
      publishStatus: '',
      createdAt: '',
      updatedAt: '',
      productFilePath: '',
      raw: {},
    }
    const savedTargets = [{ platform: 'yandex', site: 'global', language: 'ru-RU', listingCurrency: 'RUB' }, { platform: 'ozon', site: 'global', language: 'ru-RU', listingCurrency: 'RUB' }]
    const savedDraft = { ...draft, platforms: ['yandex', 'ozon'], targetSites: savedTargets }
    const sibling = { ...item, draftId: 'draft-2', title: 'Sibling draft', platforms: ['yandex'] as DraftIndexItem['platforms'] }
    const savedIndex = [{ ...item, targetSites: savedDraft.targetSites, site: savedDraft.site, platforms: savedDraft.platforms }, sibling]
    vi.mocked(workflowApi.loadDraft).mockResolvedValue(draftMutation(draft, [item, sibling]))
    vi.mocked(workflowApi.saveDraft).mockResolvedValue(draftMutation(savedDraft, savedIndex))

    const store = useWorkflowStore()
    store.platformOptions = [
      { key: 'yandex', label: 'Yandex', sites: [{ key: 'global', code: 'global', label: '俄罗斯', language: 'ru-RU' }] },
      { key: 'ozon', label: 'Ozon', sites: [{ key: 'global', code: 'global', label: '俄罗斯', language: 'ru-RU' }] },
    ]
    await store.updateDraftTargets(item, savedTargets)

    expect(workflowApi.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      draftId: 'draft-1',
      platform: 'yandex',
      platforms: ['yandex', 'ozon'],
      site: 'global',
      language: 'ru-RU',
      targetSites: savedTargets.map((target) => expect.objectContaining(target)),
    }))
    expect(store.currentDraft.draftId).toBe('')
    expect(store.draftsIndex[0].targetSites).toEqual(savedTargets)
    expect(store.draftsIndex[0].platforms).toEqual(['yandex', 'ozon'])
    expect(store.draftsIndex[1].platforms).toEqual(['yandex'])
  })

  it('通过草稿箱保存市场时拒绝混用 price 与 net_proceeds', async () => {
    const item: DraftIndexItem = {
      draftId: 'draft-cbt-mixed-pricing',
      productId: 'product-cbt-mixed-pricing',
      sourceProductId: 'product-cbt-mixed-pricing',
      platform: 'mercadolibre',
      platforms: ['mercadolibre'],
      targetSites: [],
      site: 'CBT',
      language: 'es',
      status: 'claimed',
      title: 'Mixed pricing draft',
      productTitle: 'Mixed pricing product',
      mainImage: '',
      sourcePlatform: '1688',
      sourceUrl: '',
      categoryId: '',
      categoryPath: '',
      publishStatus: '',
      createdAt: '',
      updatedAt: '',
      productFilePath: '',
      raw: {},
    }
    const store = useWorkflowStore()
    store.platformOptions = [{
      key: 'mercadolibre',
      label: '美客多',
      sites: [
        { key: 'CBT', code: 'CBT', label: '全局', language: 'en-US' },
        { key: 'MLM', code: 'MLM', label: '墨西哥', language: 'es' },
        { key: 'MCO', code: 'MCO', label: '哥伦比亚', language: 'es' },
      ],
    }]
    store.storeConfig = {
      mercadolibre: {
        account_site_id: 'CBT',
        listing_model: 'traditional_global_items',
        marketplace_bindings: [
          { site_id: 'MLM', logistic_type: 'remote', pricing_model: 'price', user_product: false },
          { site_id: 'MCO', logistic_type: 'remote', pricing_model: 'net_proceeds', user_product: false },
        ],
      },
    }

    await store.updateDraftTargets(item, [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'es',
      listingCurrency: 'USD',
      sitesToSell: [
        { siteId: 'MLM', logisticType: 'remote' },
        { siteId: 'MCO', logisticType: 'remote' },
      ],
    }])

    expect(workflowApi.saveDraft).not.toHaveBeenCalled()
    expect(store.error).toContain('不能混用 price 与 net_proceeds')
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
    draft.targetSites = [{ platform: 'yandex', site: 'global', language: 'ru-RU', listingCurrency: 'RUB' }]
    const item: DraftIndexItem = {
      draftId: 'draft-1',
      productId: 'product-1',
      sourceProductId: 'product-1',
      platform: 'yandex',
      platforms: ['yandex'],
      targetSites: [{ platform: 'yandex', site: 'global', language: 'ru-RU', listingCurrency: 'RUB' }],
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
      publishStatus: '',
      createdAt: '',
      updatedAt: '',
      productFilePath: '',
      raw: {},
    }
    // 切换语言会按市场配置重建目标；发布币种不再来自站点 option，
    // 重建后的新目标币种为空，等待店铺授权配置在核价时写入。
    const selectedTarget = { platform: 'mercadolibre', site: 'CBT', language: 'pt-BR', listingCurrency: '', sitesToSell: [] }
    const savedDraft = { ...draft, platform: 'mercadolibre', platforms: ['mercadolibre'], site: 'CBT', language: 'pt-BR', targetSites: [selectedTarget] }
    vi.mocked(workflowApi.loadDraft).mockResolvedValue(draftMutation(draft, [item]))
    vi.mocked(workflowApi.saveDraft).mockResolvedValue(draftMutation(savedDraft, [{ ...item, ...savedDraft }]))

    const store = useWorkflowStore()
    store.platformOptions = [
      { key: 'mercadolibre', label: '美客多', sites: [
        { key: 'CBT', code: 'CBT', label: '全局', language: 'en-US' },
        { key: 'MLB', code: 'MLB', label: '巴西', language: 'pt-BR' },
      ] },
      { key: 'yandex', label: 'Yandex', sites: [{ key: 'global', code: 'global', label: '俄罗斯', language: 'ru-RU' }] },
    ]
    store.storeConfig = {
      mercadolibre: {
        account_site_id: 'CBT',
        listing_model: 'traditional_global_items',
        marketplace_bindings: [{ seller_id: 'seller-br', site_id: 'MLB', logistic_type: 'remote' }],
      },
    }
    await store.updateDraftLanguage(item, 'pt-BR')

    expect(workflowApi.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      draftId: 'draft-1',
      platform: 'mercadolibre',
      platforms: ['mercadolibre'],
      site: 'CBT',
      language: 'pt-BR',
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
    draft.language = 'en-US'
    draft.targetSites = [{ platform: 'mercadolibre', site: 'CBT', language: 'en-US', listingCurrency: 'USD' }]
    draft.pricing = {}
    vi.mocked(workflowApi.loadDraft).mockResolvedValue(draftMutation(draft))

    const store = useWorkflowStore()
    store.platformOptions = [
      { key: 'mercadolibre', label: '美客多', sites: [{ key: 'CBT', code: 'CBT', label: '全局', language: 'en-US' }] },
    ]
    await store.loadDraftForPricing('draft-1')

    expect(store.pricingInput.targets).toHaveLength(1)
    expect(store.pricingInput.targets[0].targetKey).toBe('mercadolibre:cbt')
    expect(store.pricingInput.targets[0].manualPrice).toBeNull()
  })

  it('类目预检前会把核价页尺寸同步到草稿', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-1'
    draft.productId = 'product-1'
    draft.sourceProductId = 'product-1'
    draft.site = 'MLM'
    draft.language = 'es'
    draft.categoryId = 'MLM123'
    draft.targetSites = [{
      platform: 'mercadolibre',
      site: 'MLM',
      language: 'es',
      listingCurrency: 'MXN',
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
      sites: [{ key: 'MLM', code: 'MLM', label: '墨西哥', language: 'es' }],
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

  it('previews pricing without saving, then persists prices only when applied', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-1'
    draft.productId = 'product-1'
    draft.sourceProductId = 'product-1'
    draft.platform = 'mercadolibre'
    draft.platforms = ['mercadolibre']
    draft.site = 'CBT'
    draft.language = 'en-US'
    draft.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
      sitesToSell: [{ siteId: 'MLM', logisticType: 'remote', price: '29.90' }],
    }]
    draft.pricing = {}
    const pricingResult: PricingResult = {
      results: [
        {
          targetKey: 'mercadolibre:cbt',
          platform: 'mercadolibre',
          site: 'CBT',
          listingCurrency: 'USD',
          suggestedPrice: { amount: '23.45', currency: 'USD' },
          appliedPrice: { amount: '23.45', currency: 'USD' },
          appliedNetProceeds: { amount: '18.65', currency: 'USD' },
          destinationResults: [{
            siteId: 'MLM',
            logisticType: 'remote',
            pricingModel: 'net_proceeds',
            price: null,
            netProceeds: { amount: '18.65', currency: 'USD' },
            calculationFingerprint: 'fingerprint-cbt',
          }],
          convertedPrices: { USD: '23.45', CNY: '159.20' },
          calculationBasis: {
            sites_to_sell: [{
              site_id: 'MLM',
              logistic_type: 'remote',
            }],
          },
          calculationFingerprint: 'fingerprint-cbt',
          shippingCostUsd: 2.7,
          shippingCostCny: 18.33,
          totalCostCny: 112.33,
          netRevenueCny: 159.2,
          profitCny: 47.76,
          marginPercent: 30,
          commissionPercent: 16,
          paymentFeePercent: 0,
          otherFeePercent: 0,
          pricingMode: 'margin',
          targetMarginPercent: 30,
          markupPercent: 30,
          shippingQuoteMode: 'auto',
          shippingCurrency: 'USD',
          shippingAmount: 2.7,
          shippingSource: 'system_estimate',
          commissionCny: 25.47,
          paymentFeeCny: 0,
          otherFeeCny: 0,
          minimumPrice: { amount: '18.63', currency: 'USD' },
          billableWeightKg: 0.3,
          usdCnyRate: 6.7892,
          mxnUsdRate: 17.521375,
          rubCnyRate: 11.489603,
          isLoss: false,
          errors: [],
          raw: {},
        },
      ],
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
    store.storeConfig = {
      mercadolibre: {
        account_site_id: 'CBT',
        listing_model: 'user_products',
        marketplace_bindings: [
          { seller_id: 'seller-mx', site_id: 'MLM', logistic_type: 'remote', business_model: 'cross_border', pricing_model: 'net_proceeds', user_product: true },
        ],
      },
    }
    store.platformOptions = [
      {
        key: 'mercadolibre',
        label: '美客多',
        sites: [
          { key: 'CBT', code: 'CBT', label: '全局', language: 'en-US' },
          { key: 'MLM', code: 'MLM', label: '墨西哥', language: 'es' },
        ],
      },
    ]
    await store.loadDraftForPricing('draft-1')
    expect(store.pricingInput.targets.map((target) => target.manualPrice)).toEqual([null])

    await store.calculatePrice()

    expect(store.pricingInput.targets.map((target) => target.manualPrice)).toEqual([null])
    expect(workflowApi.saveDraft).not.toHaveBeenCalled()

    vi.mocked(workflowApi.calculatePrice).mockResolvedValueOnce({
      ...pricingResult,
      results: pricingResult.results.map((item, index) => index === 0 ? {
        ...item,
        suggestedPrice: { amount: '0', currency: item.listingCurrency },
        appliedPrice: { amount: '0', currency: item.listingCurrency },
        profitCny: 0,
        errors: [{ field: 'target_margin_percent', message: '平台费用合计 + 目标销售利润率必须小于 100%' }],
      } : item),
    })
    await store.applyPrice()
    expect(workflowApi.saveDraft).not.toHaveBeenCalled()

    vi.mocked(workflowApi.calculatePrice).mockResolvedValueOnce({
      ...pricingResult,
      results: pricingResult.results.map((item) => ({ ...item, destinationResults: [] })),
    })
    await store.applyPrice()
    expect(workflowApi.saveDraft).not.toHaveBeenCalled()
    expect(store.currentDraft.targetSites[0]?.sitesToSell).toEqual([{
      siteId: 'MLM',
      logisticType: 'remote',
      price: '29.90',
    }])
    expect(store.error).toContain('核价结果与当前销售市场不一致')

    await store.applyPrice()

    expect(store.pricingInput.targets.map((target) => target.manualPrice)).toEqual([null])
    expect(store.pricingInput.targets.map((target) => target.shippingAmount)).toEqual([2.7])
    expect(workflowApi.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      targetSites: [expect.objectContaining({
        sitesToSell: [{ siteId: 'MLM', logisticType: 'remote', netProceeds: '18.65' }],
      })],
      pricing: expect.objectContaining({
        targets: expect.objectContaining({
          'mercadolibre:cbt': expect.objectContaining({
            applied_price: { amount: '23.45', currency: 'USD' },
            applied_net_proceeds: { amount: '18.65', currency: 'USD' },
            destination_results: [{
              site_id: 'MLM',
              logistic_type: 'remote',
              pricing_model: 'net_proceeds',
              price: null,
              net_proceeds: { amount: '18.65', currency: 'USD' },
              calculation_fingerprint: 'fingerprint-cbt',
            }],
          }),
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
    expect(store.pricingResult?.results[0]?.destinationResults).toEqual([expect.objectContaining({
      siteId: 'MLM',
      pricingModel: 'net_proceeds',
      price: null,
      netProceeds: { amount: '18.65', currency: 'USD' },
    })])
  })

  it('恢复旧 Mercado CBT 核价时，缺少 destination_results 即视为过期', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-old-cbt-pricing'
    draft.productId = 'product-old-cbt-pricing'
    draft.sourceProductId = 'product-old-cbt-pricing'
    draft.platform = 'mercadolibre'
    draft.platforms = ['mercadolibre']
    draft.site = 'CBT'
    draft.language = 'en-US'
    draft.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
      sitesToSell: [{ siteId: 'MLM', logisticType: 'remote', price: '23.45' }],
    }]
    draft.pricing = {
      targets: {
        'mercadolibre:cbt': {
          listing_currency: 'USD',
          suggested_price: { amount: '23.45', currency: 'USD' },
          applied_price: { amount: '23.45', currency: 'USD' },
          profit_cny: 47.76,
          errors: [],
        },
      },
    }
    vi.mocked(workflowApi.loadDraft).mockResolvedValue(draftMutation(draft))

    const store = useWorkflowStore()
    store.storeConfig = {
      mercadolibre: {
        account_site_id: 'CBT',
        listing_model: 'traditional_global_items',
        marketplace_bindings: [{
          seller_id: 'seller-mx',
          site_id: 'MLM',
          logistic_type: 'remote',
          business_model: 'cross_border',
          pricing_model: 'price',
          user_product: false,
        }],
      },
    }
    store.platformOptions = [{
      key: 'mercadolibre',
      label: '美客多',
      sites: [
        { key: 'CBT', code: 'CBT', label: '全局', language: 'en-US' },
        { key: 'MLM', code: 'MLM', label: '墨西哥', language: 'es' },
      ],
    }]

    await store.loadDraftForPricing(draft.draftId)

    expect(store.pricingResult).toBeNull()
  })

  it('恢复 Mercado CBT 核价时，销售条件变化即视为过期', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-changed-cbt-condition'
    draft.productId = 'product-changed-cbt-condition'
    draft.sourceProductId = 'product-changed-cbt-condition'
    draft.platform = 'mercadolibre'
    draft.platforms = ['mercadolibre']
    draft.site = 'CBT'
    draft.language = 'es'
    draft.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'es',
      listingCurrency: 'USD',
      sitesToSell: [{
        siteId: 'MLM',
        logisticType: 'remote',
        listingTypeId: 'gold_pro',
        freeShipping: false,
        price: '23.45',
      }],
    }]
    draft.pricing = {
      targets: {
        'mercadolibre:cbt': {
          listing_currency: 'USD',
          suggested_price: { amount: '23.45', currency: 'USD' },
          applied_price: { amount: '23.45', currency: 'USD' },
          calculation_basis: {
            sites_to_sell: [{
              site_id: 'MLM',
              logistic_type: 'remote',
              listing_type_id: 'gold_special',
              free_shipping: false,
            }],
          },
          destination_results: [{
            site_id: 'MLM',
            logistic_type: 'remote',
            pricing_model: 'price',
            price: { amount: '23.45', currency: 'USD' },
            net_proceeds: null,
            calculation_fingerprint: 'fingerprint-old-condition',
          }],
          calculation_fingerprint: 'fingerprint-old-condition',
          profit_cny: 47.76,
          errors: [],
        },
      },
    }
    vi.mocked(workflowApi.loadDraft).mockResolvedValue(draftMutation(draft))

    const store = useWorkflowStore()
    store.platformOptions = [{
      key: 'mercadolibre',
      label: '美客多',
      sites: [
        { key: 'CBT', code: 'CBT', label: '全局', language: 'en-US' },
        { key: 'MLM', code: 'MLM', label: '墨西哥', language: 'es' },
      ],
    }]

    await store.loadDraftForPricing(draft.draftId)

    expect(store.pricingResult).toBeNull()
  })
})
