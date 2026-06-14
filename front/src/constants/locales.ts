import type { Marketplace } from '@/types/workflow'

export const DEFAULT_UI_LOCALE = 'zh'
export const SUPPORTED_UI_LOCALES = ['zh', 'en'] as const
export type SupportedUiLocale = (typeof SUPPORTED_UI_LOCALES)[number]

export interface UiLocaleOption {
  value: SupportedUiLocale
  label: string
  htmlLang: string
  acceptLanguage: string
}

export const UI_LOCALE_OPTIONS: UiLocaleOption[] = [
  { value: 'zh', label: '中文界面', htmlLang: 'zh-CN', acceptLanguage: 'zh-CN,zh;q=0.9,en;q=0.7' },
  { value: 'en', label: 'English UI', htmlLang: 'en', acceptLanguage: 'en-US,en;q=0.9,zh-CN;q=0.7' },
]

export interface ListingLocaleOption {
  value: string
  label: string
  marketplaceLabel: string
}

export const MARKETPLACE_LISTING_LOCALES: Record<Marketplace, ListingLocaleOption> = {
  mercadolibre: {
    value: 'es-MX',
    label: 'Spanish (Mexico)',
    marketplaceLabel: 'Mercado Libre Mexico',
  },
  wildberries: {
    value: 'ru-RU',
    label: 'Russian',
    marketplaceLabel: 'Wildberries',
  },
  ozon: {
    value: 'ru-RU',
    label: 'Russian',
    marketplaceLabel: 'Ozon',
  },
}

export const LISTING_LANGUAGE_OPTIONS: ListingLocaleOption[] = [
  MARKETPLACE_LISTING_LOCALES.mercadolibre,
  { value: 'pt-BR', label: 'Portuguese (Brazil)', marketplaceLabel: 'Mercado Livre Brazil' },
  { value: 'en-US', label: 'English', marketplaceLabel: 'Generic English' },
  MARKETPLACE_LISTING_LOCALES.wildberries,
]

export function uiLocaleOption(locale: string | null | undefined): UiLocaleOption {
  return UI_LOCALE_OPTIONS.find((item) => item.value === locale) || UI_LOCALE_OPTIONS[0]
}

export function listingLocaleForMarketplace(platform: Marketplace): ListingLocaleOption {
  return MARKETPLACE_LISTING_LOCALES[platform] || MARKETPLACE_LISTING_LOCALES.mercadolibre
}

export function listingLanguageLabel(platform: Marketplace): string {
  return listingLocaleForMarketplace(platform).label
}
