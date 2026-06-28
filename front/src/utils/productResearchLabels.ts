type ProductResearchMarketLike = {
  id?: string
  displayName?: string
  market?: string
  name?: string
  platform?: string
  site?: string
}

type ProductResearchProviderLike = {
  id?: string
  name?: string
  platform?: string
}

export function productResearchMarketCode(value: ProductResearchMarketLike | string | undefined | null) {
  const code = typeof value === 'string' ? value : value?.id || value?.market
  const raw = String(code || '').trim()
  return raw.includes('-') || raw.includes('.') ? raw : raw.toUpperCase()
}

export function productResearchMarketName(value: ProductResearchMarketLike | string | undefined | null) {
  const code = productResearchMarketCode(value)
  const configuredName = typeof value === 'string' ? '' : String(value?.displayName || value?.name || '').trim()
  return configuredName || code
}

export function productResearchMarketLabel(value: ProductResearchMarketLike | string | undefined | null) {
  const code = productResearchMarketCode(value)
  const name = productResearchMarketName(value)
  const site = typeof value === 'string' ? '' : String(value?.site || '').trim()
  if (site) return `${name || code} · ${site}`
  if (code && name && code !== name) return `${code} · ${name}`
  return name || code || '-'
}

export function productResearchProviderName(provider: ProductResearchProviderLike | undefined | null) {
  if (!provider) return '未命名搜索手段'
  const id = String(provider.id || '').trim()
  const platform = String(provider.platform || '').trim()
  const name = String(provider.name || '').trim()
  return name || id || platform || '未命名搜索手段'
}

export function productResearchStrategyLabel(value: string | undefined | null) {
  const labels: Record<string, string> = {
    ai_web_search: 'AI 联网搜索',
    configured_api: '已配置 API',
    manual_import: '人工导入',
    seeded_mock: '本地种子数据',
    stored_list: '保存列表',
  }
  return labels[String(value || '')] || String(value || '-') || '-'
}

export function productResearchSourceTypeLabel(value: string | undefined | null) {
  const labels: Record<string, string> = {
    ai_search: 'AI 搜索',
    api: 'API 搜索',
    crawler: '网页采集',
    manual_import: '人工导入',
    third_party_api: '第三方 API',
  }
  return labels[String(value || '')] || String(value || '-') || '-'
}

export function productResearchDataTypeLabel(value: string | undefined | null) {
  const labels: Record<string, string> = {
    ai_web_search: 'AI 联网搜索',
    marketplace_products: '商品数据',
    keyword_trend: '关键词趋势',
    content_trend: '内容趋势',
  }
  return labels[String(value || '')] || String(value || '-') || '-'
}
