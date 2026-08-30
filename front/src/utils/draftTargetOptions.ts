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

export function isMercadoLibrePlatform(platform: Marketplace) {
  return String(platform || '').trim().toLowerCase() === 'mercadolibre'
}

export function isMercadoLibreParentSite(platform: Marketplace, site: string) {
  return isMercadoLibrePlatform(platform) && String(site || '').trim().toUpperCase() === 'CBT'
}

/**
 * Mercado 的 CBT 是内部父刊登身份，不是用户选择的销售市场。
 * 其余平台的站点仍直接作为草稿发布目标。
 */
export function isVisibleDraftMarket(platform: Marketplace, site: string) {
  return !isMercadoLibreParentSite(platform, site)
}

export function draftLanguageOptions(platformOptions: MarketplaceOption[]): DraftLanguageOption[] {
  const languages = new Map<string, DraftLanguageOption>()
  platformOptions.forEach((platform) => {
    platform.sites.forEach((site) => {
      if (!isVisibleDraftMarket(platform.key, site.code)) return
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
    .filter((site) => (
      isVisibleDraftMarket(platform.key, site.code)
      && draftLanguageKey(site.language) === selectedLanguage
    ))
    .map((site) => ({
      platform: platform.key,
      site: site.code,
      language: site.language,
      // 发布币种由店铺授权配置在核价时写入，站点 option 不再提供币种。
      listingCurrency: '',
    })))
}

export function selectedDraftTargets(
  availableTargets: MarketplaceTargetSite[],
  targetSites: MarketplaceTargetSite[],
) {
  const selectedKeys = new Set(targetSites.map((target) => draftTargetKey(target.platform, target.site)))
  const mercadoLibreTarget = targetSites.find((target) => (
    isMercadoLibreParentSite(target.platform, target.site)
  ))
  const mercadoLibreSites = new Set((mercadoLibreTarget?.sitesToSell || []).map((target) => (
    String(target.siteId || '').trim().toUpperCase()
  )))
  return availableTargets.filter((target) => (
    isMercadoLibrePlatform(target.platform)
      ? mercadoLibreSites.has(String(target.site || '').trim().toUpperCase())
      : selectedKeys.has(draftTargetKey(target.platform, target.site))
  ))
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
