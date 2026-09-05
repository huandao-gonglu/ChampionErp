import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import BrowserCollector from '../BrowserCollector.vue'
import type { BrowserDebugStatus } from '@/types/workflow'

const status: BrowserDebugStatus = {
  connected: true, port: 9222, tabsCount: 2, errorCode: '', errorMessage: '', nextAction: '',
  powershellCommand: '', cmdCommand: '', profileDir: '', tabs: [
    { title: '商品一', url: 'https://detail.1688.com/offer/1.html', platformDetected: '1688' },
    { title: '商品二', url: 'https://amazon.com/dp/ABC123', platformDetected: 'amazon' },
  ],
}

describe('浏览器采集页面选择', () => {
  it('多页时要求选择，采集与快照发送同一个精确 URL', async () => {
    const wrapper = mount(BrowserCollector, { props: { status, loading: false } })
    const button = (name: string) => wrapper.findAll('button').find((item) => item.text() === name)!
    expect(button('采集所选页面').attributes('disabled')).toBeDefined()
    await wrapper.findAll('input[type="radio"]')[1].setValue()
    await button('采集所选页面').trigger('click')
    await button('保存 HTML 快照').trigger('click')
    expect(wrapper.emitted('collect')).toEqual([[false, status.tabs[1].url], [true, status.tabs[1].url]])
    await wrapper.setProps({ status: { ...status, connected: false } })
    expect(button('采集所选页面').attributes('disabled')).toBeDefined()
  })

  it('刷新后撤销已关闭目标的选择', async () => {
    const wrapper = mount(BrowserCollector, { props: { status, loading: false } })
    await wrapper.findAll('input')[0].setValue()
    await wrapper.setProps({ status: { ...status, tabs: [] } })
    expect(wrapper.text()).toContain('没有可采集的网页')
    expect(wrapper.findAll('input')).toHaveLength(0)
  })
})
