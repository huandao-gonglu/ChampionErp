import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import UserAgentsSettingsPanel from '../UserAgentsSettingsPanel.vue'
import { fetchUserAgents, saveUserAgents } from '@/api/userAgents'

vi.mock('@/api/userAgents', () => ({ fetchUserAgents: vi.fn(), saveUserAgents: vi.fn() }))
enableAutoUnmount(afterEach)
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchUserAgents).mockResolvedValue({ content: '<!-- 交流偏好：在下方填写 -->\n', revision: 'a'.repeat(64) })
  vi.mocked(saveUserAgents).mockImplementation(async content => ({ content, revision: 'b'.repeat(64) }))
})

describe('用户自定义全局指令', () => {
  it('展示大纲但不自动保存，用户保存时携带读取版本', async () => {
    const wrapper = mount(UserAgentsSettingsPanel)
    await flushPromises()
    expect(wrapper.get('textarea').element.value).toContain('交流偏好')
    expect(saveUserAgents).not.toHaveBeenCalled()
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
    await wrapper.get('textarea').setValue('## 交流偏好\n先给结果。\n')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(saveUserAgents).toHaveBeenCalledWith('## 交流偏好\n先给结果。\n', 'a'.repeat(64))
    expect(wrapper.text()).toContain('下次 AI 运行时生效')
  })

  it('允许用户清空偏好，不自动补回默认内容', async () => {
    const wrapper = mount(UserAgentsSettingsPanel)
    await flushPromises()
    await wrapper.get('textarea').setValue('')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(saveUserAgents).toHaveBeenCalledWith('', 'a'.repeat(64))
    expect(wrapper.get('textarea').element.value).toBe('')
  })

  it('保存冲突时保留本地编辑，等待用户决定是否重新读取', async () => {
    vi.mocked(saveUserAgents).mockRejectedValue(new Error('自定义指令已在其他页面更新，请重新读取后保存。'))
    const wrapper = mount(UserAgentsSettingsPanel)
    await flushPromises()
    await wrapper.get('textarea').setValue('不能丢失的本地修改')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('其他页面更新')
    expect(wrapper.get('textarea').element.value).toBe('不能丢失的本地修改')
    expect(fetchUserAgents).toHaveBeenCalledTimes(1)
  })

  it('读取失败时可重试且不能覆盖，超出字节限制时禁止保存', async () => {
    vi.mocked(fetchUserAgents).mockRejectedValueOnce(new Error('暂时无法读取'))
    const wrapper = mount(UserAgentsSettingsPanel)
    await flushPromises()
    expect(wrapper.find('textarea').exists()).toBe(false)
    await wrapper.get('button').trigger('click')
    await flushPromises()
    await wrapper.get('textarea').setValue('汉'.repeat(12000))
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('内容超过上限')
    await wrapper.get('form').trigger('submit')
    expect(saveUserAgents).not.toHaveBeenCalled()
  })
})
