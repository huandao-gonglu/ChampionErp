import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import SkuSourceManagerDialog from '../SkuSourceManagerDialog.vue'
import * as skuSourcesApi from '@/api/skuSources'

afterEach(() => vi.restoreAllMocks())

it('粘贴 1688 链接解析规格并绑定为默认采购源，同时支持切换默认与移除备选', async () => {
  const initialBinding = {
    id: 'bind-old',
    product_id: 'prod-1',
    draft_id: 'draft-1',
    sku_id: 'sku-1',
    seller_sku: 'SKU-DEMO-1',
    publication_id: 'job-1',
    origin: 'publish' as const,
    is_default: true,
    source: {
      supplier: '原厂家',
      source_platform: '1688',
      product_url: 'https://detail.1688.com/offer/111.html',
      source_sku_id: '1001',
      source_offer_id: '111',
      source_spec_id: 'spec-old',
      specification: '颜色：白色',
      sku_url: '',
      sku_url_verified: false,
    },
  }
  const newBinding = {
    id: 'bind-new',
    product_id: '',
    draft_id: '',
    sku_id: '',
    seller_sku: 'SKU-DEMO-1',
    publication_id: '',
    origin: 'alibaba_api' as const,
    is_default: true,
    source: {
      supplier: '新厂家',
      source_platform: '1688',
      product_url: 'https://detail.1688.com/offer/222.html',
      source_sku_id: '2001',
      source_offer_id: '222',
      source_spec_id: 'spec-new',
      specification: '颜色：黑色',
      sku_url: '',
      sku_url_verified: false,
    },
  }

  vi.spyOn(skuSourcesApi, 'querySkuSources').mockResolvedValueOnce([
    { platform: 'yandex', account_id: '1001', seller_sku: 'SKU-DEMO-1', default_binding_id: 'bind-old', bindings: [initialBinding] },
  ])
  const resolveSpy = vi.spyOn(skuSourcesApi, 'resolveAlibabaSource').mockResolvedValueOnce({
    ok: true,
    resolve_id: 'res-1',
    offer_id: '222',
    product_url: 'https://detail.1688.com/offer/222.html',
    title: '新货源商品',
    supplier: '新厂家',
    image_url: '',
    skus: [
      { source_sku_id: '2001', source_offer_id: '222', source_spec_id: 'spec-new', specification: '颜色：黑色', price: '15.0', stock: '200', image_url: '', can_auto_purchase: true, block_reason: '' },
      { source_sku_id: '2002', source_offer_id: '222', source_spec_id: '', specification: '缺 specId 规格', price: '15.0', stock: '0', image_url: '', can_auto_purchase: false, block_reason: '缺少 specId' },
    ],
  })
  const bindSpy = vi.spyOn(skuSourcesApi, 'bindSkuSource').mockResolvedValueOnce({
    ok: true,
    binding: newBinding,
    bindings: [newBinding, { ...initialBinding, is_default: false }],
  })
  const switchSpy = vi.spyOn(skuSourcesApi, 'setDefaultSkuSource').mockResolvedValueOnce({
    ok: true,
    binding: initialBinding,
    bindings: [initialBinding, { ...newBinding, is_default: false }],
  })
  const removeSpy = vi.spyOn(skuSourcesApi, 'removeSkuSource').mockResolvedValueOnce({
    ok: true,
    removed: true,
    bindings: [initialBinding],
  })

  const wrapper = mount(SkuSourceManagerDialog, {
    attachTo: document.body,
    props: {
      platform: 'yandex',
      accountId: '1001',
      sellerSku: 'SKU-DEMO-1',
      title: '测试商品',
      orderId: 'order-1',
      lineKey: 'line-1',
      revision: 0,
    },
  })
  try {
    await flushPromises()
    const body = new DOMWrapper(document.body)
    expect(body.text()).toContain('颜色：白色')
    expect(body.text()).toContain('默认货源')

    await body.get('[data-testid="sku-source-1688-url"]').setValue('https://detail.1688.com/offer/222.html')
    await body.get('form').trigger('submit')
    await flushPromises()
    expect(resolveSpy).toHaveBeenCalledWith('https://detail.1688.com/offer/222.html')
    expect(body.text()).toContain('新货源商品')
    expect(body.text()).toContain('缺少 specId')

    await body.get('[data-testid="sku-source-save-default"]').trigger('click')
    await flushPromises()
    expect(bindSpy).toHaveBeenCalledWith(expect.objectContaining({
      platform: 'yandex',
      seller_sku: 'SKU-DEMO-1',
      resolve_id: 'res-1',
      source_sku_id: '2001',
      set_default: true,
      order_id: 'order-1',
      line_key: 'line-1',
    }))
    expect(body.text()).toContain('颜色：黑色')
    expect(body.text()).toContain('备选货源')

    await body.get('[data-testid="sku-source-set-default"]').trigger('click')
    await flushPromises()
    expect(switchSpy).toHaveBeenCalledWith(expect.objectContaining({
      binding_id: 'bind-old',
      order_id: 'order-1',
      line_key: 'line-1',
    }))

    await body.findAll('[data-testid="sku-source-remove"]')[1].trigger('click')
    await flushPromises()
    expect(removeSpy).toHaveBeenCalledWith(expect.objectContaining({
      binding_id: 'bind-new',
    }))
    expect(body.text()).not.toContain('颜色：黑色')
  } finally {
    wrapper.unmount()
  }
})
