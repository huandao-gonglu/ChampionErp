import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CollectView from '../CollectView.vue'
import { createDefaultCollectDiagnostics, createDefaultCollectForm, createEmptyProduct } from '@/constants/initialState'
import { launchDesktop } from '@/utils/desktopHandoff'
import { createCollectBatchRow } from '@/utils/collectQueue'

vi.mock('@/utils/desktopHandoff', async importOriginal => ({ ...await importOriginal<object>(), launchDesktop: vi.fn() }))

beforeEach(() => { vi.mocked(launchDesktop).mockClear(); vi.stubGlobal('championDesktop', undefined) })
afterEach(() => vi.unstubAllGlobals())

function setup(mode: 'browser' | 'http' = 'browser') {
  const form = { ...createDefaultCollectForm(), mode, productUrl: 'https://detail.1688.com/offer/1.html' }
  const wrapper = mount(CollectView, { props: { form, diagnostics: createDefaultCollectDiagnostics(), product: createEmptyProduct(),
    loading: false, error: '', batchRows: [createCollectBatchRow(form.productUrl)] } })
  const button = (name: string) => wrapper.findAll('button').find(item => item.text() === name)!
  return { wrapper, form, button }
}

describe('URL 采集页面', () => {
  it('仅展示 URL 采集，进入页面、展开选项和修改输入均留在网页', async () => {
    const { wrapper, form } = setup()
    expect(wrapper.find('[data-testid="collect-method-card"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('导入手动内容')
    expect(wrapper.text()).not.toContain('打开采集浏览器')
    expect(wrapper.text()).not.toContain('API 采集')
    expect(wrapper.find('[data-testid="collect-batch-manager"]').exists()).toBe(true)
    await wrapper.findAll('summary')[0].trigger('click')
    const input = wrapper.findAll('input').find(item => item.attributes('placeholder')?.startsWith('https://detail.1688.com'))!
    await input.setValue('https://detail.1688.com/offer/2.html')
    expect(form.productUrl).toContain('/2.html')
    expect(launchDesktop).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('网页只在点击批量采集或单链接采集时交接桌面', async () => {
    const { wrapper, button, form } = setup()
    expect(form.mode).toBe('browser')
    await button('开始采集（1）').trigger('click')
    expect(launchDesktop).toHaveBeenCalledOnce()
    expect(launchDesktop).toHaveBeenLastCalledWith(expect.objectContaining({ state: { workflow: expect.objectContaining({
      collect: { mode: 'browser', productUrl: form.productUrl, platform: form.platform },
      collectAction: { type: 'batch', rowIds: [wrapper.props('batchRows')[0].id] },
    }) } }))
    expect(wrapper.emitted('batchCollect')).toBeUndefined()
    await button('采集单链接').trigger('click')
    expect(launchDesktop).toHaveBeenCalledTimes(2)
    expect(launchDesktop).toHaveBeenLastCalledWith(expect.objectContaining({ state: { workflow: expect.objectContaining({ collectAction: { type: 'single' } }) } }))
    expect(wrapper.emitted('collect')).toBeUndefined()
    wrapper.unmount()
  })

  it('重试只交接失败链接的范围', async () => {
    const { wrapper, button } = setup()
    const succeeded = { ...wrapper.props('batchRows')[0], status: 'success' as const }
    const failed = { ...createCollectBatchRow('https://detail.1688.com/offer/2.html'), status: 'failed' as const }
    await wrapper.setProps({ batchRows: [succeeded, failed] })
    await button('重试失败项').trigger('click')
    expect(launchDesktop).toHaveBeenLastCalledWith(expect.objectContaining({ state: { workflow: expect.objectContaining({
      batchRows: [succeeded, failed], collectAction: { type: 'batch', rowIds: [failed.id] },
    }) } }))
    wrapper.unmount()
  })

  it('显式选择 HTTP 后在网页执行，Cookie 只从本次输入提交并随即清空', async () => {
    const { wrapper, form, button } = setup('http')
    await wrapper.findAll('button').find(item => item.text().includes('高级选项'))!.trigger('click')
    const input = wrapper.get('[data-testid="transient-alibaba-cookie"]')
    await input.setValue('request-only-cookie')
    expect(JSON.stringify(form)).not.toContain('request-only-cookie')
    await button('采集单链接').trigger('click')
    expect(wrapper.emitted('collect')?.at(-1)).toEqual([{ alibabaCookie: 'request-only-cookie' }])
    expect((input.element as HTMLTextAreaElement).value).toBe('')
    await button('开始采集（1）').trigger('click')
    expect(wrapper.emitted('batchCollect')).toHaveLength(1)
    expect(launchDesktop).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('桌面直接提交 URL，等待验证码时可取消等待', async () => {
    vi.stubGlobal('championDesktop', { localOperations: true })
    const { wrapper, button } = setup()
    await button('采集单链接').trigger('click')
    expect(wrapper.emitted('collect')).toHaveLength(1)
    expect(launchDesktop).not.toHaveBeenCalled()
    await wrapper.setProps({ diagnostics: { ...createDefaultCollectDiagnostics(), status: 'waiting_verification' } })
    await button('取消等待').trigger('click')
    expect(wrapper.emitted('cancelVerification')).toHaveLength(1)
    wrapper.unmount()
  })
})
