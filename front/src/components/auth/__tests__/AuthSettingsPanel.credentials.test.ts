import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as settingsApi from '@/api/workflow/settings'
import { useAccountStore } from '@/stores/account'
import AuthSettingsPanel from '../AuthSettingsPanel.vue'
import { launchDesktop, receivedDesktopHandoff } from '@/utils/desktopHandoff'
vi.mock('@/utils/desktopHandoff', async importOriginal => ({ ...await importOriginal<object>(), launchDesktop: vi.fn(), acknowledgeDesktopHandoff: vi.fn() }))

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
        AlibabaSettingsPanel: true,
      },
    },
  })
}

const save = async (wrapper: ReturnType<typeof mountPanel>) => {
  await wrapper.findAll('button').find(button => button.text() === '保存 AI 设置')!.trigger('click')
  return wrapper.emitted('saveAi')!.at(-1)![0] as Record<string, unknown>
}

it('仅模型读取显示正在读取模型，其他工作台加载不会误报模型检测', async () => {
  const wrapper = mountPanel()
  await wrapper.setProps({ loading: true })
  expect(wrapper.text()).not.toContain('正在读取模型')
  await wrapper.setProps({ modelsReading: true })
  expect(wrapper.get('[role="status"]').text()).toBe('正在读取模型')
})

it('只在打开模型字段时查询目录，取回列表后由用户选择模型', async () => {
  const list = vi.spyOn(settingsApi, 'testAiModel').mockResolvedValue({
    ok: true, message: '', error: '', errorCode: '', nextAction: '',
    raw: { available_models: [{ id: 'model-b', name: '模型 B' }] },
  })
  try {
    const wrapper = mountPanel({
      providers: [{ id: 'openai', model_discovery: 'openai_models' }],
      ai_models: [{ id: 'demo', model: 'custom', provider_id: 'openai', base_url: 'https://example.test', api_key_configured: true }],
    })
    expect(wrapper.findAll('button').some(button => button.text() === '加载模型列表')).toBe(false)
    await wrapper.get('input[type="password"]').trigger('blur')
    await wrapper.get('input[placeholder="https://api.example.com/v1"]').trigger('blur')
    expect(list).not.toHaveBeenCalled()

    const field = wrapper.get('input[role="combobox"]')
    await field.trigger('click')
    await field.trigger('click')
    await flushPromises()
    expect(list).toHaveBeenCalledTimes(1)
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ id: 'demo', probe_capabilities: false }))
    expect((field.element as HTMLInputElement).value).toBe('custom')
    await wrapper.get('[role="option"]').trigger('click')
    expect((field.element as HTMLInputElement).value).toBe('model-b')
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false)

    list.mockRejectedValueOnce(new Error('模型目录暂不可用'))
    await field.trigger('keydown', { key: 'ArrowDown' })
    await flushPromises()
    expect(wrapper.text()).toContain('模型目录暂不可用')
    await field.setValue('manual-id')
    await field.trigger('blur')
    expect((await save(wrapper)).ai_models).toEqual([expect.objectContaining({ model: 'manual-id' })])
  } finally {
    list.mockRestore()
  }
})

it('同一列表中仅系统模型保护连接与删除，个人模型可以编辑、保存和删除', async () => {
  const wrapper = mountPanel({
    ai_models: [
      { id: 'erp_newapi_text', name: 'dsflash', model: 'deepseek-flash', system_managed: true, provider_id: 'openai' },
      { id: 'erp_newapi_image', name: 'qwenflash', model: 'qwen-image-3.0', system_managed: true, provider_id: 'openai' },
      { id: 'personal', name: '我的模型', model: 'personal-chat', provider_id: 'openai' },
    ],
  })
  const button = (label: string) => wrapper.findAll('button').find(b => b.text().includes(label))!
  expect(button('添加模型').element.disabled).toBe(false)
  expect(button('删除当前模型').element.disabled).toBe(true)
  expect(wrapper.get('input[placeholder="当前账号托管密钥"]').attributes('disabled')).toBeDefined()
  expect(wrapper.get('input[placeholder="例如 DeepSeek 文本模型"]').attributes('disabled')).toBeUndefined()
  await button('我的模型').trigger('click')
  expect(button('删除当前模型').element.disabled).toBe(false)
  expect(wrapper.get('input[placeholder="API Key"]').attributes('disabled')).toBeUndefined()
  await button('删除当前模型').trigger('click')
  await button('保存 AI 设置').trigger('click')
  const payload = wrapper.emitted('saveAi')![0][0] as { ai_models: Record<string, unknown>[] }
  expect(payload.ai_models.map(m => m.id)).toEqual(['erp_newapi_text', 'erp_newapi_image'])
  expect(payload.ai_models[0]).not.toHaveProperty('api_key')
})

