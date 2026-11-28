import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import DraftWorkspacePanel from '../DraftWorkspacePanel.vue'

describe('草稿工作台重新加载入口', () => {
  it('冲突错误保留在工作台内，并提供可点击的重新加载按钮', async () => {
    const wrapper = mount(DraftWorkspacePanel, { props: {
      activeTab: 'pricing', draftId: 'draft-1', draftTitle: '草稿',
      syncMessage: '草稿已更新，请重新读取后核价。',
    } })
    const alert = wrapper.get('[role="alert"]')
    expect(alert.text()).toContain('草稿已更新')
    const button = alert.get('button')
    expect(button.text()).toBe('重新加载最新数据')
    await button.trigger('click')
    expect(wrapper.emitted('reload')).toHaveLength(1)
    await wrapper.setProps({ refreshing: true })
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.text()).toBe('正在加载…')
  })

  it('没有错误时也可主动重新加载，保存期间禁用', async () => {
    const wrapper = mount(DraftWorkspacePanel, { props: { activeTab: 'text', draftId: 'draft-1', draftTitle: '草稿', reloadDisabled: true } })
    const reload = wrapper.findAll('button').find(button => button.text() === '重新加载最新数据')!
    expect(reload.attributes('disabled')).toBeDefined()
    await wrapper.setProps({ reloadDisabled: false })
    await reload.trigger('click')
    expect(wrapper.emitted('reload')).toHaveLength(1)
  })

  it('保存操作固定在顶栏最右侧，不随重新加载按钮显隐位移', async () => {
    const wrapper = mount(DraftWorkspacePanel, {
      props: { activeTab: 'text', draftId: 'draft-1', draftTitle: '草稿' },
      slots: {
        'secondary-actions': '<button type="button">生成文案</button>',
        actions: '<button type="button" class="btn btn-primary page-save-btn">保存草稿</button>',
      },
    })
    expect(wrapper.find('.page-header-sticky').exists()).toBe(true)
    const headerButtons = () => wrapper.get('header').findAll('button').map(b => b.text())
    expect(headerButtons()).toEqual(['返回草稿箱', '生成文案', '重新加载最新数据', '保存草稿'])

    await wrapper.setProps({ syncMessage: '远端已更新' })
    expect(headerButtons()).toEqual(['返回草稿箱', '生成文案', '保存草稿'])
  })
})
