import { afterEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import WorkspaceDialog from '../WorkspaceDialog.vue'
enableAutoUnmount(afterEach)
const pointer = { isPrimary: true, button: 0, pointerId: 1 }
function render(closeDisabled = false, modal = true) {
  return mount(WorkspaceDialog, {
    props: { open: true, title: '采购来源', variant: 'drawer', closeDisabled, modal },
    slots: { default: '<input aria-label="规格" />' },
    global: { stubs: { teleport: true } },
  })
}
describe('WorkspaceDialog 抽屉与弹窗关闭', () => {
  it('普通抽屉不进入模态顶层，拖选内容不会触发遮罩关闭', async () => {
    const showModal = vi.spyOn(HTMLDialogElement.prototype, 'showModal')
    try {
      const wrapper = render(false, false)
      await flushPromises()
      expect(showModal).not.toHaveBeenCalled()
      expect((wrapper.get('dialog').element as HTMLDialogElement).open).toBe(true)
      expect(wrapper.get('dialog').attributes('aria-modal')).toBe('false')
      const backdrop = wrapper.get('[data-testid="workspace-dialog-backdrop"]')
      await wrapper.get('input').trigger('pointerdown', pointer)
      await backdrop.trigger('pointerup', pointer)
      expect(wrapper.emitted('close')).toBeUndefined()
      await backdrop.trigger('pointerdown', pointer)
      await backdrop.trigger('pointercancel', pointer)
      await backdrop.trigger('pointerup', pointer)
      expect(wrapper.emitted('close')).toBeUndefined()
      await backdrop.trigger('pointerdown', pointer)
      await backdrop.trigger('pointerup', pointer)
      expect(wrapper.emitted('close')).toHaveLength(1)
    } finally { showModal.mockRestore() }
  })
  it('普通抽屉提交期间禁止遮罩与 Escape 关闭，解除后支持 Escape', async () => {
    const wrapper = render(true, false)
    await flushPromises()
    const backdrop = wrapper.get('[data-testid="workspace-dialog-backdrop"]')
    await backdrop.trigger('pointerdown', pointer)
    await backdrop.trigger('pointerup', pointer)
    await wrapper.get('input').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('close')).toBeUndefined()
    await wrapper.setProps({ closeDisabled: false })
    await wrapper.get('input').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
  it('内容区按下、遮罩松开不关闭；完整遮罩点击关闭', async () => {
    const wrapper = render()
    await flushPromises()
    await wrapper.get('input').trigger('pointerdown', pointer)
    await wrapper.get('dialog').trigger('pointerup', pointer)
    expect(wrapper.emitted('close')).toBeUndefined()
    await wrapper.get('dialog').trigger('pointerdown', pointer)
    await wrapper.get('dialog').trigger('pointerup', pointer)
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
  it('提交期间阻止 Escape、遮罩和关闭按钮', async () => {
    const wrapper = render(true)
    await flushPromises()
    await wrapper.get('dialog').trigger('cancel')
    await wrapper.get('dialog').trigger('pointerdown', pointer)
    await wrapper.get('dialog').trigger('pointerup', pointer)
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    expect(wrapper.emitted('close')).toBeUndefined()
    await wrapper.setProps({ closeDisabled: false })
    await wrapper.get('dialog').trigger('cancel')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
  it('取消的指针手势不关闭', async () => {
    const wrapper = render()
    await flushPromises()
    await wrapper.get('dialog').trigger('pointerdown', pointer)
    await wrapper.get('dialog').trigger('pointercancel', pointer)
    await wrapper.get('dialog').trigger('pointerup', pointer)
    expect(wrapper.emitted('close')).toBeUndefined()
  })
})
