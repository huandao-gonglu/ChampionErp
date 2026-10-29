// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import PricingExchangeRates from '../PricingExchangeRates.vue'
import PricingTemplates from '../PricingTemplates.vue'
import PricingFeesEditor from '../PricingFeesEditor.vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { createDefaultPricingInput } from '@/constants/initialState'
import { applyPricingTemplate, pricingTemplateParameters, type PricingTemplateSettings } from '@/types/pricing'
import * as api from '@/api/pricingWorkspace'

vi.mock('@/api/pricingWorkspace', () => ({ fetchPricingExchangeRates: vi.fn(), fetchPricingTemplates: vi.fn(), savePricingTemplate: vi.fn(), setDefaultPricingTemplate: vi.fn(), deletePricingTemplate: vi.fn() }))
enableAutoUnmount(afterEach)
function fixture() {
  const input = createDefaultPricingInput()
  input.targets = [{ targetKey: 'ozon:global', platform: 'ozon', site: 'global', listingCurrency: 'CNY', sitesToSell: [], paymentFeePercent: 1, otherFeePercent: 0, pricingMode: 'margin', targetMarginPercent: 30, markupPercent: 30, shippingQuoteMode: 'auto', shippingCurrency: 'CNY', shippingAmount: 88, manualPrice: null }]
  input.domesticFreightCny = 2
  input.packagingCostCny = 0.8
  return reactive(input)
}
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.fetchPricingExchangeRates).mockResolvedValue({ rates: { usd_cny_rate: 7.12, rub_cny_rate: 12.15, mxn_usd_rate: 18.9 }, source: 'API', fetched_at: '', cached: false })
  vi.mocked(api.fetchPricingTemplates).mockResolvedValue({ items: [], default_template_id: '' })
})

