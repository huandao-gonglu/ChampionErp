import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { i18n } from '@/i18n'
import WatermarkSettingsPanel from '../WatermarkSettingsPanel.vue'
import { fetchSystemSettings, previewWatermark, saveSystemSettings, type WatermarkPreview } from '@/api/systemSettings'
import type { BackendWatermarkSettings } from '@/types/workflow.generated'

vi.mock('@/api/systemSettings', () => ({ fetchSystemSettings: vi.fn(), previewWatermark: vi.fn(), saveSystemSettings: vi.fn() }))
enableAutoUnmount(afterEach)
const watermark: BackendWatermarkSettings = { text: '', image_data_url: '', opacity: 25, size_percent: 30, density: 5, color: '#737373' }
const result = (url = 'data:image/png;base64,cHJldmlldw=='): WatermarkPreview => ({ preview_url: url, sample_name: '草稿示例', message: '' })
beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  vi.mocked(fetchSystemSettings).mockResolvedValue({ orders_auto_sync_interval_hours: 12, watermark: { ...watermark } })
  vi.mocked(previewWatermark).mockResolvedValue(result())
  vi.mocked(saveSystemSettings).mockImplementation(async change => ({ orders_auto_sync_interval_hours: 12, watermark: change.watermark! }))
})
afterEach(() => vi.useRealTimers())
const open = () => mount(WatermarkSettingsPanel, { global: { plugins: [i18n] } })
const updatePreview = async () => { await flushPromises(); await vi.advanceTimersByTimeAsync(300); await flushPromises() }

describe('图片水印设置', () => {
  it('进入即自动预览草稿图，右侧无按钮和底图选择，文字编辑不自动保存', async () => {
    const wrapper = open(); await updatePreview()
    const panel = wrapper.get('[data-testid="watermark-preview"]')
    expect(panel.get('img').attributes('src')).toBe(result().preview_url)
    expect(panel.find('input').exists()).toBe(false)
    expect(panel.find('button').exists()).toBe(false)
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false)
    expect(wrapper.find('select').exists()).toBe(false)
    expect(previewWatermark).toHaveBeenCalledWith(watermark, expect.any(AbortSignal))
    await wrapper.get('textarea').setValue('品牌水印')
    await updatePreview()
    expect(previewWatermark).toHaveBeenLastCalledWith({ ...watermark, text: '品牌水印' }, expect.any(AbortSignal))
    expect(saveSystemSettings).not.toHaveBeenCalled()
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(saveSystemSettings).toHaveBeenCalledWith({ watermark: { ...watermark, text: '品牌水印' } })
    expect(wrapper.text()).toContain('水印设置已保存')
  })

  it('允许文字和图片同时保存，清空两者后也可保存并自动预览原图', async () => {
    vi.mocked(fetchSystemSettings).mockResolvedValueOnce({ orders_auto_sync_interval_hours: 12, watermark: { ...watermark, text: '品牌', image_data_url: 'data:image/png;base64,cG5n' } })
    const wrapper = open(); await updatePreview()
    expect(wrapper.get('textarea').element.value).toBe('品牌')
    expect(wrapper.find('img[alt="已上传的水印图片"]').exists()).toBe(true)
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(vi.mocked(saveSystemSettings).mock.calls[0]![0].watermark).toMatchObject({ text: '品牌', image_data_url: 'data:image/png;base64,cG5n' })
    await wrapper.get('textarea').setValue('')
    await wrapper.findAll('button').find(node => node.text() === '移除图片')!.trigger('click')
    await updatePreview()
    expect(previewWatermark).toHaveBeenLastCalledWith(watermark, expect.any(AbortSignal))
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(saveSystemSettings).toHaveBeenLastCalledWith({ watermark })
  })

  it('快速拖动合并请求，旧请求的迟到回执不能覆盖新效果', async () => {
    let finishOld!: (value: WatermarkPreview) => void
    vi.mocked(previewWatermark).mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve }))
    const wrapper = open(); await updatePreview()
    const oldSignal = vi.mocked(previewWatermark).mock.calls[0]![1]!
    await wrapper.get('[data-testid="watermark-density"]').setValue(6)
    await wrapper.get('[data-testid="watermark-density"]').setValue(9)
    await wrapper.get('[data-testid="watermark-size"]').setValue(40)
    expect(previewWatermark).toHaveBeenCalledTimes(1)
    expect(oldSignal.aborted).toBe(true)
    await updatePreview()
    expect(previewWatermark).toHaveBeenCalledTimes(2)
    expect(previewWatermark).toHaveBeenLastCalledWith({ ...watermark, density: 9, size_percent: 40 }, expect.any(AbortSignal))
    finishOld(result('data:image/png;base64,b2xk')); await flushPromises()
    expect(wrapper.get('[data-testid="watermark-preview"] img').attributes('src')).toBe(result().preview_url)
  })

  it('无草稿图或预览失败时直接显示说明，保存不受预览请求影响', async () => {
    vi.mocked(previewWatermark).mockResolvedValueOnce({ preview_url: '', sample_name: '', message: '草稿中暂无可用图片' })
    const wrapper = open(); await updatePreview()
    expect(wrapper.get('[data-testid="watermark-preview"]').text()).toContain('草稿中暂无可用图片')
    expect(wrapper.find('[data-testid="watermark-preview"] img').exists()).toBe(false)
    vi.mocked(previewWatermark).mockRejectedValueOnce(new Error('预览失败'))
    await wrapper.get('textarea').setValue('文字'); await updatePreview()
    expect(wrapper.get('[data-testid="watermark-preview"]').text()).toContain('预览失败')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(saveSystemSettings).toHaveBeenCalledTimes(1)
  })

  it('离开页面取消待执行和进行中的预览', async () => {
    const wrapper = open(); await flushPromises()
    wrapper.unmount(); await vi.advanceTimersByTimeAsync(300)
    expect(previewWatermark).not.toHaveBeenCalled()
    const second = open(); await updatePreview()
    const signal = vi.mocked(previewWatermark).mock.calls[0]![1]!
    second.unmount()
    expect(signal.aborted).toBe(true)
  })

  it('加载失败时提供重新读取入口', async () => {
    vi.mocked(fetchSystemSettings).mockRejectedValueOnce(new Error('读取失败'))
    const wrapper = open(); await flushPromises()
    expect(wrapper.find('form').exists()).toBe(false)
    await wrapper.get('button').trigger('click'); await updatePreview()
    expect(wrapper.find('form').exists()).toBe(true)
    expect(previewWatermark).toHaveBeenCalledTimes(1)
  })
})
