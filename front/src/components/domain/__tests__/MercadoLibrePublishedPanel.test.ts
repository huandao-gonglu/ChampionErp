// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import MercadoLibrePublishedPanel from '@/components/domain/MercadoLibrePublishedPanel.vue'
import type { MercadoLibreUserProduct } from '@/types/workflow'

const userProduct: MercadoLibreUserProduct = {
  productId: 'product-1',
  draftId: 'draft-1',
  sitelessUserProductId: 'UP-SITELESS-1',
  sitelessFamilyId: 'FAMILY-1',
  parentItemId: 'CBT-PARENT-1',
  parentUserProductId: 'UP-PARENT-1',
  sellerId: 'seller-global',
  status: 'partial',
  familyName: '测试商品 Family',
  title: '测试商品',
  thumbnail: '',
  model: 'MODEL-1',
  accountUserId: 'account-user-1',
  confirmedPayload: {},
  error: '',
  lastOperation: {},
  updatedAt: '2026-08-26T10:00:00Z',
  markets: [
    {
      siteId: 'MLM',
      itemId: 'MLM-ITEM-1',
      userProductId: 'UP-MLM-1',
      sellerId: 'seller-mx',
      logisticType: 'remote',
      status: 'active',
      price: 399,
      netProceeds: null,
      freeShipping: null,
      saleTerms: [],
      currencyId: 'MXN',
      listingTypeId: 'gold_special',
      error: '',
      lastOperation: {},
      updatedAt: '2026-08-26T09:00:00Z',
    },
    {
      siteId: 'MLB',
      itemId: '',
      userProductId: 'UP-MLB-1',
      sellerId: 'seller-br',
      logisticType: 'fulfillment',
      status: 'failed',
      price: null,
      netProceeds: null,
      freeShipping: null,
      saleTerms: [],
      currencyId: 'BRL',
      listingTypeId: 'gold_special',
      error: '类目映射失败',
      lastOperation: {},
      updatedAt: '2026-08-26T10:00:00Z',
    },
  ],
  raw: {},
}

function mountPanel() {
  return mount(MercadoLibrePublishedPanel, {
    props: {
      userProducts: [userProduct],
      status: 'all',
      page: 1,
      perPage: 50,
      total: 1,
      totalPages: 1,
      refreshScope: 'identity_mapping_only',
      checkedAt: '2026-08-26T10:01:00Z',
      loading: false,
      error: '',
    },
  })
}

describe('MercadoLibrePublishedPanel', () => {
  it('按 Siteless User Product 展示多个市场投影与局部失败', () => {
    const wrapper = mountPanel()

    expect(wrapper.findAll('[data-testid="ml-user-product"]')).toHaveLength(1)
    expect(wrapper.findAll('[data-testid="ml-market-publication"]')).toHaveLength(2)
    expect(wrapper.text()).toContain('UP-SITELESS-1')
    expect(wrapper.text()).toContain('UP-MLM-1')
    expect(wrapper.text()).not.toContain('MLB-ITEM')
    expect(wrapper.text()).toContain('类目映射失败')
    expect(wrapper.get('[data-testid="ml-user-products-refresh-scope"]').text()).toContain('identity_mapping_only')
    expect(wrapper.get('[data-testid="ml-user-products-refresh-scope"]').text()).toContain('状态和价格仍来自本地 publication 快照')
    expect(wrapper.text()).toContain('映射检查于 2026-08-26T10:01:00Z')
    expect(wrapper.findAll('select')[0]!.findAll('option').map((option) => option.attributes('value'))).toContain('partial')
  })

  it('暂停操作明确作用于整个 Siteless User Product', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mountPanel()

    await wrapper.get('button.btn-secondary').trigger('click')

    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('整个 User Product'))
    expect(wrapper.emitted('pauseUserProduct')).toEqual([[userProduct]])
  })

  it('partial 筛选按第一页重新读取 User Products', async () => {
    const wrapper = mountPanel()

    await wrapper.findAll('select')[0]!.setValue('partial')

    expect(wrapper.emitted('refresh')).toEqual([['partial', 1, 50, false]])
  })

  it('只有身份映射对账按钮请求远端 refresh', async () => {
    const wrapper = mountPanel()
    const refreshButton = wrapper.findAll('button').find((button) => button.text() === '对账身份映射')

    expect(refreshButton).toBeDefined()
    await refreshButton!.trigger('click')

    expect(wrapper.emitted('refresh')).toEqual([['all', 1, 50, true]])
  })
})
