// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { DOMWrapper, enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import WorkspaceHint from '../WorkspaceHint.vue'

enableAutoUnmount(afterEach)
afterEach(() => { vi.useRealTimers() })
const props = { label: '费用说明', content: '每件商品的固定费用。' }

it('悬停即显示实际浮层，聚焦可读，Escape 关闭', async () => {
  const wrapper = mount(WorkspaceHint, { props, attachTo: document.body })
  const body = new DOMWrapper(document.body)
  const button = wrapper.get('button')
  expect(body.find('[role="tooltip"]').exists()).toBe(false)
  await wrapper.trigger('mouseenter')
  await flushPromises()
  const hint = body.get('[role="tooltip"]')
  expect(hint.text()).toBe(props.content)
  expect(hint.attributes('style')).toContain('visibility: visible')
  expect(button.attributes('aria-describedby')).toBe(hint.attributes('id'))
  expect(button.attributes('title')).toBeUndefined()
  await button.trigger('keydown', { key: 'Escape' })
  expect(body.find('[role="tooltip"]').exists()).toBe(false)
  await button.trigger('focus')
  await flushPromises()
  expect(body.get('[role="tooltip"]').text()).toBe(props.content)
})

it('点击固定提示，再次点击或点击外部关闭，移入浮层可继续阅读', async () => {
  vi.useFakeTimers()
  const wrapper = mount(WorkspaceHint, { props, attachTo: document.body })
  const body = new DOMWrapper(document.body)
  await wrapper.get('button').trigger('click')
  await flushPromises()
  await wrapper.trigger('mouseleave')
  await vi.advanceTimersByTimeAsync(150)
  expect(body.find('[role="tooltip"]').exists()).toBe(true)
  await wrapper.get('button').trigger('click')
  expect(body.find('[role="tooltip"]').exists()).toBe(false)
  await wrapper.trigger('mouseenter')
  await flushPromises()
  await wrapper.trigger('mouseleave')
  await body.get('[role="tooltip"]').trigger('mouseenter')
  await vi.advanceTimersByTimeAsync(150)
  expect(body.find('[role="tooltip"]').exists()).toBe(true)
  await body.trigger('pointerdown')
  expect(body.find('[role="tooltip"]').exists()).toBe(false)
})

it('模态框内的提示挂在框内，Escape 不冒泡关闭模态框', async () => {
  const dialog = document.createElement('dialog')
  document.body.append(dialog)
  const cancel = vi.fn()
  dialog.addEventListener('keydown', cancel)
  const wrapper = mount(WorkspaceHint, { props, attachTo: dialog })
  try {
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(dialog.querySelector('[role="tooltip"]')?.textContent).toBe(props.content)
    await wrapper.get('button').trigger('keydown', { key: 'Escape' })
    expect(dialog.querySelector('[role="tooltip"]')).toBeNull()
    expect(cancel).not.toHaveBeenCalled()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    window.dispatchEvent(new Event('resize'))
    await flushPromises()
    expect(dialog.querySelector('[role="tooltip"]')).toBeNull()
  } finally { wrapper.unmount(); dialog.remove() }
})