describe('AuthSettingsPanel 固定 Gemini Canvas 多账号与桌面交接', () => {
  beforeEach(() => vi.stubGlobal('championDesktop', { localOperations: true }))
  afterEach(() => vi.unstubAllGlobals())
  const fixedCanvas = {
    id: 'gemini_canvas_browser', name: 'Gemini Canvas 修图', connection_type: 'browser',
    browser_provider: 'gemini', browser_mode: 'managed_profile', browser_profile: 'canvas',
    browser_port: '', browser_proxy: '', browser_url: 'https://gemini.google.com/share/example',
    canvas_url: 'https://gemini.google.com/share/example',
    provider: 'Gemini Canvas', model: '', capabilities: ['image_edit'], enabled: true, canvas_managed: true,
  }
  const preset = {
    id: 'gemini_canvas', label: 'Gemini Canvas', description: '通过 Gemini 网页修改图片',
    supported_capabilities: ['image_edit'], model: fixedCanvas,
  }
  const apiModel = { id: 'existing', name: '已有模型', connection_type: 'api', provider_id: 'openai', capabilities: [] }

  it('左侧固定展示 Gemini Canvas（不可删除、不可复制），展示登录账号并支持取消登录与代理配置', async () => {
    const logoutSpy = vi.spyOn(settingsApi, 'logoutCanvasAccount').mockResolvedValue({
      ok: true,
      accounts: [{ id: 'acct_b', profile: 'acct_b', email: 'bob@gmail.com', enabled: true, status: 'signed_in' }],
      canvasStatus: {
        scope: 'desktop', desktop_online: true,
        accounts: [{ id: 'acct_b', profile: 'acct_b', email: 'bob@gmail.com', enabled: true, status: 'signed_in' }],
      },
    })
    const toggleSpy = vi.spyOn(settingsApi, 'toggleCanvasAccount').mockResolvedValue({
      ok: true,
      accounts: [{ id: 'acct_b', profile: 'acct_b', email: 'bob@gmail.com', enabled: false, status: 'signed_in' }],
      canvasStatus: {
        scope: 'desktop', desktop_online: true,
        accounts: [{ id: 'acct_b', profile: 'acct_b', email: 'bob@gmail.com', enabled: false, status: 'signed_in' }],
      },
    })
    try {
      const wrapper = mountPanel({
        ai_models: [apiModel, fixedCanvas],
        canvas_status: {
          scope: 'desktop', desktop_online: true,
          accounts: [
            { id: 'acct_a', profile: 'acct_a', email: 'alice@gmail.com', name: 'Alice', enabled: true, status: 'signed_in' },
            { id: 'acct_b', profile: 'acct_b', email: 'bob@gmail.com', name: 'Bob', enabled: true, status: 'signed_in' },
          ],
        },
        ai_use_cases: [{ id: 'image.translate', label: '图片翻译', required_capabilities: ['image_edit'], desktop_capability: 'image_edit' }],
      })
      expect(wrapper.find('[data-testid="add-web-model"]').exists()).toBe(false)
      const canvasTab = wrapper.get('[data-testid="model-tab-gemini-canvas"]')
      expect(canvasTab.text()).toContain('已登录 2 / 2 个账号')
      await canvasTab.trigger('click')
      const btn = (label: string) => wrapper.findAll('button').find(b => b.text() === label)!
      expect(btn('删除当前模型').element.disabled).toBe(true)
      expect(btn('复制当前模型').element.disabled).toBe(true)
      expect(wrapper.get('[data-testid="gemini-canvas-panel"]').text()).toContain('alice@gmail.com')
      expect(wrapper.get('[data-testid="gemini-canvas-panel"]').text()).toContain('bob@gmail.com')
      expect(wrapper.get('[data-testid="canvas-managed-url"]').text()).toBe('https://gemini.google.com/share/example')
      expect(wrapper.find('[data-testid="canvas-open-managed-settings"]').exists()).toBe(false)

      await wrapper.get('[data-testid="canvas-logout-acct_a"]').trigger('click')
      await flushPromises()
      expect(logoutSpy).toHaveBeenCalledWith('acct_a')
      const remainingRows = wrapper.findAll('[data-testid="canvas-account-row"]')
      expect(remainingRows).toHaveLength(1)
      expect(remainingRows[0].text()).toContain('bob@gmail.com')
      expect(remainingRows[0].text()).not.toContain('alice@gmail.com')
      expect(wrapper.get('[data-testid="gemini-canvas-panel"]').text()).toContain('已取消登录并清除本机会话：alice@gmail.com')
      expect(canvasTab.text()).toContain('已登录 1 / 1 个账号')

      await wrapper.get('input[aria-label="启用 bob@gmail.com"]').setValue(false)
      await flushPromises()
      expect(toggleSpy).toHaveBeenCalledWith('acct_b', false)

      await wrapper.get('[data-testid="canvas-browser-proxy"]').setValue('http://127.0.0.1:7890')
      const payload = await save(wrapper)
      const savedCanvas = (payload.ai_models as Record<string, unknown>[]).find(m => m.id === 'gemini_canvas_browser')!
      expect(savedCanvas).toMatchObject({
        canvas_managed: true, browser_provider: 'gemini', browser_proxy: 'http://127.0.0.1:7890', capabilities: ['image_edit'],
      })
      expect(savedCanvas).not.toHaveProperty('canvas_url')
    } finally {
      logoutSpy.mockRestore()
      toggleSpy.mockRestore()
    }
  })

  it('网页端同样展示固定 Gemini Canvas 与在线桌面账号，点击配置或登录直接唤起桌面端', async () => {
    vi.stubGlobal('championDesktop', undefined)
    vi.mocked(launchDesktop).mockClear()
    const wrapper = mountPanel({
      ai_models: [apiModel, fixedCanvas],
      canvas_status: {
        scope: 'web_delegated', desktop_online: true,
        accounts: [{ id: 'canvas', profile: 'canvas', email: 'web-view@gmail.com', enabled: true, status: 'signed_in' }],
      },
      ai_use_cases: [{ id: 'image.translate', label: '图片翻译', required_capabilities: ['image_edit'], desktop_capability: 'image_edit' }],
    })
    await wrapper.get('[data-testid="model-tab-gemini-canvas"]').trigger('click')
    expect(wrapper.get('[data-testid="canvas-web-handoff-banner"]').text()).toContain('在线桌面已连接')
    expect(wrapper.get('[data-testid="gemini-canvas-panel"]').text()).toContain('web-view@gmail.com')
    expect(launchDesktop).not.toHaveBeenCalled()

    await wrapper.get('[data-testid="canvas-add-account"]').trigger('click')
    expect(launchDesktop).toHaveBeenCalledWith(expect.objectContaining({
      target: '/?tab=auth&auth_section=ai_models',
      state: { modelSettings: { selectedModelId: 'gemini_canvas_browser' } },
    }))
    await wrapper.get('[data-testid="canvas-logout-canvas"]').trigger('click')
    expect(launchDesktop).toHaveBeenCalledTimes(2)
    await wrapper.get('[data-testid="canvas-browser-proxy"]').trigger('click')
    expect(launchDesktop).toHaveBeenCalledTimes(3)
    wrapper.unmount()
  })

  it('只有改绑到本机模型时才交接，桌面恢复待保存的绑定', async () => {
    vi.stubGlobal('championDesktop', undefined)
    vi.mocked(launchDesktop).mockClear()
    const config = {
      ai_models: [apiModel, fixedCanvas],
      ai_use_cases: [{ id: 'image.translate', label: '图片翻译', required_capabilities: ['image_edit'], desktop_capability: 'image_edit' }],
    }
    const web = mountPanel(config)
    await web.get('[data-testid="auth-settings-tab-ai_bindings"]').trigger('click')
    const fields = web.get('[data-testid="save-ai-bindings"]').element.closest('section')!
    const select = web.findAll('select').find(item => fields.contains(item.element))!
    await select.setValue(fixedCanvas.id)
    await web.get('[data-testid="save-ai-bindings"]').trigger('click')
    const context = vi.mocked(launchDesktop).mock.calls[0][0]!
    expect(context.state?.modelSettings?.bindings).toMatchObject({ 'image.translate': { model_id: fixedCanvas.id } })
    expect(web.emitted('saveAi')).toBeUndefined()
    web.unmount()

    vi.stubGlobal('championDesktop', { localOperations: true })
    const desktop = mountPanel(config)
    try {
      receivedDesktopHandoff.value = { requestId: 'binding', target: context.target!, state: context.state! }
      await flushPromises()
      expect(desktop.get('[data-testid="auth-settings-tab-ai_bindings"]').attributes('aria-selected')).toBe('true')
      await desktop.get('[data-testid="save-ai-bindings"]').trigger('click')
      expect(desktop.emitted('saveAi')?.[0][0]).toEqual({ ai_use_case_bindings: context.state?.modelSettings?.bindings })
      expect(launchDesktop).toHaveBeenCalledOnce()
    } finally {
      receivedDesktopHandoff.value = null
      desktop.unmount()
    }
  })

  it('手动切换网页模式必须选择服务商，已保存目录外服务商保持可读取', async () => {
    const wrapper = mountPanel({ ai_models: [apiModel], browser_presets: [preset] })
    await wrapper.get('[data-testid="ai-connection-type"]').setValue('browser')
    await wrapper.findAll('button').find(button => button.text() === '保存 AI 设置')!.trigger('click')
    expect(wrapper.emitted('saveAi')).toBeUndefined()
    expect(wrapper.get('[role="alert"]').text()).toContain('选择服务商')
    await wrapper.get('[data-testid="web-provider-id"]').setValue('gemini')
    const payload = await save(wrapper)
    expect((payload.ai_models as Record<string, unknown>[])[0]).toMatchObject({
      connection_type: 'browser', browser_provider: 'gemini', browser_url: preset.model.browser_url,
      browser_mode: 'managed_profile', browser_profile: 'canvas',
    })
  })

  it('单个模型自动保存回读时保留其他模型未提交的编辑', async () => {
    const wrapper = mountPanel({ ai_models: [apiModel, fixedCanvas], browser_presets: [preset] })
    await wrapper.get('input[placeholder="例如 DeepSeek 文本模型"]').setValue('尚未保存的名称')
    expect(wrapper.emitted('saveAi')).toBeUndefined()
    await wrapper.setProps({ aiConfig: { ai_models: [apiModel, { ...fixedCanvas, timeout_seconds: 240 }], browser_presets: [preset] } })
    expect((wrapper.get('input[placeholder="例如 DeepSeek 文本模型"]').element as HTMLInputElement).value).toBe('尚未保存的名称')
  })
})

