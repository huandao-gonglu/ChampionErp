import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createEmptyDraftDetail, createEmptyDraftProductContext, createEmptyProduct } from '@/constants/initialState'
import { useWorkflowStore } from '@/stores/workflow'
import * as workflowApi from '@/api/workflow'
import type { AppStateResponse, DraftMutationResponse, ProductMutationResponse } from '@/api/workflow'
import type { DraftDetail, DraftIndexItem, PricingResult, Product } from '@/types/workflow'

vi.mock('@/api/workflow', () => ({
  fetchState: vi.fn(),
  saveProduct: vi.fn(),
  collectProduct: vi.fn(),
  importManualProduct: vi.fn(),
  saveCollectSettings: vi.fn(),
  uploadImages: vi.fn(),
  generateCopy: vi.fn(),
  imageEdit: vi.fn(),
  imageTranslate: vi.fn(),
  calculatePrice: vi.fn(),
  publishPrecheck: vi.fn(),
  enqueuePublish: vi.fn(),
  fetchCategoryAttrs: vi.fn(),
  testStoreAuth: vi.fn(),
  testAiModel: vi.fn(),
  searchCategories: vi.fn(),
  saveStoreSettings: vi.fn(),
  saveAiConfig: vi.fn(),
  refreshCategoryCache: vi.fn(),
  previewPublishPayload: vi.fn(),
  openBrowserProfile: vi.fn(),
  openAuthLink: vi.fn(),
  open1688Browser: vi.fn(),
  loadProduct: vi.fn(),
  generateImagePrompts: vi.fn(),
  fillCategoryAttributes: vi.fn(),
  fetchPublishLogs: vi.fn(),
  fetchPublishJob: vi.fn(),
  fetchProductsIndex: vi.fn(),
  fetchDraftsIndex: vi.fn(),
  fetchBrowserDebugStatus: vi.fn(),
  fetchAiConfig: vi.fn(),
  fetchMercadoLibreAuthChecklist: vi.fn(),
  refreshMercadoLibreToken: vi.fn(),
  runMercadoLibreRealAuthTest: vi.fn(),
  buildMercadoLibreAuthLink: vi.fn(),
  exchangeMercadoLibreCode: vi.fn(),
  clearStoreAuth: vi.fn(),
  startCategoryCacheRefresh: vi.fn(),
  fetchCategoryCacheRefreshJob: vi.fn(),
  suggestCategories: vi.fn(),
  runCategoryPrecheck: vi.fn(),
  confirmMercadoLibreRealPublish: vi.fn(),
  publishProductDirect: vi.fn(),
  deleteProducts: vi.fn(),
  clean1688Text: vi.fn(),
  saveImagePool: vi.fn(),
  diagnosticsToCollectDiagnostics: vi.fn(),
  collectFromBrowserTab: vi.fn(),
  collectBatch: vi.fn(),
  claimProducts: vi.fn(),
  assignUpc: vi.fn(),
  loadDraft: vi.fn(),
  saveDraft: vi.fn(),
  deleteDraft: vi.fn(),
}))

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
      product,
      imagePool: [],
      appConfig: {},
      storeConfig: {},
      storeAuthSummary: {},
      outputDir: '',
      platformOptions: [],
      productsIndex: [],
      publishLogs: [],
    } satisfies AppStateResponse)

    const store = useWorkflowStore()
    await store.loadState()

    expect(store.product.productId).toBe('')
    expect(store.collectDiagnostics.status).toBe('idle')
    expect(workflowApi.fetchState).toHaveBeenCalledOnce()
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

  it('stores precheck result returned by backend', async () => {
    const product = collectedProduct()
    product.drafts.mercadolibre.status = 'ready_to_publish'
    vi.mocked(workflowApi.publishPrecheck).mockResolvedValue({
      product,
      precheck: { ok: true, errors: [], warnings: [], errorItems: [], warningItems: [], checkedAt: '2026-06-02T00:00:00Z' },
      platformResults: {},
    })

    const store = useWorkflowStore()
    store.product = product
    await store.runPrecheck()

    expect(store.precheck?.ok).toBe(true)
    expect(store.product.drafts.mercadolibre.status).toBe('ready_to_publish')
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
      targetSites: savedTargets,
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
      targetSites: [selectedTarget],
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
          'mercadolibre:cbt': expect.objectContaining({ appliedPrice: 23.45 }),
          'mercadolibre:mlm': expect.objectContaining({ appliedPrice: 410.88 }),
        }),
      }),
    }))
  })
})