describe('核价汇率和模板控件', () => {
  it('Ozon 旧手动运费改用 OGL，其他固定成本保留', async () => {
    const input = fixture()
    input.targets[0].shippingQuoteMode = 'manual'
    const wrapper = mount(PricingFeesEditor, { props: { input } })
    await flushPromises()
    expect(input.targets[0].shippingQuoteMode).toBe('auto')
    expect(wrapper.get('select[aria-label="国际运费报价方式"]').text()).toContain('OGL 官方物流')
    expect(wrapper.find('option[value="manual"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('电子服务费单独计入')
    expect(input.domesticFreightCny).toBe(2)
    expect(input.packagingCostCny).toBe(0.8)
  })

  it('应用旧模板仍使用 OGL 自动核价', () => {
    const input = fixture()
    const parameters = pricingTemplateParameters(input)
    parameters.targets['ozon:global'].shipping_quote_mode = 'manual'
    parameters.targets['ozon:global'].shipping_amount = 999
    applyPricingTemplate(input, { id: 'old', name: '原模板', parameters, updated_at: '' })
    expect(input.targets[0].shippingQuoteMode).toBe('auto')
    expect(input.targets[0].shippingAmount).toBe(0)
  })

  it('重新打开恢复模板来源，最新模板和默认模板不覆盖草稿参数', async () => {
    const input = fixture()
    input.templateSource = { id: 'used', name: '原核价模板' }
    const latest = fixture()
    latest.domesticFreightCny = 88
    vi.mocked(api.fetchPricingTemplates).mockResolvedValue({ default_template_id: 'used', items: [
      { id: 'used', name: '改名后的模板', parameters: pricingTemplateParameters(latest), updated_at: '' },
    ] })
    const before = JSON.stringify(input)
    const wrapper = mount(PricingTemplates, { props: { input, useDefault: true } })
    expect(wrapper.get<HTMLSelectElement>('select').element.value).toBe('used')
    await flushPromises()
    expect(wrapper.get<HTMLSelectElement>('select').element.value).toBe('used')
    expect(wrapper.get('option[value="used"]').text()).toContain('原核价模板')
    expect(JSON.stringify(input)).toBe(before)
    expect(wrapper.emitted('notice')).toBeUndefined()
  })

  it('来源模板已删除或读取失败时保留名称和参数，成功读取后才标记已删除', async () => {
    const input = fixture()
    input.templateSource = { id: 'deleted', name: '历史模板' }
    vi.mocked(api.fetchPricingTemplates).mockRejectedValueOnce(new Error('模板读取失败'))
    const wrapper = mount(PricingTemplates, { props: { input, useDefault: false } })
    await flushPromises()
    expect(wrapper.get<HTMLSelectElement>('select').element.value).toBe('deleted')
    expect(wrapper.get('option[value="deleted"]').text()).toBe('历史模板')
    await wrapper.findAll('button').find(button => button.text() === '重试')!.trigger('click')
    await flushPromises()
    expect(wrapper.get('option[value="deleted"]').text()).toBe('历史模板 · 已删除')
    expect(input.domesticFreightCny).toBe(2)
    expect(input.templateSource).toEqual({ id: 'deleted', name: '历史模板' })
  })

  it('旧草稿不猜测来源，选择模板后记录来源供核价保存', async () => {
    const input = fixture()
    const template = { id: 'chosen', name: '日常费用', parameters: pricingTemplateParameters(input), updated_at: '' }
    vi.mocked(api.fetchPricingTemplates).mockResolvedValue({ default_template_id: 'chosen', items: [template] })
    const wrapper = mount(PricingTemplates, { props: { input, useDefault: false } })
    await flushPromises()
    expect(wrapper.get('option[value=""]').text()).toBe('选择核价模板')
    expect(input.templateSource).toBeUndefined()
    await wrapper.get('select').setValue('chosen')
    expect(input.templateSource).toEqual({ id: 'chosen', name: '日常费用' })
  })

  it('说明收进可展开的 hint，切换定价方式同步说明', async () => {
    const wrapper = mount(PricingFeesEditor, { props: { input: fixture() }, attachTo: document.body })
    expect(wrapper.text()).not.toContain('利润率 =')
    expect(wrapper.find('.cursor-help').exists()).toBe(false)
    const hint = wrapper.get('button[aria-label="定价方式说明"]')
    await hint.trigger('focus')
    await flushPromises()
    expect(document.querySelector('[role="tooltip"]')?.textContent).toContain('利润率 = 利润 ÷ 售价')
    await wrapper.findAll('button').find(button => button.text() === '成本加价')!.trigger('click')
    expect(document.querySelector('[role="tooltip"]')?.textContent).toContain('加价率 = 利润 ÷ 成本')
    await hint.trigger('keydown', { key: 'Escape' })
    expect(document.querySelector('[role="tooltip"]')).toBeNull()
  })

  it('模板提示可直接展开，不切换复选框或折叠项', async () => {
    const wrapper = mount(PricingTemplates, { props: { input: fixture() }, attachTo: document.body, global: { stubs: { teleport: true } } })
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '保存为模板')!.trigger('click')
    const save = wrapper.findAllComponents(WorkspaceDialog).find(dialog => dialog.props('title') === '保存核价模板')!
    const ratesCheckbox = save.get<HTMLInputElement>('input[type="checkbox"]')
    expect(ratesCheckbox.element.checked).toBe(false)
    await save.get('button[aria-label="同时保存本次手动汇率说明"]').trigger('click')
    await flushPromises()
    expect(ratesCheckbox.element.checked).toBe(false)
    expect(save.get('[role="tooltip"]').text()).toContain('下次使用 API 实时汇率')
    const details = save.get<HTMLDetailsElement>('details')
    await save.get('button[aria-label="模板保存范围说明"]').trigger('click')
    await flushPromises()
    expect(details.element.open).toBe(false)
    expect(details.get('[role="tooltip"]').text()).toContain('仅保存手动费用与定价规则')
  })

  it('模板保存并恢复货损参数，旧模板缺省为不计提', () => {
    const input = fixture()
    Object.assign(input.targets[0], { returnRatePercent: 10, returnDisposition: 'resell', returnHandlingCostCny: 40 })
    const template = { id: 'returns', name: '退货预估', parameters: pricingTemplateParameters(input), updated_at: '' }
    const restored = fixture()
    applyPricingTemplate(restored, template)
    expect(restored.targets[0]).toMatchObject({ returnRatePercent: 10, returnDisposition: 'resell', returnHandlingCostCny: 40 })
    delete template.parameters.targets['ozon:global'].return_rate_percent
    delete template.parameters.targets['ozon:global'].return_disposition
    delete template.parameters.targets['ozon:global'].return_handling_cost_cny
    applyPricingTemplate(restored, template)
    expect(restored.targets[0]).toMatchObject({ returnRatePercent: 0, returnDisposition: 'discard', returnHandlingCostCny: 0 })
  })

  it('直接修改单个汇率，刷新 API 后保留覆盖，恢复时使用最新参考值', async () => {
    const input = fixture()
    const wrapper = mount(PricingExchangeRates, { props: { input } })
    await flushPromises()
    await wrapper.get('input[aria-label="USD/CNY 汇率"]').setValue('7')
    expect(input.exchangeRateOverrides).toEqual({ usd_cny_rate: 7 })
    vi.mocked(api.fetchPricingExchangeRates).mockResolvedValueOnce({ rates: { usd_cny_rate: 7.3, rub_cny_rate: 12.5, mxn_usd_rate: 19 }, source: 'API', fetched_at: '', cached: false })
    await wrapper.findAll('button').find(button => button.text() === '刷新')!.trigger('click')
    await flushPromises()
    expect(wrapper.get<HTMLInputElement>('input[aria-label="USD/CNY 汇率"]').element.value).toBe('7')
    expect(wrapper.get<HTMLInputElement>('input[aria-label="USD/MXN 汇率"]').element.value).toBe('19')
    expect(wrapper.text()).toContain('API 7.3000')
    await wrapper.findAll('button').find(button => button.text() === '恢复实时值')!.trigger('click')
    expect(input.exchangeRateOverrides).toEqual({})
    expect(wrapper.get<HTMLInputElement>('input[aria-label="USD/CNY 汇率"]').element.value).toBe('7.3')
    expect(wrapper.find('select').exists()).toBe(false)
  })

  it('空汇率与非正汇率在对应字段提示，失败刷新不覆盖手动值', async () => {
    const input = fixture()
    const wrapper = mount(PricingExchangeRates, { props: { input } })
    await flushPromises()
    const field = wrapper.get('input[aria-label="CNY/RUB 汇率"]')
    await field.setValue('')
    expect(field.attributes('aria-invalid')).toBe('true')
    expect(wrapper.text()).toContain('请填写大于 0 的汇率')
    await field.setValue('12')
    vi.mocked(api.fetchPricingExchangeRates).mockRejectedValueOnce(new Error('汇率服务不可用'))
    await wrapper.findAll('button').find(button => button.text() === '刷新')!.trigger('click')
    await flushPromises()
    expect(input.exchangeRateOverrides).toEqual({ rub_cny_rate: 12 })
    expect(wrapper.text()).toContain('汇率服务不可用')
  })

  it('已有整组手动汇率转为三个显式覆盖，保留原值', async () => {
    const input = fixture()
    Object.assign(input, { exchangeRateMode: 'manual', usdCnyRate: 7, rubCnyRate: 12, mxnUsdRate: 18 })
    mount(PricingExchangeRates, { props: { input } })
    await flushPromises()
    expect(input.exchangeRateMode).toBe('live')
    expect(input.exchangeRateOverrides).toEqual({ usd_cny_rate: 7, rub_cny_rate: 12, mxn_usd_rate: 18 })
  })

  it('保存模板默认不带手动汇率、自动运费或商品资料', async () => {
    const input = fixture()
    input.exchangeRateOverrides = { usd_cny_rate: 7 }
    input.purchaseCostCny = 100
    input.battery = true
    vi.mocked(api.savePricingTemplate).mockImplementation(async body => ({ default_template_id: 'saved', saved_id: 'saved', items: [{ id: 'saved', name: body.name, parameters: body.parameters, updated_at: '2026-10-18T00:00:00Z' }] }))
    const wrapper = mount(PricingTemplates, { props: { input }, global: { stubs: { teleport: true } } })
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '保存为模板')!.trigger('click')
    const save = wrapper.findAllComponents(WorkspaceDialog).find(dialog => dialog.props('title') === '保存核价模板')!
    await save.get('input[aria-label="模板名称"]').setValue('日常直发')
    await save.findAll('button').find(button => button.text() === '保存并使用')!.trigger('click')
    await flushPromises()
    const body = vi.mocked(api.savePricingTemplate).mock.calls[0][0]
    expect(body.parameters.common).toEqual({ domestic_freight_cny: 2, packaging_cost_cny: 0.8, other_cost_cny: 0, exchange_rate_overrides: {} })
    expect(body.parameters.targets['ozon:global'].shipping_amount).toBe(0)
    expect(JSON.stringify(body)).not.toContain('purchaseCostCny')
    expect(JSON.stringify(body)).not.toContain('battery')
    expect(body.set_default).toBe(true)
    expect(input.exchangeRateOverrides).toEqual({ usd_cny_rate: 7 })
    expect(wrapper.emitted('notice')?.at(-1)?.[0]).toContain('已保存「日常直发」并设为默认模板')
  })

  it('默认模板异步加载期间的用户修改不会被覆盖', async () => {
    const input = fixture()
    const templateInput = fixture()
    templateInput.domesticFreightCny = 8
    let resolve!: (value: PricingTemplateSettings) => void
    vi.mocked(api.fetchPricingTemplates).mockReturnValueOnce(new Promise(done => { resolve = done }))
    const wrapper = mount(PricingTemplates, { props: { input, useDefault: true }, global: { stubs: { teleport: true } } })
    input.domesticFreightCny = 5
    resolve({ default_template_id: 'default', items: [{ id: 'default', name: '默认', parameters: pricingTemplateParameters(templateInput), updated_at: '' }] })
    await flushPromises()
    expect(input.domesticFreightCny).toBe(5)
    expect(wrapper.emitted('ready')).toHaveLength(1)
  })
})
