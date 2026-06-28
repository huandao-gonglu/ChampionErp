import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createEmptyProduct } from '@/constants/initialState'
import { useWorkflowStore } from '@/stores/workflow'
import * as workflowApi from '@/api/workflow'
import type { AppStateResponse, ProductMutationResponse } from '@/api/workflow'
import type { Product } from '@/types/workflow'

vi.mock('@/api/workflow', () => ({
  fetchState: vi.fn(),
  saveProduct: vi.fn(),
  collectProduct: vi.fn(),
  importManualProduct: vi.fn(),
  saveCollectSettings: vi.fn(),
  uploadImages: vi.fn(),
  generateCopy: vi.fn(),
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
  syncGeneratedImages: vi.fn(),
  clean1688Text: vi.fn(),
  saveImagePool: vi.fn(),
  diagnosticsToCollectDiagnostics: vi.fn(),
  collectFromBrowserTab: vi.fn(),
  collectBatch: vi.fn(),
  claimProducts: vi.fn(),
  assignUpc: vi.fn(),
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
})
