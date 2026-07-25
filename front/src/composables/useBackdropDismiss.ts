import { ref } from 'vue'

/**
 * 只有指针从遮罩空白处按下并在遮罩空白处释放时才关闭弹窗，避免拖选输入内容时误关闭。
 */
export function useBackdropDismiss(onDismiss: () => void) {
  const backdropPointerId = ref<number | null>(null)

  function recordBackdropPointer(event: PointerEvent) {
    if (!event.isPrimary) return
    backdropPointerId.value = event.target === event.currentTarget && event.button === 0 ? event.pointerId : null
  }

  function dismissFromBackdrop(event: PointerEvent) {
    if (!event.isPrimary || backdropPointerId.value !== event.pointerId) return
    const shouldDismiss = event.target === event.currentTarget
    backdropPointerId.value = null
    if (shouldDismiss) onDismiss()
  }

  function resetBackdropPointer() {
    backdropPointerId.value = null
  }

  return {
    recordBackdropPointer,
    dismissFromBackdrop,
    resetBackdropPointer,
  }
}
