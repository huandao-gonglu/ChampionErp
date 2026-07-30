import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import AuthSettingsPanel from '../AuthSettingsPanel.vue'

function mountPanel() {
  return mount(AuthSettingsPanel, {
    props: {
      appConfig: {
        pricing_defaults: {},
        '1688_api': {
          app_key: 'mask...-key',
          app_secret: 'mask...cret',
          access_token: 'mask...oken',
          masked_app_key: 'mask...-key',
          masked_app_secret: 'mask...cret',
          masked_access_token: 'mask...oken',
          status: '已配置',
          base_url: 'https://example.test/1688',
          method: 'alibaba.product.get',
          api_version: '1.0',
          timeout_seconds: '20',
        },
        yunexpress: {
          app_id: 'mask...p-id',
          app_secret: 'mask...cret',
          source_key: 'mask...-key',
          masked_app_id: 'mask...p-id',
          masked_app_secret: 'mask...cret',
          masked_source_key: 'mask...-key',
          status: '已配置',
          environment: 'sandbox',
          base_url: 'https://example.test/yunexpress',
        },
      },
      aiConfig: {},
      storeConfig: {},
      storeAuthSummary: {},
      platformOptions: [
        {
          key: 'mercadolibre',
          label: 'Mercado Libre',
          sites: [],
        },
      ],
      mercadolibreChecklist: null,
      lastResult: null,
      authLink: '',
      loading: false,
    },
    global: {
      stubs: {
        ProductResearchSettingsPanel: true,
      },
    },
  })
}

describe('AuthSettingsPanel credential lifecycle', () => {
  it('does not replay public masks when testing saved 1688 credentials', async () => {
    const wrapper = mountPanel()

    await wrapper.get('[data-testid="test-1688-api"]').trigger('click')

    const event = wrapper.emitted('testApi')?.[0]
    expect(event?.[0]).toBe('1688')
    expect(event?.[1]).toEqual(expect.objectContaining({
      app_key: '',
      app_secret: '',
      access_token: '',
    }))
    expect(JSON.stringify(event?.[1])).not.toContain('mask...cret')
  })

  it('clears explicit 1688 and YunExpress credentials after each request', async () => {
    const wrapper = mountPanel()
    const appKey = wrapper.get('[data-testid="transient-1688-app-key"]')
    const appSecret = wrapper.get('[data-testid="transient-1688-app-secret"]')
    const sourceKey = wrapper.get('[data-testid="transient-yunexpress-source-key"]')
    const yunAppId = wrapper.get('[data-testid="transient-yunexpress-app-id"]')
    const yunAppSecret = wrapper.get('[data-testid="transient-yunexpress-app-secret"]')

    await appKey.setValue('request-1688-app-key')
    await appSecret.setValue('request-1688-app-secret')
    await wrapper.get('[data-testid="test-1688-api"]').trigger('click')
    expect(wrapper.emitted('testApi')?.[0]?.[1]).toEqual(expect.objectContaining({
      app_key: 'request-1688-app-key',
      app_secret: 'request-1688-app-secret',
    }))
    expect((appKey.element as HTMLInputElement).value).toBe('')
    expect((appSecret.element as HTMLInputElement).value).toBe('')

    await yunAppId.setValue('request-yun-app-id')
    await yunAppSecret.setValue('request-yun-app-secret')
    await sourceKey.setValue('request-yun-source-key')
    await wrapper.get('[data-testid="test-yunexpress-api"]').trigger('click')
    expect(wrapper.emitted('testApi')?.[1]?.[1]).toEqual(expect.objectContaining({
      app_id: 'request-yun-app-id',
      app_secret: 'request-yun-app-secret',
      source_key: 'request-yun-source-key',
    }))
    expect((yunAppId.element as HTMLInputElement).value).toBe('')
    expect((yunAppSecret.element as HTMLInputElement).value).toBe('')
    expect((sourceKey.element as HTMLInputElement).value).toBe('')
  })
})
