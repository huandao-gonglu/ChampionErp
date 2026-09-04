import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import {
  createProductResearchHotProductRun,
  fetchProductResearchSettings,
  saveProductResearchSettings,
  testProductResearchSearchProvider,
} from '@/api/workflow/research'
import type { ProductResearchSourceRegistryItem } from '@/types/workflow'

vi.mock('@/api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

describe('选品 API 当前 wire schema', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('只把当前 snake_case 运行结果映射为前端 camelCase', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ok: true,
        description: '当前运行',
        run: {
          run_id: 'run-current',
          runId: 'run-legacy',
          status: 'completed',
          search_mode: 'target_only',
          searchMode: 'legacy-mode',
          created_at: '2026-07-30T10:00:00Z',
          createdAt: 'legacy-time',
          description: '当前运行',
        },
        items: [{
          id: 'item-current',
          title: '当前商品',
          image_url: 'https://current.example/image.jpg',
          imageUrl: 'https://legacy.example/image.jpg',
          source_url: 'https://current.example/item',
          sourceUrl: 'https://legacy.example/item',
          market_id: 'mercadolibre-mx',
          market: 'legacy-market',
          review_count: 12,
          reviewCount: 99,
          hot_score: 8.5,
          hotScore: 1,
        }],
        source_status: [{
          source: 'provider-current',
          source_id: 'provider-current',
          sourceId: 'provider-legacy',
          status: 'success',
          items_found: 1,
          itemsFound: 99,
        }],
        sourceStatus: [{
          source_id: 'legacy-container',
        }],
      },
    })

    const result = await createProductResearchHotProductRun(
      {},
      { presentationId: 'presentation-research' },
    )

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/v1/product-research/hot-products/search',
      {},
      { aiPresentationId: 'presentation-research' },
    )

    expect(result.run).toEqual(expect.objectContaining({
      runId: 'run-current',
      searchMode: 'target_only',
      createdAt: '2026-07-30T10:00:00Z',
    }))
    expect(result.items[0]).toEqual(expect.objectContaining({
      imageUrl: 'https://current.example/image.jpg',
      sourceUrl: 'https://current.example/item',
      marketId: 'mercadolibre-mx',
      reviewCount: 12,
      hotScore: 8.5,
    }))
    expect(result.sourceStatus).toHaveLength(1)
    expect(result.sourceStatus[0]).toEqual(expect.objectContaining({
      sourceId: 'provider-current',
      itemsFound: 1,
    }))
  })

  it('读取当前配置并继续用 snake_case 写回', async () => {
    const backendConfig = {
      search_providers: [{
        id: 'provider-current',
        source_id: 'provider-legacy',
        name: '当前 Provider',
        source_type: 'api',
        sourceType: 'legacy-type',
        platform: 'mercadolibre',
        enabled: true,
        priority: 5,
        supported_markets: ['mercadolibre-mx'],
        supportedMarkets: ['legacy-market'],
        supported_languages: ['es-MX'],
        supported_data_types: ['hot_products'],
        auth_required: false,
        rate_limit_per_minute: 30,
        compliance_note: 'current',
        config_json: {
          provider_strategy: 'configured_api',
        },
        configJson: {
          provider_strategy: 'legacy',
        },
      }],
      target_markets: [{
        id: 'mercadolibre-mx',
        market: 'legacy-market',
        code: 'legacy-code',
        platform: 'mercadolibre',
        site: 'mlm',
        display_name: 'Mercado Libre México',
        displayName: 'Legacy Name',
        search_methods: [{
          method_id: 'provider-current',
          providerId: 'provider-legacy',
          enabled: true,
          prompt: 'current prompt',
          config_json: {},
        }],
        searchMethods: [{
          methodId: 'legacy-method',
        }],
      }],
      source_registry: [{
        id: 'provider-current',
        name: '当前 Provider',
        source_type: 'api',
        platform: 'mercadolibre',
        enabled: true,
        priority: 5,
        supported_markets: ['mercadolibre-mx'],
        supported_languages: ['es-MX'],
        supported_data_types: ['hot_products'],
        auth_required: false,
        rate_limit_per_minute: 30,
        compliance_note: 'current',
        config_json: {
          provider_strategy: 'configured_api',
        },
      }],
    }
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { ok: true, config: backendConfig },
    })

    const config = await fetchProductResearchSettings()

    expect(config.searchProviders[0]).toEqual(expect.objectContaining({
      id: 'provider-current',
      sourceType: 'api',
      supportedMarkets: ['mercadolibre-mx'],
      providerStrategy: 'configured_api',
    }))
    expect(config.targetMarkets[0]).toEqual(expect.objectContaining({
      id: 'mercadolibre-mx',
      displayName: 'Mercado Libre México',
    }))
    expect(config.targetMarkets[0]?.searchMethods[0]?.methodId).toBe('provider-current')

    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { ok: true, config: backendConfig },
    })
    await saveProductResearchSettings(config)

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/v1/product-research/source-registry/save',
      {
        config: {
          search_providers: [expect.objectContaining({
            id: 'provider-current',
            source_type: 'api',
            supported_markets: ['mercadolibre-mx'],
            config_json: expect.objectContaining({
              provider_strategy: 'configured_api',
            }),
          })],
          target_markets: [expect.objectContaining({
            id: 'mercadolibre-mx',
            display_name: 'Mercado Libre México',
            search_methods: [expect.objectContaining({
              method_id: 'provider-current',
              config_json: {},
            })],
          })],
        },
      },
    )
  })

  it('不再从旧 camelCase 配置容器恢复数据', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        ok: true,
        config: {
          searchProviders: [{ id: 'legacy-provider' }],
          targetMarkets: [{ id: 'legacy-market' }],
          sourceRegistry: [{ id: 'legacy-registry' }],
        },
      },
    })

    const config = await fetchProductResearchSettings()

    expect(config.searchProviders).toEqual([])
    expect(config.targetMarkets).toEqual([])
    expect(config.sourceRegistry).toEqual([])
  })

  it('将 AI 搜索 Provider 测试关联到当前 presentation', async () => {
    const provider: ProductResearchSourceRegistryItem = {
      id: 'ai-web-search',
      name: 'AI 联网搜索',
      sourceType: 'ai',
      platform: 'amazon',
      enabled: true,
      priority: 1,
      supportedMarkets: ['amazon-us'],
      supportedLanguages: ['en-US'],
      supportedDataTypes: ['hot_products'],
      authRequired: false,
      rateLimitPerMinute: 10,
      complianceNote: '',
      providerStrategy: 'ai_web_search',
      configJson: {},
      raw: {},
    }
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        ok: true,
        status: 'success',
        source_id: provider.id,
        provider_strategy: provider.providerStrategy,
        market: 'amazon-us',
        keyword: 'mahjong gift',
        items_found: 1,
        duration_ms: 15,
        sample: {},
      },
    })

    const result = await testProductResearchSearchProvider(
      provider,
      { market: 'amazon-us', keyword: 'mahjong gift' },
      { presentationId: 'presentation-provider-test' },
    )

    expect(result.ok).toBe(true)
    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/v1/product-research/search-providers/test',
      expect.objectContaining({
        provider: expect.objectContaining({ id: provider.id }),
        options: { market: 'amazon-us', keyword: 'mahjong gift' },
      }),
      { aiPresentationId: 'presentation-provider-test' },
    )
  })
})
