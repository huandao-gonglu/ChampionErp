import { describe, expect, it } from 'vitest'
import {
  MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED_MESSAGE,
  mercadoLibreBindingPricingMode,
  mercadoLibreHasFullyManagedBinding,
  mercadoLibreMarketplaceBindings,
  mercadoLibreSelectableBindings,
  mercadoLibreTargetPricingError,
} from '@/utils/mercadolibreGlobalSelling'

describe('Mercado Libre binding pricing_model', () => {
  it('平台返回的 listing_price 规范化为买家售价模式', () => {
    const storeConfig = {
      mercadolibre: {
        listing_model: 'traditional_global_items',
        marketplace_bindings: [{
          site_id: 'MLM',
          logistic_type: 'fulfillment',
          pricing_model: 'listing_price',
        }],
      },
    }
    const binding = mercadoLibreMarketplaceBindings(storeConfig)[0]!

    expect(mercadoLibreBindingPricingMode(binding, storeConfig)).toBe('price')
    expect(mercadoLibreSelectableBindings(storeConfig)).toHaveLength(1)
  })

  it('traditional 对 global_net_proceeds fail closed，User Products 才允许归一为 net_proceeds', () => {
    const traditionalConfig = {
      mercadolibre: {
        listing_model: 'traditional_global_items',
        marketplace_bindings: [{
          site_id: 'MLM',
          logistic_type: 'remote',
          pricing_model: 'global_net_proceeds',
        }],
      },
    }
    const userProductsConfig = {
      mercadolibre: {
        ...traditionalConfig.mercadolibre,
        listing_model: 'user_products',
      },
    }
    const binding = mercadoLibreMarketplaceBindings(traditionalConfig)[0]!

    expect(mercadoLibreBindingPricingMode(binding, traditionalConfig)).toBe('')
    expect(mercadoLibreSelectableBindings(traditionalConfig)).toEqual([])
    expect(mercadoLibreBindingPricingMode(binding, userProductsConfig)).toBe('net_proceeds')
    expect(mercadoLibreSelectableBindings(userProductsConfig)).toHaveLength(1)
  })

  it('Fully Managed 是 seller 级能力，traditional 也整体阻断标准流程', () => {
    const storeConfig = {
      mercadolibre: {
        listing_model: 'traditional_global_items',
        marketplace_bindings: [
          { site_id: 'MLM', logistic_type: 'remote', pricing_model: 'price', business_model: 'cross_border' },
          {
            site_id: 'MLB',
            logistic_type: 'fulfillment',
            pricing_model: 'global_net_proceeds',
            business_model: 'CBT CN Fulfillment Managed',
          },
        ],
      },
    }

    expect(mercadoLibreHasFullyManagedBinding(storeConfig)).toBe(true)
    expect(mercadoLibreSelectableBindings(storeConfig)).toEqual([])
    expect(mercadoLibreTargetPricingError({
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'en-US',
      listingCurrency: 'USD',
      sitesToSell: [{ siteId: 'MLM', logisticType: 'remote', price: '29.90' }],
    }, storeConfig)).toBe(MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED_MESSAGE)
  })
})
