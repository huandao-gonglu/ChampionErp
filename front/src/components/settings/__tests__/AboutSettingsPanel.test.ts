import { afterEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { i18n } from '@/i18n'
import { version } from '../../../../package.json'
import AboutSettingsPanel from '../AboutSettingsPanel.vue'

enableAutoUnmount(afterEach)
afterEach(() => vi.unstubAllGlobals())
const global = { plugins: [i18n], stubs: { teleport: true } }
const pointer = { isPrimary: true, button: 0, pointerId: 1 }
const fixture = {
  packages: [
    { name: 'vue', version: '3.5.35', ecosystem: 'npm', license: 'MIT', homepage: 'https://vuejs.org', notices: [{ source: 'LICENSE', text: 'vue' }] },
    { name: 'Pillow', version: '12.2.0', ecosystem: 'Python', license: 'MIT-CMU', homepage: 'javascript:alert(1)', notices: [{ source: 'LICENSE', text: 'pillow' }] },
  ],
  texts: { vue: 'Copyright Vue contributors\nPermission is hereby granted', pillow: 'Pillow license text' },
}

describe('关于与法律声明', () => {
  it('展示包版本和公司信息；协议与隐私政策独立打开且不发起网络请求', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    const wrapper = mount(AboutSettingsPanel, { global })
    expect(wrapper.get('[data-testid="app-version"]').text()).toContain(version)
    expect(wrapper.text()).toContain('成都环岛公鹿科技有限公司')
    expect(wrapper.get('a').attributes('href')).toBe('mailto:huandao-gonglu@qq.com')
    await wrapper.get('[data-testid="view-agreement"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('article').attributes('lang')).toBe('zh-CN')
    expect(wrapper.get('dialog').text()).toContain('软件许可与服务协议')
    expect(wrapper.get('article').text()).toContain('免费提供下载、安装和基础功能使用')
    expect(wrapper.get('article').text()).not.toContain('diskid')
    expect(wrapper.get('article').text()).toContain('当前版本未提供后台自动下载')
    expect(wrapper.get('article').text()).toContain('仅打开或关闭本协议不代表同意')
    expect(fetch).not.toHaveBeenCalled()
    await wrapper.get('dialog').trigger('cancel')
    expect(wrapper.find('dialog').exists()).toBe(false)
    await wrapper.get('[data-testid="view-privacy"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('dialog').text()).toContain('隐私政策')
    expect(wrapper.get('article').attributes('lang')).toBe('zh-CN')
    expect(wrapper.get('article').text()).toContain('MAC 地址')
    expect(wrapper.get('article').text()).toContain('diskid')
    expect(wrapper.get('article').text()).toContain('cpuid')
    expect(wrapper.get('article').text()).toContain('自采集之日起 3 个月')
    expect(wrapper.get('article').text()).not.toContain('三、免费功能与收费服务')
    expect(fetch).not.toHaveBeenCalled()
    await wrapper.get('article').trigger('pointerdown', pointer)
    await wrapper.get('dialog').trigger('pointerup', pointer)
    expect(wrapper.find('dialog').exists()).toBe(true)
    await wrapper.get('dialog').trigger('pointerdown', pointer)
    await wrapper.get('dialog').trigger('pointerup', pointer)
    expect(wrapper.find('dialog').exists()).toBe(false)
  })

  it('英文声明以单栏连续展示全部原文，内容拖出不关闭，正常遮罩点击关闭', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => fixture }))
    const wrapper = mount(AboutSettingsPanel, { global })
    await wrapper.get('[data-testid="view-licenses"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[lang="en"]').text()).toContain('THIRD PARTY SOFTWARE')
    const notices = wrapper.get('pre').text()
    expect(notices).toContain(fixture.texts.vue)
    expect(notices).toContain(fixture.texts.pillow)
    expect(notices).toContain('The following software may be included in this product: Pillow (12.2.0)')
    expect(notices).toContain('\n\n---\n\n')
    expect(notices.indexOf('Pillow')).toBeLessThan(notices.indexOf('vue ('))
    expect(wrapper.find('input[type="search"]').exists()).toBe(false)
    expect(wrapper.find('nav').exists()).toBe(false)
    expect(wrapper.find('[role="tablist"]').exists()).toBe(false)
    expect(wrapper.findAll('pre')).toHaveLength(1)
    expect(wrapper.find('a[href^="javascript:"]').exists()).toBe(false)
    await wrapper.get('pre').trigger('pointerdown', pointer)
    await wrapper.get('dialog').trigger('pointerup', pointer)
    expect(wrapper.find('dialog').exists()).toBe(true)
    await wrapper.get('dialog').trigger('pointerdown', pointer)
    await wrapper.get('dialog').trigger('pointerup', pointer)
    expect(wrapper.find('dialog').exists()).toBe(false)
  })

  it('声明加载失败时提供重试；关闭会取消未完成读取', async () => {
    const fetch = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ ok: true, json: async () => fixture })
    vi.stubGlobal('fetch', fetch)
    const wrapper = mount(AboutSettingsPanel, { global })
    await wrapper.get('[data-testid="view-licenses"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Unable to load')
    await wrapper.get('[role="alert"] button').trigger('click')
    await flushPromises()
    expect(wrapper.get('pre').text()).toContain('Copyright Vue contributors')
    const signal = fetch.mock.calls[1][1].signal as AbortSignal
    await wrapper.get('dialog').trigger('cancel')
    expect(signal.aborted).toBe(true)
  })
})
