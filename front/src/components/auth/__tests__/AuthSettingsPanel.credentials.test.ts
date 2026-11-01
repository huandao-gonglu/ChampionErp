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

describe('AuthSettingsPanel 网页连接预置', () => {
  const preset = {
    id: 'gemini_canvas', label: 'Gemini Canvas', description: '通过 Gemini 网页修改图片',
    supported_capabilities: ['image_edit'],
    model: {
      id: 'gemini_canvas_browser', name: 'Gemini Canvas 修图', connection_type: 'browser',
      browser_provider: 'gemini', browser_mode: 'managed_profile', browser_profile: 'canvas',
      browser_port: '', browser_proxy: '', browser_url: 'https://gemini.google.com/share/example',
      provider: 'Gemini Canvas', model: '', capabilities: [], enabled: true,
    },
  }
  const apiModel = { id: 'existing', name: '已有模型', connection_type: 'api', provider_id: 'openai', capabilities: [] }
  const save = async (wrapper: ReturnType<typeof mountPanel>) => {
    await wrapper.findAll('button').find(button => button.text() === '保存 AI 设置')!.trigger('click')
    return wrapper.emitted('saveAi')!.at(-1)![0] as Record<string, unknown>
  }

  it('从网页入口添加 Gemini，删除后仍能按预置恢复，且不自动启用或测试能力', async () => {
    const wrapper = mountPanel({ ai_models: [apiModel], browser_presets: [preset] })
    await wrapper.get('[data-testid="add-web-model"]').setValue('gemini_canvas')
    expect(wrapper.get('[data-testid="web-model-guide"]').text()).toContain('通过 Gemini 网页修改图片')
    let payload = await save(wrapper)
    const added = (payload.ai_models as Record<string, unknown>[]).find(model => model.id === 'gemini_canvas_browser')!
    expect(added).toMatchObject({ ...preset.model, capabilities: [] })
    expect(added).not.toHaveProperty('capability_profiles')
    expect(wrapper.emitted('testAi')).toBeUndefined()
    await wrapper.findAll('button').find(button => button.text() === '删除当前模型')!.trigger('click')
    payload = await save(wrapper)
    expect((payload.ai_models as Record<string, unknown>[]).map(model => model.id)).toEqual(['existing'])
    await wrapper.get('[data-testid="add-web-model"]').setValue('gemini_canvas')
    payload = await save(wrapper)
    expect((payload.ai_models as Record<string, unknown>[]).find(model => model.id === 'gemini_canvas_browser')).toMatchObject(preset.model)
    expect(wrapper.get('[data-testid="add-web-model"]').findAll('option').map(option => option.text())).toEqual(['添加网页模型', 'Gemini Canvas'])
  })

  it('重复选择已添加的网页模型只定位现有配置，保留个人代理和资料目录', async () => {
    const personal = { ...preset.model, browser_proxy: 'http://private.test:8123', browser_profile: 'personal' }
    const wrapper = mountPanel({ ai_models: [apiModel, personal], browser_presets: [preset] })
    await wrapper.get('[data-testid="add-web-model"]').setValue('gemini_canvas')
    const payload = await save(wrapper)
    expect(payload.ai_models).toHaveLength(2)
    expect((payload.ai_models as Record<string, unknown>[])[1]).toMatchObject(personal)
  })

  it('网页服务商来自下拉目录，修改显示名称不改变适配器标识', async () => {
    const wrapper = mountPanel({ ai_models: [preset.model], browser_presets: [preset] })
    const selector = wrapper.get('[data-testid="web-provider-id"]')
    expect(selector.element.tagName).toBe('SELECT')
    expect(selector.findAll('option').map(option => option.attributes('value'))).toEqual(['', 'gemini'])
    await wrapper.get('input[placeholder="例如 DeepSeek 文本模型"]').setValue('我的图片助手')
    const payload = await save(wrapper)
    expect((payload.ai_models as Record<string, unknown>[])[0]).toMatchObject({
      name: '我的图片助手', browser_provider: 'gemini', browser_url: preset.model.browser_url,
    })
  })

  it('手动切换网页模式必须选择服务商，选择后自动带入对应配置', async () => {
    const wrapper = mountPanel({ ai_models: [apiModel], browser_presets: [preset] })
    await wrapper.get('[data-testid="ai-connection-type"]').setValue('browser')
    await wrapper.findAll('button').find(button => button.text() === '保存 AI 设置')!.trigger('click')
    expect(wrapper.emitted('saveAi')).toBeUndefined()
    expect(wrapper.get('[role="alert"]').text()).toContain('选择服务商')
    await wrapper.get('[data-testid="web-provider-id"]').setValue('gemini')
    const payload = await save(wrapper)
    expect((payload.ai_models as Record<string, unknown>[])[0]).toMatchObject({
      connection_type: 'browser', browser_provider: 'gemini', browser_url: preset.model.browser_url,
      browser_mode: 'managed_profile', browser_profile: 'canvas', capabilities: [],
    })
  })

  it('已保存的目录外服务商保持可读取，显式选择新服务商后清除旧能力证明', async () => {
    const previous = {
      ...preset.model, browser_provider: 'saved-service', browser_url: 'https://saved.example/app',
      browser_proxy: 'http://private.test:8123', capabilities: ['chat'],
      capability_profiles: { chat: { tested: true } },
    }
    const wrapper = mountPanel({ ai_models: [previous], browser_presets: [preset] })
    expect(wrapper.get('[data-testid="web-provider-id"] option[value="saved-service"]').attributes('disabled')).toBeDefined()
    let payload = await save(wrapper)
    expect((payload.ai_models as Record<string, unknown>[])[0]).toMatchObject(previous)
    await wrapper.get('[data-testid="web-provider-id"]').setValue('gemini')
    payload = await save(wrapper)
    expect((payload.ai_models as Record<string, unknown>[])[0]).toMatchObject({
      browser_provider: 'gemini', browser_url: preset.model.browser_url,
      browser_proxy: previous.browser_proxy, capabilities: [],
    })
    expect((payload.ai_models as Record<string, unknown>[])[0]).not.toHaveProperty('capability_profiles')
  })

  it('网页模型通过实际能力测试后才启用图像编辑', async () => {
    const wrapper = mountPanel({ ai_models: [apiModel], browser_presets: [preset] })
    await wrapper.get('[data-testid="add-web-model"]').setValue('gemini_canvas')
    await wrapper.get('[data-testid="test-web-capability-image_edit"]').trigger('click')
    expect(wrapper.emitted('testAi')).toBeUndefined()
    await wrapper.findAll('button').find(button => button.text() === '确定测试')!.trigger('click')
    expect(wrapper.emitted('testAi')?.[0]?.[0]).toMatchObject({
      id: 'gemini_canvas_browser', capabilities: [], probe_only_capability: 'image_edit',
    })
    await wrapper.setProps({ lastResult: {
      ok: true, message: '测试通过', error: '', errorCode: '', nextAction: '', raw: {
        channel: 'ai_model', model_id: 'gemini_canvas_browser', test_trigger: 'capability_checkbox',
        supported_capabilities: ['image_edit'], capability_results: {
          image_edit: { ok: true, capability_profile: { version: 2, tested: true } },
        },
      },
    } })
    const payload = await save(wrapper)
    expect((payload.ai_models as Record<string, unknown>[])[1]).toMatchObject({
      capabilities: ['image_edit'], capability_profiles: { image_edit: { tested: true } },
    })
  })
})

