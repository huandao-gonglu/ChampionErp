import type { CollectDiagnostics, CollectForm, DraftDetail, DraftProductContext, Marketplace, MarketplaceDraft, PricingInput, Product } from '@/types/workflow'
import { listingLanguageValue } from '@/constants/locales'

export const marketplaces: Marketplace[] = ['mercadolibre', 'yandex', 'ozon']

export function createEmptyDraft(language = '', site = ''): MarketplaceDraft {
  return {
    draftId: '',
    platforms: [],
    targetSites: [],
    site,
    enabled: true,
    globalTitle: '',
    title: '',
    description: '',
    brand: '',
    model: '',
    bullets: [],
    categoryId: '',
    descriptionCategoryId: '',
    categoryPath: '',
    attributes: {},
    pricing: {},
    images: [],
    status: 'pending',
    language,
    stock: '',
    sku: '',
    upc: '',
    packageDimensions: {
      lengthCm: '',
      widthCm: '',
      heightCm: '',
      weightKg: '',
    },
    saleTerms: [],
    allowGtinExemption: false,
    validationErrors: [],
    publishStatus: '',
    lastPrecheck: {},
    lastPrecheckTarget: {},
    publication: null,
  }
}

export function createEmptyProduct(): Product {
  return {
    productId: '',
    name: '',
    brand: '',
    model: '',
    category: '',
    sku: '',
    stock: '',
    upc: '',
    cost: '',
    materials: [],
    sellingPoints: [],
    packageIncludes: [],
    source: {
      sourceUrl: '',
      sourcePlatform: '',
      title: '',
      price: '',
      currency: '',
      description: '',
      dimensions: {
        lengthCm: '',
        widthCm: '',
        heightCm: '',
      },
      weightKg: '',
      imagePool: [],
      attributes: {},
      attributeMatches: {},
      collectStatus: '',
      collectDiagnostics: {},
    },
    drafts: {
      mercadolibre: createEmptyDraft(listingLanguageValue('mercadolibre'), 'CBT'),
      yandex: createEmptyDraft(listingLanguageValue('yandex'), 'global'),
      ozon: createEmptyDraft(listingLanguageValue('ozon'), 'global'),
    },
    raw: {},
  }
}

export function createEmptyDraftDetail(platform: Marketplace = 'mercadolibre'): DraftDetail {
  return {
    ...createEmptyDraft(listingLanguageValue(platform)),
    productId: '',
    sourceProductId: '',
    platform,
    platforms: [platform],
    site: '',
    createdAt: '',
    updatedAt: '',
    raw: {},
  }
}

export function createEmptyDraftProductContext(): DraftProductContext {
  return {
    productId: '',
    sourceProductId: '',
    title: '',
    sourceTitle: '',
    sourcePlatform: '',
    sourceUrl: '',
    brand: '',
    model: '',
    sku: '',
    stock: '',
    cost: '',
    sourcePrice: '',
    currency: '',
    weightKg: '',
    dimensions: {
      lengthCm: '',
      widthCm: '',
      heightCm: '',
    },
    imagePool: [],
    raw: {},
  }
}

export function createDefaultCollectForm(): CollectForm {
  return {
    productUrl: '',
    productUrls: '',
    platform: '1688',
    mode: 'browser',
    alibabaApiBaseUrl: 'https://gw.open.1688.com/openapi/param2/1/com.alibaba.product/alibaba.product.get',
    alibabaApiMethod: 'alibaba.product.get',
    alibabaApiVersion: '1.0',
    alibabaApiTimeoutSeconds: '20',
    autoAiRecognition: true,
    outputDir: 'data/images/source',
    selectedClaimPlatforms: ['mercadolibre'],
    manualTitle: '',
    manualPrice: '',
    manualBullets: '',
    manualDescription: '',
    manualDimensions: '',
    manualWeight: '',
    manualImages: '',
    rawText: '',
  }
}

export function createDefaultCollectDiagnostics(): CollectDiagnostics {
  return {
    status: 'idle',
    progress: 0,
    message: '等待采集',
    downloadedImages: 0,
    extractedBullets: 0,
    antiBotWarning: false,
    lastSourceUrl: '',
    errorCode: '',
    nextAction: '',
    htmlSnapshotPath: '',
    screenshotPath: '',
    raw: {},
  }
}

export function createDefaultPricingInput(): PricingInput {
  return {
    platform: 'mercadolibre',
    site: 'CBT',
    purchaseCostCny: 0,
    domesticFreightCny: 0,
    packagingCostCny: 0,
    otherCostCny: 0,
    weightKg: 0,
    lengthCm: 0,
    widthCm: 0,
    heightCm: 0,
    usdCnyRate: 0,
    mxnUsdRate: 0,
    rubCnyRate: 0,
    exchangeRateMode: 'live',
    targets: [],
  }
}
