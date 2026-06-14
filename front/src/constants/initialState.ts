import type { CollectDiagnostics, CollectForm, Marketplace, MarketplaceDraft, PricingInput, Product } from '@/types/workflow'
import { listingLanguageLabel } from '@/constants/locales'

export const marketplaces: Marketplace[] = ['mercadolibre', 'wildberries', 'ozon']

export function createEmptyDraft(language = ''): MarketplaceDraft {
  return {
    draftId: '',
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
      mercadolibre: createEmptyDraft(listingLanguageLabel('mercadolibre')),
      wildberries: createEmptyDraft(listingLanguageLabel('wildberries')),
      ozon: createEmptyDraft(listingLanguageLabel('ozon')),
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
    alibabaAppKey: '',
    alibabaAppSecret: '',
    alibabaAccessToken: '',
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
    site: 'MLM',
    purchaseCostCny: 0,
    domesticFreightCny: 0,
    weightKg: 0,
    lengthCm: 0,
    widthCm: 0,
    heightCm: 0,
    commissionPercent: 16,
    targetMarginPercent: 30,
    usdCnyRate: 0,
    mxnUsdRate: 0,
    exchangeRateMode: 'live',
    displayCurrencyMode: 'platform',
  }
}
