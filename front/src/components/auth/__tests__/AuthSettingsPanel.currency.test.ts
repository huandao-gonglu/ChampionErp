import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import AuthSettingsPanel from '../AuthSettingsPanel.vue'
import type { AuthResult } from '@/api/workflow/normalizers'

function mountPanel(options: {
  platform: string
  storeConfig?: Record<string, unknown>
  lastResult?: AuthResult | null
  platformOptions?: Array<{ key: string; label: string; sites: Array<{ key: string; code: string; label: string; language: string }> }>
}) {
  const wrapper = mount(AuthSettingsPanel, {
    props: {
      appConfig: {},
      aiConfig: {},
      storeConfig: options.storeConfig ?? {},
      storeAuthSummary: {},
      platformOptions: options.platformOptions ?? [{ key: options.platform, label: options.platform, sites: [] }],
      mercadolibreChecklist: null,
      lastResult: options.lastResult ?? null,
      authLink: '',
      loading: false,
    },
    global: {
      stubs: {
        ProductResearchSettingsPanel: true,
      },
    },
  })
  return wrapper
}

async function openStoresTab(wrapper: ReturnType<typeof mountPanel>, platform: string) {
  await wrapper.get('[data-testid="auth-settings-tab-stores"]').trigger('click')
  await wrapper.get('[data-testid="store-platform-select"]').setValue(platform)
}

