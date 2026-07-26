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
  identifyProductForCategory: vi.fn(),
  calculatePrice: vi.fn(),
  publishPrecheck: vi.fn(),
  enqueuePublish: vi.fn(),
  fetchCategoryAttrs: vi.fn(),
  testStoreAuth: vi.fn(),
  testAiModel: vi.fn(),
  searchCategories: vi.fn(),
  saveStoreSettings: vi.fn(),
  saveAiConfig: vi.fn(),
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

  it('identifies the product once and keeps automatic category candidates for every target site', async () => {
    const draft = createEmptyDraftDetail('mercadolibre')
    draft.draftId = 'draft-1'
    draft.productId = 'real-product-1'
    draft.sourceProductId = 'real-product-1'
    draft.site = 'MLM'
    draft.targetSites = [
      { platform: 'mercadolibre', site: 'MLM', language: 'es-MX', currency: 'MXN' },
      { platform: 'mercadolibre', site: 'CBT', language: 'en-US', currency: 'USD' },
    ]
    vi.mocked(workflowApi.saveDraft).mockResolvedValue(draftMutation(draft))
    vi.mocked(workflowApi.identifyProductForCategory).mockResolvedValue({
      identity: { name: '手持风扇', productType: 'handheld_fan', confidence: 0.94, reason: ['标题和属性一致'] },
      targets: [
        { platform: 'mercadolibre', site: 'MLM', language: 'es-MX', currency: 'MXN', query: 'ventilador portátil' },
        { platform: 'mercadolibre', site: 'CBT', language: 'en-US', currency: 'USD', query: 'portable handheld fan' },
      ],
    })
    vi.mocked(workflowApi.searchCategories)
      .mockResolvedValueOnce({ results: [{ id: 'MLM1', name: 'Ventiladores', path: 'Hogar / Ventiladores', raw: {} }] })
      .mockResolvedValueOnce({ results: [{ id: 'CBT1', name: 'Fans', path: 'Home / Fans', raw: {} }] })

    const store = useWorkflowStore()
    store.currentDraft = draft
    await store.autoSuggestCategoriesForDraft()

    expect(workflowApi.identifyProductForCategory).toHaveBeenCalledTimes(1)
    expect(workflowApi.searchCategories).toHaveBeenNthCalledWith(1, 'mercadolibre', 'ventilador portátil', 'MLM', 5)
    expect(workflowApi.searchCategories).toHaveBeenNthCalledWith(2, 'mercadolibre', 'portable handheld fan', 'CBT', 5)
    expect(store.categoryAutoMatchProductName).toBe('手持风扇')
    expect(store.categoryQuery).toBe('ventilador portátil')
    expect(store.categoryResults[0]?.id).toBe('MLM1')

    store.selectPublishTarget(draft.targetSites[1])
    expect(store.categoryQuery).toBe('portable handheld fan')
    expect(store.categoryResults[0]?.id).toBe('CBT1')
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
    expect(workflowApi.identifyProductForCategory).not.toHaveBeenCalled()
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
