import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as settingsApi from '@/api/workflow/settings'
import GeminiCanvasSettingsCard from '../GeminiCanvasSettingsCard.vue'

describe('GeminiCanvasSettingsCard', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('登录请求遇到网关 524 超时时自动回读账号状态，桌面已完成登录则显示成功且不清空列表', async () => {
    vi.spyOn(settingsApi, 'loginCanvasAccount').mockResolvedValueOnce({
      ok: false,
      accounts: [],
      canvasStatus: undefined,
      gatewayError: true,
      error: '等待登录响应超时（HTTP 524），若已在弹出窗口完成登录，请稍候或刷新查看账号状态。',
    })
    vi.spyOn(settingsApi, 'fetchAiConfig').mockResolvedValueOnce({
      raw: {
        canvas_status: {
          scope: 'desktop',
          desktop_online: true,
          accounts: [
            {
              id: 'acct_1',
              profile: 'acct_1',
              email: 'gongluhuandao@gmail.com',
              name: 'gonglu huandao',
              enabled: true,
              status: 'signed_in',
            },
          ],
        },
      },
    })

    const wrapper = mount(GeminiCanvasSettingsCard, {
      props: {
        model: { id: 'gemini_canvas_fixed', name: 'Gemini Canvas 修图', connection_type: 'browser', canvas_managed: true },
        canvasStatus: { scope: 'desktop', desktop_online: true, accounts: [] },
        isBoundToImageTranslate: true,
        desktopShell: true,
        localOperations: true,
        desktopReason: '',
        locked: false,
      },
    })

    const addButton = wrapper.findAll('button').find(btn => btn.text().includes('添加 Google 账号'))!
    await addButton.trigger('click')
    await flushPromises()

    expect(settingsApi.fetchAiConfig).toHaveBeenCalledTimes(1)
    const emitted = wrapper.emitted('accountsUpdated')
    expect(emitted).toHaveLength(1)
    expect(emitted![0][0]).toMatchObject({
      scope: 'desktop',
      desktop_online: true,
      accounts: [expect.objectContaining({ email: 'gongluhuandao@gmail.com', status: 'signed_in' })],
    })
    expect(wrapper.text()).toContain('已登录：gongluhuandao@gmail.com')
    expect(wrapper.text()).not.toContain('添加 Google 账号失败')
  })

  it('登录请求遇到网关错误且回读失败时不清空已有账号列表', async () => {
    vi.spyOn(settingsApi, 'loginCanvasAccount').mockResolvedValueOnce({
      ok: false,
      accounts: [],
      canvasStatus: undefined,
      gatewayError: true,
      error: '等待登录响应超时（HTTP 524）',
    })
    vi.spyOn(settingsApi, 'fetchAiConfig').mockRejectedValueOnce(new Error('network down'))

    const existing = {
      scope: 'desktop',
      desktop_online: true,
      accounts: [{ id: 'acct_existing', profile: 'acct_existing', email: 'keep@gmail.com', enabled: true, status: 'signed_in' as const }],
    }
    const wrapper = mount(GeminiCanvasSettingsCard, {
      props: {
        model: { id: 'gemini_canvas_fixed', name: 'Gemini Canvas 修图', connection_type: 'browser', canvas_managed: true },
        canvasStatus: existing,
        isBoundToImageTranslate: true,
        desktopShell: true,
        localOperations: true,
        desktopReason: '',
        locked: false,
      },
    })

    const addButton = wrapper.findAll('button').find(btn => btn.text().includes('添加 Google 账号'))!
    await addButton.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('accountsUpdated')).toBeUndefined()
    expect(wrapper.text()).toContain('HTTP 524')
    expect(wrapper.text()).toContain('keep@gmail.com')
  })
})