describe('AuthSettingsPanel credential lifecycle', () => {
  it('1688 独立授权只保存自身配置，采购查询测试保留长订单号并沿用保存的凭据', async () => {
    const wrapper = mountPanel()
    expect(wrapper.find('[data-testid="auth-settings-tab-interruptions"]').exists()).toBe(false)
    await wrapper.get('[data-testid="auth-settings-tab-alibaba"]').trigger('click')
    expect(wrapper.get('[data-testid="test-1688-order"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-testid="1688-test-order-number"]').setValue('3317081160226242182')
    await wrapper.get('[data-testid="test-1688-order"]').trigger('click')
    expect(wrapper.emitted('testApi')?.[0]).toEqual(['1688_order', expect.objectContaining({ app_key: '', app_secret: '', access_token: '' }), '3317081160226242182'])
    await wrapper.get('[data-testid="transient-1688-access-token"]').setValue('replacement-token')
    await wrapper.get('[data-testid="save-1688-settings"]').trigger('click')
    expect(wrapper.emitted('saveAi')?.[0]?.[0]).toEqual({ '1688_api': expect.objectContaining({ access_token: 'replacement-token' }) })
    expect((wrapper.get('[data-testid="transient-1688-access-token"]').element as HTMLInputElement).value).toBe('')
  })

  it('API 设置仅提供汇率与 1688，不再显示或提交云途配置', async () => {
    const wrapper = mountPanel()

    await wrapper.get('[data-testid="auth-settings-tab-apis"]').trigger('click')
    expect(wrapper.text()).toContain('核价汇率')
    expect(wrapper.text()).toContain('1688 采集 API')
    expect(wrapper.text()).not.toContain('云途')
    expect(wrapper.find('[data-testid="test-yunexpress-api"]').exists()).toBe(false)

    await wrapper.get('[data-testid="save-platform-api-settings"]').trigger('click')
    expect(wrapper.emitted('saveAi')?.[0]?.[0]).not.toHaveProperty('yunexpress')
  })

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

  it('每次请求后清空临时填写的 1688 凭据', async () => {
    const wrapper = mountPanel()
    const appKey = wrapper.get('[data-testid="transient-1688-app-key"]')
    const appSecret = wrapper.get('[data-testid="transient-1688-app-secret"]')

    await appKey.setValue('request-1688-app-key')
    await appSecret.setValue('request-1688-app-secret')
    await wrapper.get('[data-testid="test-1688-api"]').trigger('click')
    expect(wrapper.emitted('testApi')?.[0]?.[1]).toEqual(expect.objectContaining({
      app_key: 'request-1688-app-key',
      app_secret: 'request-1688-app-secret',
    }))
    expect((appKey.element as HTMLInputElement).value).toBe('')
    expect((appSecret.element as HTMLInputElement).value).toBe('')
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
