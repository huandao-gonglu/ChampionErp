import { describe, expect, it, vi } from 'vitest'
import { useBackdropDismiss } from '@/composables/useBackdropDismiss'

function pointerEvent(target: EventTarget, currentTarget: EventTarget, pointerId = 1) {
  return {
    isPrimary: true,
    button: 0,
    pointerId,
    target,
    currentTarget,
  } as PointerEvent
}

describe('useBackdropDismiss', () => {
  it('拖选从输入框移到遮罩松开时不关闭', () => {
    const dismiss = vi.fn()
    const { recordBackdropPointer, dismissFromBackdrop } = useBackdropDismiss(dismiss)
    const backdrop = new EventTarget()
    const input = new EventTarget()

    recordBackdropPointer(pointerEvent(input, backdrop))
    dismissFromBackdrop(pointerEvent(backdrop, backdrop))

    expect(dismiss).not.toHaveBeenCalled()
  })

  it('仅在遮罩按下并在遮罩松开时关闭', () => {
    const dismiss = vi.fn()
    const { recordBackdropPointer, dismissFromBackdrop } = useBackdropDismiss(dismiss)
    const backdrop = new EventTarget()

    recordBackdropPointer(pointerEvent(backdrop, backdrop))
    dismissFromBackdrop(pointerEvent(backdrop, backdrop))

    expect(dismiss).toHaveBeenCalledOnce()
  })

  it('在弹窗内松开时会清除已记录的遮罩按下状态', () => {
    const dismiss = vi.fn()
    const { recordBackdropPointer, dismissFromBackdrop } = useBackdropDismiss(dismiss)
    const backdrop = new EventTarget()
    const dialog = new EventTarget()
    const input = new EventTarget()

    recordBackdropPointer(pointerEvent(backdrop, backdrop))
    dismissFromBackdrop(pointerEvent(dialog, backdrop))
    recordBackdropPointer(pointerEvent(input, backdrop))
    dismissFromBackdrop(pointerEvent(backdrop, backdrop))

    expect(dismiss).not.toHaveBeenCalled()
  })
})