describe('AuthSettingsPanel 发布货币区块', () => {
  it('单值锁定状态只读展示币种、来源与验证时间', async () => {
    const wrapper = mountPanel({
      platform: 'ozon',
      storeConfig: {
        ozon: {
          listing_currency: 'CNY',
          allowed_currencies: ['CNY'],
          currency_mode: 'locked',
          currency_status: 'ready',
          currency_source: 'account_api',
          currency_verified_at: '2026-08-23T12:00:00Z',
        },
      },
    })
    await openStoresTab(wrapper, 'ozon')

    const text = wrapper.text()
    expect(wrapper.get('[data-testid="store-currency-status"]').text()).toBe('已就绪')
    expect(wrapper.get('[data-testid="store-currency-value"]').text()).toBe('CNY')
    expect(wrapper.get('[data-testid="store-currency-source"]').text()).toBe('平台账户')
    expect(text).toContain('2026-08-23T12:00:00Z')
    expect(text).toContain('发布货币由平台账户锁定，不允许修改')
    expect(wrapper.find('[data-testid="store-currency-select"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="store-currency-manual"]').exists()).toBe(false)
  })

  it('多值待选状态使用允许集下拉框并要求选择', async () => {
    const wrapper = mountPanel({
      platform: 'mercadolibre',
      storeConfig: {
        mercadolibre: {
          listing_currency: '',
          allowed_currencies: ['USD', 'MXN'],
          currency_mode: 'selectable',
          currency_status: 'selection_required',
          currency_source: 'site_api',
          currency_verified_at: '2026-08-23T12:00:00Z',
        },
      },
    })
    await openStoresTab(wrapper, 'mercadolibre')

    expect(wrapper.get('[data-testid="store-currency-status"]').text()).toBe('请选择')
    expect(wrapper.text()).toContain('必须从允许币种中选择后才能核价与发布')
    const select = wrapper.get('[data-testid="store-currency-select"]')
    expect(select.findAll('option').map((option) => option.text())).toEqual([
      '请选择发布货币',
      'USD',
      'MXN',
    ])

    await select.setValue('MXN')
    await wrapper.get('[data-testid="store-currency-save"]').trigger('click')
    expect(wrapper.emitted('saveCurrency')?.[0]).toEqual(['mercadolibre', 'MXN'])
  })

  it('无查询能力时要求人工填写 ISO 4217 代码', async () => {
    const wrapper = mountPanel({
      platform: 'mercadolibre',
      storeConfig: {
        mercadolibre: {
          listing_currency: '',
          allowed_currencies: [],
          currency_mode: 'manual',
          currency_status: 'manual_required',
          currency_source: '',
          currency_verified_at: '',
        },
      },
    })
    await openStoresTab(wrapper, 'mercadolibre')

    expect(wrapper.get('[data-testid="store-currency-status"]').text()).toBe('需人工填写')
    expect(wrapper.text()).toContain('平台不提供币种查询能力，请人工填写')
    const input = wrapper.get('[data-testid="store-currency-manual"]')
    expect((input.element as HTMLInputElement).value).toBe('')

    await input.setValue('usd')
    await wrapper.get('[data-testid="store-currency-save"]').trigger('click')
    expect(wrapper.emitted('saveCurrency')?.[0]).toEqual(['mercadolibre', 'usd'])
  })

  it('读取失败状态阻断保存并提供重试入口', async () => {
    const wrapper = mountPanel({
      platform: 'yandex',
      storeConfig: {
        yandex: {
          listing_currency: 'RUB',
          allowed_currencies: ['RUB'],
          currency_mode: 'locked',
          currency_status: 'refresh_failed',
          currency_source: 'business_settings',
          currency_verified_at: '2026-08-23T10:00:00Z',
          currency_error_code: 'YANDEX_RATE_LIMITED',
          currency_error_message: 'Yandex 接口被限流',
        },
      },
    })
    await openStoresTab(wrapper, 'yandex')

    expect(wrapper.get('[data-testid="store-currency-status"]').text()).toBe('读取失败')
    expect(wrapper.text()).toContain('Yandex 接口被限流')
    expect(wrapper.text()).toContain('上次读取值仅供参考，核价与发布已阻断')

    await wrapper.get('[data-testid="store-currency-retry"]').trigger('click')
    // 重试针对已保存配置执行，不携带未保存表单副本。
    expect(wrapper.emitted('testAuth')?.[0]).toEqual(['yandex'])
  })

  it('未验证状态提示先完成授权', async () => {
    const wrapper = mountPanel({ platform: 'ozon', storeConfig: { ozon: {} } })
    await openStoresTab(wrapper, 'ozon')

    expect(wrapper.get('[data-testid="store-currency-status"]').text()).toBe('未验证')
    expect(wrapper.text()).toContain('请先测试授权并读取发布货币；未就绪前不能核价与发布')
  })

  it('preview 测试结果标注“预览，尚未保存”', async () => {
    const wrapper = mountPanel({
      platform: 'ozon',
      storeConfig: { ozon: {} },
      lastResult: {
        ok: true,
        message: '测试成功：授权可用。',
        error: '',
        errorCode: '',
        nextAction: '',
        raw: {
          ok: true,
          platform: 'ozon',
          preview: true,
          currency_configuration: {
            listing_currency: 'CNY',
            currency_status: 'ready',
            currency_mode: 'locked',
          },
        },
      },
    })
    await openStoresTab(wrapper, 'ozon')

    const preview = wrapper.get('[data-testid="store-currency-preview"]')
    expect(preview.text()).toContain('预览，尚未保存')
    expect(preview.text()).toContain('CNY')
    // 持久化状态仍为未验证。
    expect(wrapper.get('[data-testid="store-currency-status"]').text()).toBe('未验证')
    expect(wrapper.get('[data-testid="store-currency-value"]').text()).toBe('-')
  })

  it('授权测试按钮统一为“测试授权并读取发布货币”', async () => {
    for (const platform of ['mercadolibre', 'yandex', 'ozon']) {
      const wrapper = mountPanel({ platform })
      await openStoresTab(wrapper, platform)
      expect(wrapper.text()).toContain('测试授权并读取发布货币')
    }
  })

  it('CBT 授权展示官方契约币种来源与已同步子市场物流', async () => {
    const wrapper = mountPanel({
      platform: 'mercadolibre',
      platformOptions: [{
        key: 'mercadolibre',
        label: '美客多',
        sites: [
          { key: 'CBT', code: 'CBT', label: '全局', language: 'en-US' },
          { key: 'MLM', code: 'MLM', label: '墨西哥', language: 'es' },
          { key: 'MLB', code: 'MLB', label: '巴西', language: 'pt-BR' },
        ],
      }],
      storeConfig: {
        mercadolibre: {
          account_site_id: 'CBT',
          listing_currency: 'USD',
          allowed_currencies: ['USD'],
          currency_mode: 'locked',
          currency_status: 'ready',
          currency_source: 'global_selling_contract',
          marketplace_bindings: [
            { seller_id: 'seller-mx', site_id: 'MLM', logistic_type: 'remote', business_model: 'cross_border' },
            { seller_id: 'seller-br', site_id: 'MLB', logistic_type: 'fulfillment', business_model: 'CBT CN Fulfillment Managed', pricing_model: 'net_proceeds' },
            { seller_id: 'seller-global', site_id: 'CBT', logistic_type: 'remote' },
          ],
        },
      },
    })
    await openStoresTab(wrapper, 'mercadolibre')

    expect(wrapper.get('[data-testid="store-currency-source"]').text()).toBe('Global Selling 官方契约')
    const bindings = wrapper.findAll('[data-testid="ml-marketplace-binding"]')
    expect(bindings).toHaveLength(2)
    expect(bindings[0].text()).toContain('墨西哥（MLM）')
    expect(bindings[0].text()).toContain('物流 remote')
    expect(bindings[1].text()).toContain('巴西（MLB）')
    expect(bindings[1].text()).toContain('物流 fulfillment')
    expect(bindings[1].text()).toContain('CBT CN Fulfillment Managed')
    expect(wrapper.get('[data-testid="ml-fully-managed-warning"]').text()).toContain('标准售价与销售目的地流程已阻断')
    expect(bindings.map((binding) => binding.text()).join(' ')).not.toContain('seller-global')
  })
})
