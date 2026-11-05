import type { CollectDiagnostics, CollectForm, DraftDetail, DraftProductContext, Marketplace, MarketplaceDraft, PricingInput, Product } from '@/types/workflow'
import { listingLanguageValue } from '@/constants/locales'

export const marketplaces: Marketplace[] = ['mercadolibre', 'yandex', 'ozon']

export function createEmptyDraft(language = '', site = ''): MarketplaceDraft {
  return {
    skuItems: [],
    grouping: { mode: 'combined', name: '' },
    draftId: '',
    platforms: [],
    targetSites: [],
    site,
    enabled: true,
    globalTitle: '',
    title: '',
    description: '',
    categoryId: '',
    descriptionCategoryId: '',
    categoryPath: '',
    attributes: {},
    pricing: {},
    images: [],
    status: 'pending',
    language,
    saleTerms: [],
    guaranteePeriod: {},
    allowGtinExemption: false,
    validationErrors: [],
    publishStatus: '',
    lastPrecheck: {},
    lastPrecheckTarget: {},
  }
}

export function createEmptyProduct(): Product {
  return {
    attributes: {},
    skuItems: [],
    productId: '',
    name: '',
    brand: '',
    model: '',
    category: '',
    sku: '',
    stock: '',
    upc: '',
    materials: [],
    packageIncludes: [],
    source: {
      sourceUrl: '',
      sourcePlatform: '',
      title: '',
      price: '',
      currency: '',
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
    skuItems: [],
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
    autoAiRecognition: true,
    outputDir: 'data/images/source',
    selectedClaimPlatforms: ['mercadolibre'],
    manualTitle: '',
    manualPrice: '',
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
    exchangeRateOverrides: {},
    targets: [],
  }
}
