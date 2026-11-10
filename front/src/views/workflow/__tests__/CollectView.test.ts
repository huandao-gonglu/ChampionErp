import { reactive } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CollectView from '../CollectView.vue'
import { createDefaultCollectDiagnostics, createDefaultCollectForm, createEmptyProduct } from '@/constants/initialState'
import { launchDesktop } from '@/utils/desktopHandoff'
import { createCollectBatchRow } from '@/utils/collectQueue'

vi.mock('@/utils/desktopHandoff', async importOriginal => ({ ...await importOriginal<object>(), launchDesktop: vi.fn() }))

beforeEach(() => { vi.mocked(launchDesktop).mockClear(); vi.stubGlobal('championDesktop', undefined) })
afterEach(() => vi.unstubAllGlobals())

function setup() {
  const form = reactive({ ...createDefaultCollectForm(), productUrl: 'https://detail.1688.com/offer/1.html' })
  const wrapper = mount(CollectView, { attachTo: document.body, props: { form, diagnostics: createDefaultCollectDiagnostics(), product: createEmptyProduct(),
    loading: false, error: '', batchRows: [createCollectBatchRow(form.productUrl)] } })
  const button = (name: string) => wrapper.findAll('button').find(item => item.text() === name)!
  return { wrapper, form, button }
}

describe('URL 采集页面', () => {
  it('只在 URL 批量与 API 之间切换，API 接收商品链接并直接提交', async () => {
    const { wrapper, form, button } = setup()
    expect(wrapper.findAll('[role="tab"]').map(item => item.text())).toEqual(['浏览器采集', 'API 采集'])
    expect(wrapper.text()).not.toContain('HTTP 抓取')
    expect(wrapper.text()).not.toContain('浏览器会话')
    await wrapper.get('#batch-url-input').setValue('https://example.org/pending')
    await button('浏览器采集').trigger('keydown', { key: 'ArrowRight' })
    expect(form.mode).toBe('api')
    expect(wrapper.get('#collect-panel-browser').isVisible()).toBe(false)
    await wrapper.get('input[type="url"]').setValue('https://detail.1688.com/offer/2.html')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('collect')).toEqual([[]])
    expect(form.productUrl).toBe('https://detail.1688.com/offer/2.html')
    await button('浏览器采集').trigger('click')
    expect(form.mode).toBe('browser')
    expect(wrapper.get('#collect-panel-browser').isVisible()).toBe(true)
    expect((wrapper.get('#batch-url-input').element as HTMLTextAreaElement).value).toBe('https://example.org/pending')
    expect(launchDesktop).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('网页提交批量队列时交接桌面', async () => {
    const { wrapper, button, form } = setup()
    expect(form.mode).toBe('browser')
    await button('开始采集（1）').trigger('click')
    expect(launchDesktop).toHaveBeenCalledOnce()
    expect(launchDesktop).toHaveBeenLastCalledWith(expect.objectContaining({ state: { workflow: expect.objectContaining({
      collect: { mode: 'browser', productUrl: form.productUrl, platform: form.platform },
      collectAction: { type: 'batch', rowIds: [wrapper.props('batchRows')[0].id] },
    }) } }))
    expect(wrapper.emitted('batchCollect')).toBeUndefined()
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

  it('桌面直接提交批量链接，等待验证码时可取消', async () => {
    vi.stubGlobal('championDesktop', { localOperations: true })
    const { wrapper, button } = setup()
    await button('开始采集（1）').trigger('click')
    expect(wrapper.emitted('batchCollect')?.at(-1)).toEqual([[wrapper.props('batchRows')[0].id]])
    expect(launchDesktop).not.toHaveBeenCalled()
    await wrapper.setProps({ diagnostics: { ...createDefaultCollectDiagnostics(), status: 'waiting_verification' } })
    await button('取消等待').trigger('click')
    expect(wrapper.emitted('cancelVerification')).toHaveLength(1)
    wrapper.unmount()
  })
})
