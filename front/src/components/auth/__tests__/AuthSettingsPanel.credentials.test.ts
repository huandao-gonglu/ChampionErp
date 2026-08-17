import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import AuthSettingsPanel from '../AuthSettingsPanel.vue'

const defaultPlatformOptions = [
  {
    key: 'mercadolibre',
    label: 'Mercado Libre',
    sites: [],
  },
]

function mountPanel(aiConfig = {}, platformOptions = defaultPlatformOptions, storeConfig: Record<string, unknown> = {}) {
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
      aiConfig,
      storeConfig,
      storeAuthSummary: {},
      platformOptions,
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
  it('异步配置尚未返回时可渲染空平台状态', async () => {
    const wrapper = mountPanel({}, [])

    expect(wrapper.text()).toContain('平台配置加载中')
    await wrapper.setProps({ platformOptions: defaultPlatformOptions })
    expect(wrapper.text()).toContain('Mercado Libre')
  })

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

  it('Ozon 授权测试直接提交尚未保存的表单凭据', async () => {
    const wrapper = mountPanel({}, [{ key: 'ozon', label: 'Ozon', sites: [] }])

    await wrapper.get('[data-testid="auth-settings-tab-stores"]').trigger('click')
    await wrapper.get('[data-testid="store-platform-select"]').setValue('ozon')
    await wrapper.get('[data-testid="ozon-client-id"]').setValue('unsaved-client')
    await wrapper.get('[data-testid="ozon-api-key"]').setValue('unsaved-api-key')
    await wrapper.get('[data-testid="test-ozon-auth"]').trigger('click')

    expect(wrapper.emitted('testAuth')?.[0]).toEqual([
      'ozon',
      '',
      {
        ozon: {
          client_id: 'unsaved-client',
          api_key: 'unsaved-api-key',
        },
      },
    ])
  })

  it('Yandex 授权测试提交未保存的 Token 与 Campaign ID', async () => {
    const wrapper = mountPanel({}, [{ key: 'yandex', label: 'Yandex', sites: [] }])

    await wrapper.get('[data-testid="auth-settings-tab-stores"]').trigger('click')
    await wrapper.get('[data-testid="store-platform-select"]').setValue('yandex')
    expect(wrapper.text()).not.toContain('在线授权校验将在接入对应 API 后启用')

    await wrapper.get('[data-testid="yandex-api-token"]').setValue('unsaved-token')
    await wrapper.get('[data-testid="yandex-campaign-id"]').setValue('123456')
    await wrapper.get('[data-testid="test-yandex-auth"]').trigger('click')

    expect(wrapper.emitted('testAuth')?.[0]).toEqual([
      'yandex',
      '',
      {
        yandex: {
          api_token: 'unsaved-token',
          campaign_id: '123456',
        },
      },
    ])
  })

  it('Yandex Campaign ID 输入提示区分 Campaign ID 与 Business ID', async () => {
    const wrapper = mountPanel({}, [{ key: 'yandex', label: 'Yandex', sites: [] }])

    await wrapper.get('[data-testid="auth-settings-tab-stores"]').trigger('click')
    await wrapper.get('[data-testid="store-platform-select"]').setValue('yandex')

    const hint = wrapper.get('[data-testid="yandex-campaign-id-hint"]')
    expect(hint.text()).toContain('请填写 Campaign ID（店铺 ID）')
    expect(hint.text()).toContain('不要填写 Business ID（柜台 ID）')
    expect(hint.text()).toContain('API 和模块')
  })

  it('Yandex 保存授权时一并提交 Campaign ID', async () => {
    const wrapper = mountPanel({}, [{ key: 'yandex', label: 'Yandex', sites: [] }])

    await wrapper.get('[data-testid="auth-settings-tab-stores"]').trigger('click')
    await wrapper.get('[data-testid="store-platform-select"]').setValue('yandex')
    await wrapper.get('[data-testid="yandex-api-token"]').setValue('saved-token')
    await wrapper.get('[data-testid="yandex-campaign-id"]').setValue('654321')

    const saveButton = wrapper.findAll('button').find((button) => button.text() === '保存当前平台授权')
    expect(saveButton).toBeDefined()
    await saveButton!.trigger('click')

    expect(wrapper.emitted('saveStore')?.[0]).toEqual([{
      yandex: { api_token: 'saved-token', campaign_id: '654321' },
    }])
  })

  it('Yandex 回填已保存 Campaign ID 并展示已验证店铺元数据', async () => {
    const wrapper = mountPanel({}, [{ key: 'yandex', label: 'Yandex', sites: [] }], {
      yandex: {
        api_token: 'tok...ken',
        campaign_id: '111',
        business_id: '222',
        business_name: 'Example Business',
        shop_name: 'example-shop.market',
        placement_type: 'FBS',
        api_availability: 'AVAILABLE',
        api_key_name: 'erp-publish-key',
        auth_scopes: ['marketplace:offers', 'marketplace:prices'],
        only_default_price: false,
        stock_update_mode: 'campaign_warehouses',
        warehouse_ids: [9, 7],
        capabilities_verified_at: '2026-08-17T00:00:00Z',
        auth_status: '测试成功',
      },
    })

    await wrapper.get('[data-testid="auth-settings-tab-stores"]').trigger('click')
    await wrapper.get('[data-testid="store-platform-select"]').setValue('yandex')

    expect((wrapper.get('[data-testid="yandex-campaign-id"]').element as HTMLInputElement).value).toBe('111')
    const text = wrapper.text()
    expect(text).toContain('已验证店铺信息')
    expect(text).toContain('222')
    expect(text).toContain('example-shop.market')
    expect(text).toContain('FBS')
    expect(text).toContain('AVAILABLE')
    expect(text).toContain('marketplace:offers、marketplace:prices')
    expect(text).toContain('仓库 9、7')
    expect(text).toContain('Campaign 级价格')
  })

})

