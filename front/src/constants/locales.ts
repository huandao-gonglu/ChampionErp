import type { Marketplace } from '@/types/workflow'

export interface ListingLocaleOption {
  value: string
  label: string
  marketplaceLabel: string
}

export const MARKETPLACE_LISTING_LOCALES: Record<Marketplace, ListingLocaleOption> = {
  mercadolibre: {
    value: 'es',
    label: 'Spanish',
    marketplaceLabel: 'Mercado Libre Spanish-speaking markets',
  },
  yandex: {
    value: 'ru-RU',
    label: 'Russian',
    marketplaceLabel: 'Yandex Russia',
  },
  ozon: {
    value: 'ru-RU',
    label: 'Russian',
    marketplaceLabel: 'Ozon',
  },
}

export function listingLocaleForMarketplace(platform: Marketplace): ListingLocaleOption {
  return MARKETPLACE_LISTING_LOCALES[platform] || MARKETPLACE_LISTING_LOCALES.mercadolibre
}

export function listingLanguageValue(platform: Marketplace): string {
  return listingLocaleForMarketplace(platform).value
}
