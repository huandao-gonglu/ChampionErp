import type { Marketplace, MarketplaceOption, MarketplaceTargetSite } from '@/types/workflow'

export interface DraftLanguageOption {
  value: string
  siteCount: number
}

export function draftLanguageKey(language: string) {
  return String(language || '').trim().toLowerCase()
}

export function draftTargetKey(platform: Marketplace, site: string) {
  return `${String(platform || '').trim().toLowerCase()}:${String(site || '').trim().toLowerCase()}`
}

export function draftLanguageOptions(platformOptions: MarketplaceOption[]): DraftLanguageOption[] {
  const languages = new Map<string, DraftLanguageOption>()
  platformOptions.forEach((platform) => {
    platform.sites.forEach((site) => {
      const language = String(site.language || '').trim()
      if (!language) return
      const key = draftLanguageKey(language)
      const current = languages.get(key) || { value: language, siteCount: 0 }
      current.siteCount += 1
      languages.set(key, current)
    })
  })
  return Array.from(languages.values())
}

export function draftTargetsForLanguage(
  platformOptions: MarketplaceOption[],
  language: string,
): MarketplaceTargetSite[] {
  const selectedLanguage = draftLanguageKey(language)
  if (!selectedLanguage) return []
  return platformOptions.flatMap((platform) => platform.sites
    .filter((site) => draftLanguageKey(site.language) === selectedLanguage)
    .map((site) => ({
      platform: platform.key,
      site: site.code,
      language: site.language,
      marketCurrency: site.marketCurrency,
      listingCurrency: site.listingCurrency,
    })))
}

export function selectedDraftTargets(
  availableTargets: MarketplaceTargetSite[],
  targetSites: MarketplaceTargetSite[],
) {
  const selectedKeys = new Set(targetSites.map((target) => draftTargetKey(target.platform, target.site)))
  return availableTargets.filter((target) => selectedKeys.has(draftTargetKey(target.platform, target.site)))
}

export function draftTargetLabel(
  platformOptions: MarketplaceOption[],
  target: MarketplaceTargetSite,
  compact = false,
) {
  const platform = platformOptions.find((option) => option.key === target.platform)
  const site = platform?.sites.find((option) => option.code.toLowerCase() === String(target.site || '').toLowerCase())
  if (!site) return `${platform?.label || target.platform} · ${target.site || '-'}`
  return compact
    ? `${platform?.label || target.platform} · ${site.code}`
    : `${platform?.label || target.platform} · ${site.label}（${site.code}）`
}

