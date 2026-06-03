import type { CollectDiagnostics, CollectForm, Marketplace, MarketplaceDraft, PricingInput, Product } from '@/types/workflow'

export const marketplaces: Marketplace[] = ['mercadolibre', 'wildberries', 'ozon']

export function createEmptyDraft(language = ''): MarketplaceDraft {
  return {
    enabled: true,
    title: '',
    description: '',
    bullets: [],
    categoryId: '',
    categoryPath: '',
    attributes: {},
    price: '',
    images: [],
    status: 'pending',
    language,
    stock: '',
    sku: '',
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
      collectStatus: '',
      collectDiagnostics: {},
    },
    drafts: {
      mercadolibre: createEmptyDraft('Spanish (Mexico)'),
      wildberries: createEmptyDraft('Russian'),
      ozon: createEmptyDraft('Russian'),
    },
    raw: {},
  }
}

export function createDefaultCollectForm(): CollectForm {
  return {
    productUrl: '',
    productUrls: '',
    platform: '1688',
    mode: 'browser',
    alibabaCookie: '',
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
    site: 'MLM',
    purchaseCostCny: 0,
    domesticFreightCny: 0,
    weightKg: 0,
    lengthCm: 0,
    widthCm: 0,
    heightCm: 0,
    commissionPercent: 16,
    targetMarginPercent: 30,
    usdCnyRate: 7.2,
    mxnUsdRate: 18,
  }
}