describe('AuthSettingsPanel credential lifecycle', () => {
  it('1688 授权按角色展示卡片：普通用户只显示万邦，管理员显示全部三张卡', async () => {
    setActivePinia(createPinia())
    const account = useAccountStore()
    account.user = { id: 'u1', email: 'u@example.com', display_name: '普通用户', role: 'user', status: 'enabled', created_at: 0, updated_at: 0 }
    const userWrapper = mountPanel()
    await userWrapper.get('[data-testid="auth-settings-tab-alibaba"]').trigger('click')
    expect(userWrapper.findAll('alibaba-settings-panel-stub').map(card => card.attributes('provider')))
      .toEqual(['onebound'])
    expect(userWrapper.find('[data-testid="transient-1688-access-token"]').exists()).toBe(false)
    const payload = await save(userWrapper)
    expect(payload).not.toHaveProperty('1688_api')
    userWrapper.unmount()

    account.user = { id: 'a1', email: 'a@example.com', display_name: '管理员', role: 'admin', status: 'enabled', created_at: 0, updated_at: 0 }
    const adminWrapper = mountPanel()
    await adminWrapper.get('[data-testid="auth-settings-tab-alibaba"]').trigger('click')
    expect(adminWrapper.findAll('alibaba-settings-panel-stub').map(card => card.attributes('provider')))
      .toEqual(['alibaba', 'alibaba_crossborder', 'onebound'])
    adminWrapper.unmount()
  })

  it('异步配置尚未返回时可渲染空平台状态', async () => {
    const wrapper = mountPanel({}, [])

    expect(wrapper.text()).toContain('平台配置加载中')
    await wrapper.setProps({ platformOptions: defaultPlatformOptions })
    expect(wrapper.text()).toContain('Mercado Libre')
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
      { id: 'openai', label: 'OpenAI（官方 / 兼容协议）', provider_family: 'openai', default_base_url: 'https://api.openai.com/v1', default_api_style: 'openai_responses', supported_api_styles: ['openai_compatible', 'openai_responses'], model_discovery: 'openai_models' },
      { id: 'anthropic', label: 'Anthropic（Claude）', provider_family: 'anthropic', default_base_url: 'https://api.anthropic.com', default_api_style: 'anthropic_messages', supported_api_styles: ['anthropic_messages'], model_discovery: 'anthropic_models' },
      { id: 'google', label: 'Google Gemini', provider_family: 'google', default_base_url: 'https://generativelanguage.googleapis.com', default_api_style: 'google_gemini', supported_api_styles: ['google_gemini'], model_discovery: 'google_models' },
      { id: 'alibaba', label: '阿里云百炼 / Qwen', provider_family: 'alibaba', supported_api_styles: ['openai_compatible', 'openai_responses'], model_discovery: 'openai_models' },
      { id: 'omlx', label: 'oMLX（本地模型）', provider_family: 'omlx', default_base_url: 'http://127.0.0.1:8000/v1', default_api_style: 'openai_compatible', supported_api_styles: ['openai_compatible', 'openai_responses'], model_discovery: 'openai_models' },
    ],
  }

  it('将服务商预设与 API 协议作为两个独立维度展示', () => {
    const wrapper = mountPanel(qwenConfig)

    expect(wrapper.text()).toContain('服务商')
    expect(wrapper.text()).toContain('API 协议')
    expect(wrapper.text()).not.toContain('自定义服务')
    expect(wrapper.get('[data-testid="ai-provider-id"]').findAll('option').map((item) => item.text())).toEqual([
      '请选择服务商',
      'OpenAI（官方 / 兼容协议）',
      'Anthropic（Claude）',
      'Google Gemini',
      '阿里云百炼 / Qwen',
      'oMLX（本地模型）',
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

  it.each([
    ['chat', '文本', '简单问候'],
    ['json', 'JSON 模式', '返回一个 JSON 对象'],
    ['web_search', '联网搜索', '成都此刻的天气'],
    ['tool_calling', 'Function Call', '调用测试工具'],
    ['image_understanding', '图片理解', '两张测试图片'],
    ['image_edit', '图像编辑', '红色改为蓝色'],
  ])('%s 测试展示固定说明，只提交能力选择', async (capability, label, description) => {
    const wrapper = mountPanel({
      ...qwenConfig,
      ai_models: [{ ...qwenConfig.ai_models[0], capabilities: [] }],
    })
    const toggle = wrapper.findAll('label').find(item => item.text().trim() === label)!.get('input')
    await toggle.setValue(true)

    const dialog = wrapper.get('[data-testid="capability-probe-dialog"]')
    expect(dialog.get('[data-testid="capability-probe-description"]').text()).toContain(description)
    expect(dialog.find('textarea, input, [contenteditable="true"]').exists()).toBe(false)
    expect(wrapper.emitted('testAi')).toBeUndefined()

    await dialog.findAll('button').find(button => button.text() === '确定测试')!.trigger('click')
    const payload = wrapper.emitted('testAi')!.at(-1)![0] as Record<string, unknown>
    expect(payload).toMatchObject({
      probe_only_capability: capability,
      probe_capabilities: true,
      test_trigger: 'capability_checkbox',
      capabilities: [],
    })
    expect(payload).not.toHaveProperty('probe_messages')
    expect(payload).not.toHaveProperty('probe_image_prompt')
    expect(dialog.findAll('button').every(button => button.attributes('disabled') !== undefined)).toBe(true)
    wrapper.unmount()
  })

  it('图片理解通过双图测试后才可用于套图规划', async () => {
    const wrapper = mountPanel({
      ...qwenConfig,
      ai_use_cases: [{ id: 'image.plan', label: '套图规划', required_capabilities: ['chat', 'json', 'image_understanding'] }],
      ai_use_case_prompts: { 'image.plan': { path: 'config/prompts/image_set_plan.json' } },
    })
    const toggle = wrapper.findAll('label').find(label => label.text().trim() === '图片理解')!.get('input')
    const select = wrapper.findAll('select').find(item => item.findAll('option').some(option => option.text() === '自动匹配'))!
    expect(select.find('option[value="qwen_translation"]').exists()).toBe(false)
    await toggle.setValue(true)
    expect(wrapper.text()).toContain('两张测试图片')
    await wrapper.findAll('button').find(button => button.text() === '确定测试')!.trigger('click')
    expect(wrapper.emitted('testAi')?.at(-1)?.[0]).toMatchObject({
      probe_only_capability: 'image_understanding', capabilities: ['chat', 'json'],
    })
    await wrapper.setProps({ lastResult: {
      ok: true, message: '', error: '', errorCode: '', nextAction: '',
      raw: {
        channel: 'ai_model', model_id: 'qwen_translation', test_trigger: 'capability_checkbox',
        supported_capabilities: ['image_understanding'],
        capability_results: { image_understanding: { ok: true, capability_profile: { tested: true, version: 2 } } },
      },
    } })
    expect(select.find('option[value="qwen_translation"]').exists()).toBe(true)
    expect((await save(wrapper)).ai_models).toEqual([expect.objectContaining({
      capability_profiles: { image_understanding: expect.objectContaining({ tested: true }) },
    })])
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

  it('切换为 Anthropic 或 Google Gemini 时自动匹配原生协议并开放 Thinking', async () => {
    const wrapper = mountPanel(qwenConfig)

    await wrapper.get('[data-testid="ai-provider-id"]').setValue('anthropic')
    expect(wrapper.get('[data-testid="ai-api-style"]').findAll('option').map((item) => item.text())).toEqual([
      'Anthropic Messages',
    ])
    expect((wrapper.get('[data-testid="ai-api-style"]').element as HTMLSelectElement).value).toBe('anthropic_messages')
    expect(wrapper.get('[data-testid="ai-model-thinking"]').attributes('disabled')).toBeUndefined()

    await wrapper.get('[data-testid="ai-provider-id"]').setValue('google')
    expect(wrapper.get('[data-testid="ai-api-style"]').findAll('option').map((item) => item.text())).toEqual([
      'Google Gemini',
    ])
    expect((wrapper.get('[data-testid="ai-api-style"]').element as HTMLSelectElement).value).toBe('google_gemini')
    expect(wrapper.get('[data-testid="ai-model-thinking"]').attributes('disabled')).toBeUndefined()
  })

  it('已保存的 DeepSeek 旧配置继续展示并开放 Thinking 勾选框与推理参数', async () => {
    const deepseekConfig = JSON.parse(JSON.stringify(qwenConfig))
    deepseekConfig.ai_models[0].provider_id = 'deepseek'
    deepseekConfig.ai_models[0].api_style = 'openai_compatible'
    delete deepseekConfig.ai_models[0].generation_capabilities
    const wrapper = mountPanel(deepseekConfig)

    expect(wrapper.get('[data-testid="ai-provider-id"]').findAll('option').map((item) => item.text())).toContain('DeepSeek（已保存配置）')
    await wrapper.get('[data-testid="auth-settings-tab-ai_bindings"]').trigger('click')
    expect(wrapper.get('[data-testid="generation-reasoning-mode-text.translate"]').attributes('disabled')).toBeUndefined()

    await wrapper.get('[data-testid="auth-settings-tab-ai_models"]').trigger('click')
    const thinkingToggle = wrapper.get('[data-testid="ai-model-thinking"]')
    expect(thinkingToggle.attributes('disabled')).toBeUndefined()
    await thinkingToggle.setValue(true)
    const saved = await save(wrapper)
    expect((saved.ai_models as Array<Record<string, unknown>>)[0].thinking_enabled).toBe(true)
  })

  it('未选择服务商时禁用 Thinking 勾选框和功能绑定推理参数', async () => {
    const unknownConfig = JSON.parse(JSON.stringify(qwenConfig))
    unknownConfig.ai_models[0].provider_id = ''
    delete unknownConfig.ai_models[0].generation_capabilities
    const wrapper = mountPanel(unknownConfig)

    expect(wrapper.get('[data-testid="ai-model-thinking"]').attributes('disabled')).toBeDefined()
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
