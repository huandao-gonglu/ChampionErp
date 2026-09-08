import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import BrowserCollector from '../BrowserCollector.vue'
import type { BrowserCollectRow, BrowserDebugStatus } from '@/types/workflow'

const status: BrowserDebugStatus = {
  connected: true, port: 9222, tabsCount: 2, errorCode: '', errorMessage: '', nextAction: '',
  powershellCommand: '', cmdCommand: '', profileDir: '', tabs: [
    { title: '商品一', url: 'https://detail.1688.com/offer/1.html', platformDetected: '1688' },
    { title: '商品二', url: 'https://amazon.com/dp/ABC123', platformDetected: 'amazon' },
  ],
}

describe('浏览器采集页面选择', () => {
  it('支持多选，按列表顺序发送精确 URL；采集与快照使用同一选择', async () => {
    const wrapper = mount(BrowserCollector, { props: { status, rows: [], loading: false } })
    const button = (name: string) => wrapper.findAll('button').find((item) => item.text().startsWith(name))!
    expect(button('采集所选页面').attributes('disabled')).toBeDefined()
    const checkboxes = wrapper.findAll('input[name="collect-browser-tab"]')
    await checkboxes[1].setValue(true)
    await checkboxes[0].setValue(true)
    expect(wrapper.text()).toContain('已选 2 / 2 页')
    await button('采集所选页面').trigger('click')
    await button('保存 HTML 快照').trigger('click')
    expect(wrapper.emitted('collect')).toEqual([[false, status.tabs.map(tab => tab.url)], [true, status.tabs.map(tab => tab.url)]])
    await wrapper.setProps({ status: { ...status, connected: false } })
    expect(button('采集所选页面').attributes('disabled')).toBeDefined()
  })

  it('全选可取消；刷新只移除已关闭目标，不自动勾选新页面', async () => {
    const wrapper = mount(BrowserCollector, { props: { status, rows: [], loading: false } })
    const all = wrapper.get('input[type="checkbox"]')
    await all.setValue(true)
    expect(wrapper.text()).toContain('已选 2 / 2 页')
    await all.setValue(false)
    expect(wrapper.text()).toContain('已选 0 / 2 页')
    await all.setValue(true)
    await wrapper.setProps({ status: { ...status, tabs: [status.tabs[1], { ...status.tabs[0], url: 'https://detail.1688.com/offer/3.html' }] } })
    const checkboxes = wrapper.findAll('input[name="collect-browser-tab"]')
    expect((checkboxes[0].element as HTMLInputElement).checked).toBe(true)
    expect((checkboxes[1].element as HTMLInputElement).checked).toBe(false)
    expect((all.element as HTMLInputElement).indeterminate).toBe(true)
    await wrapper.setProps({ status: { ...status, tabs: [] } })
    expect(wrapper.text()).toContain('没有可采集的网页')
    expect(wrapper.findAll('input')).toHaveLength(0)
  })

  it('执行期间锁定选择与提交，展示每页进度、错误和快照', async () => {
    const rows: BrowserCollectRow[] = status.tabs.map((tab, index) => ({
      ...tab, status: index ? 'failed' : 'success', saveOnly: true,
      error: index ? '商品页已关闭' : '', nextAction: '', htmlSnapshotPath: index ? '' : '/tmp/a b.html',
    }))
    const wrapper = mount(BrowserCollector, { props: { status, rows, loading: false } })
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.setProps({ loading: true })
    expect(wrapper.findAll('input').every(input => input.attributes('disabled') !== undefined)).toBe(true)
    const button = wrapper.findAll('button').find(item => item.text().startsWith('采集所选页面'))!
    await button.trigger('click')
    expect(wrapper.emitted('collect')).toBeUndefined()
    expect(wrapper.text()).toContain('本轮处理：2 / 2 页')
    expect(wrapper.text()).toContain('商品页已关闭')
    expect(wrapper.get('a').attributes('href')).toBe('/file?path=%2Ftmp%2Fa%20b.html')
    await wrapper.setProps({ rows: [{ ...rows[0], status: 'waiting_verification' }, { ...rows[1], status: 'pending', error: '' }] })
    expect(wrapper.text()).toContain('等待人工验证')
    expect(wrapper.text()).toContain('等待处理')
  })

  it('重复 URL 只选择一次，忽略非网页标签', async () => {
    const wrapper = mount(BrowserCollector, { props: {
      status: { ...status, tabs: [status.tabs[0], status.tabs[0], { ...status.tabs[1], url: 'chrome://newtab/' }] }, rows: [], loading: false,
    } })
    expect(wrapper.findAll('input[name="collect-browser-tab"]')).toHaveLength(1)
    const button = wrapper.findAll('button').find(item => item.text().startsWith('采集所选页面'))!
    await button.trigger('click')
    expect(wrapper.emitted('collect')).toEqual([[false, [status.tabs[0].url]]])
  })
})