describe('AuthSettingsPanel AI 功能绑定高级设置', () => {
  const qwenConfig = {
    ai_models: [
      {
        id: 'qwen_translation',
        name: 'Qwen 翻译',
        connection_type: 'api',
        provider: '阿里云百炼 / Qwen',
        provider_id: 'alibaba',
        api_style: 'openai_responses',
        model: 'qwen3.7-plus',
        capabilities: ['chat', 'json'],
        enabled: true,
        generation_capabilities: {
          status: 'supported',
          provider_id: 'alibaba',
          api_style: 'openai_responses',
          temperature: { status: 'supported' },
          max_output_tokens: { status: 'supported' },
          reasoning: {
            status: 'supported',
            modes: ['disabled', 'enabled'],
            efforts: ['minimal', 'low', 'medium', 'high', 'xhigh', 'max'],
            supports_budget_tokens: false,
            note: 'Responses 使用 reasoning.effort。',
          },
        },
      },
    ],
    ai_use_cases: [
      {
        id: 'text.translate',
        label: '翻译',
        required_capabilities: ['chat', 'json'],
      },
    ],
    ai_use_case_bindings: {
      'text.translate': { model_id: 'qwen_translation' },
    },
    ai_use_case_prompts: {
      'text.translate': { path: 'config/prompts/text_translate.json' },
    },
    providers: [
      { id: 'openai', label: 'OpenAI', provider_family: 'openai', default_base_url: 'https://api.openai.com/v1', default_api_style: 'openai_responses', supported_api_styles: ['openai_compatible', 'openai_responses'] },
      { id: 'deepseek', label: 'DeepSeek', provider_family: 'generic_openai', default_base_url: 'https://api.deepseek.com', default_api_style: 'openai_compatible', supported_api_styles: ['openai_compatible'], base_url_editable: false },
      { id: 'alibaba', label: '阿里云百炼 / Qwen', provider_family: 'alibaba', supported_api_styles: ['openai_compatible', 'openai_responses'] },
    ],
  }

  it('将服务商预设与 API 协议作为两个独立维度展示', () => {
    const wrapper = mountPanel(qwenConfig)

    expect(wrapper.text()).toContain('服务商')
    expect(wrapper.text()).toContain('API 协议')
    expect(wrapper.text()).not.toContain('自定义服务')
    expect(wrapper.get('[data-testid="ai-provider-id"]').findAll('option').map((item) => item.text())).toEqual([
      '请选择服务商',
      'OpenAI',
      'DeepSeek',
      '阿里云百炼 / Qwen',
    ])
    expect(wrapper.get('[data-testid="ai-api-style"]').findAll('option').map((item) => item.text())).toEqual([
      'Chat Completions',
      'Responses',
    ])
  })

  it('保存统一生成配置，不在前端拼接厂商字段', async () => {
    const wrapper = mountPanel(qwenConfig)
    await wrapper.get('[data-testid="auth-settings-tab-ai_bindings"]').trigger('click')

    await wrapper.get('[data-testid="generation-temperature-text.translate"]').setValue('0')
    await wrapper.get('[data-testid="generation-max-output-text.translate"]').setValue('3000')
    await wrapper.get('[data-testid="generation-reasoning-mode-text.translate"]').setValue('disabled')
    await wrapper.get('[data-testid="save-ai-bindings"]').trigger('click')

    const payload = wrapper.emitted('saveAi')?.[0]?.[0] as Record<string, unknown>
    const bindings = payload.ai_use_case_bindings as Record<string, Record<string, unknown>>
    expect(bindings['text.translate']).toEqual({
      model_id: 'qwen_translation',
      timeout_override_seconds: '',
      generation: {
        temperature: '0',
        max_output_tokens: '3000',
        reasoning: { mode: 'disabled' },
      },
    })
    expect(JSON.stringify(bindings)).not.toContain('enable_thinking')
    expect(JSON.stringify(bindings)).not.toContain('reasoning_effort')
  })

  it('模型配置只提交 provider_id，不再提交 provider_family', async () => {
    const wrapper = mountPanel(qwenConfig)
    await wrapper.get('[data-testid="ai-provider-id"]').setValue('openai')
    const saveButton = wrapper.findAll('button').find((button) => button.text() === '保存 AI 设置')
    expect(saveButton).toBeDefined()
    await saveButton!.trigger('click')

    const payload = wrapper.emitted('saveAi')?.[0]?.[0] as Record<string, unknown>
    const models = payload.ai_models as Array<Record<string, unknown>>
    expect(models[0].provider_id).toBe('openai')
    expect(models[0]).not.toHaveProperty('provider_family')
  })

  it('缺少 provider_id 的旧配置不会被强制归类到现有服务商', () => {
    const legacyConfig = JSON.parse(JSON.stringify(qwenConfig))
    legacyConfig.ai_models[0].provider = 'OpenAI-Compatible'
    delete legacyConfig.ai_models[0].provider_id
    legacyConfig.ai_models[0].base_url = 'https://proxy.example.invalid/v1'

    const wrapper = mountPanel(legacyConfig)

    expect((wrapper.get('[data-testid="ai-provider-id"]').element as HTMLSelectElement).value).toBe('')
    expect(wrapper.text()).toContain('未选择服务商')
    expect(wrapper.get('[data-testid="ai-provider-id"]').findAll('option').map((item) => item.text())).not.toContain('自定义服务')
  })

  it('能力探测不会在请求前伪造待测 capability', async () => {
    const wrapper = mountPanel(qwenConfig)
    const functionCallLabel = wrapper
      .findAll('label')
      .find((label) => label.text().includes('Function Call'))
    expect(functionCallLabel).toBeDefined()

    await functionCallLabel!.get('input[type="checkbox"]').setValue(true)
    const confirm = wrapper
      .findAll('button')
      .find((button) => button.text() === '确定测试')
    expect(confirm).toBeDefined()
    await confirm!.trigger('click')

    const events = wrapper.emitted('testAi') || []
    const payload = events.at(-1)?.[0] as Record<string, unknown>
    expect(payload.probe_only_capability).toBe('tool_calling')
    expect(payload.probe_capabilities).toBe(true)
    expect(payload.capabilities).toEqual(['chat', 'json'])
  })

  it('DeepSeek 未建模的推理参数不会被开放', async () => {
    const genericConfig = JSON.parse(JSON.stringify(qwenConfig))
    genericConfig.ai_models[0].provider_id = 'deepseek'
    genericConfig.ai_models[0].generation_capabilities = {
      ...genericConfig.ai_models[0].generation_capabilities,
      provider_id: 'deepseek',
      reasoning: { status: 'unknown', modes: [], efforts: [], supports_budget_tokens: false },
    }
    const wrapper = mountPanel(genericConfig)
    await wrapper.get('[data-testid="auth-settings-tab-ai_bindings"]').trigger('click')

    expect(wrapper.get('[data-testid="generation-reasoning-mode-text.translate"]').attributes('disabled')).toBeDefined()
  })

  it('继承模式下可直接选择推理强度，并自动切换为开启', async () => {
    const wrapper = mountPanel(qwenConfig)
    await wrapper.get('[data-testid="auth-settings-tab-ai_bindings"]').trigger('click')
    const effort = wrapper.get('[data-testid="generation-reasoning-effort-text.translate"]')

    expect(effort.attributes('disabled')).toBeUndefined()
    await effort.setValue('low')
    await wrapper.get('[data-testid="save-ai-bindings"]').trigger('click')

    const payload = wrapper.emitted('saveAi')?.[0]?.[0] as Record<string, unknown>
    const bindings = payload.ai_use_case_bindings as Record<string, Record<string, unknown>>
    expect(bindings['text.translate'].generation).toEqual({
      reasoning: { mode: 'enabled', effort: 'low' },
    })
  })
})
