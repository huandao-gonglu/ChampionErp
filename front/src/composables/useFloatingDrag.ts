import { computed, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { floatingPanelPosition } from '@/utils/floatingPanelPosition'

/** 只按气泡大小约束拖拽范围，弹窗独立选择方向，不改变气泡位置。 */
export function useFloatingDrag(element: Ref<HTMLElement | null>) {
  const right = ref(20)
  const bottom = ref(20)
  const dragging = ref(false)
  const viewport = ref({ width: window.innerWidth, height: window.innerHeight })
  const size = ref({ width: 56, height: 56 })
  let pointer: { id: number; x: number; y: number; right: number; bottom: number; target: HTMLElement } | null = null
  let suppressClick = false
  const style = computed(() => ({ right: `${right.value}px`, bottom: `${bottom.value}px` }))
  const panel = computed(() => floatingPanelPosition({
    left: viewport.value.width - right.value - size.value.width,
    top: viewport.value.height - bottom.value - size.value.height,
    ...size.value,
  }, viewport.value))

  function clampPosition() {
    const rect = element.value?.getBoundingClientRect()
    if (!rect) return
    viewport.value = { width: window.innerWidth, height: window.innerHeight }
    size.value = { width: rect.width || 56, height: rect.height || 56 }
    right.value = Math.max(20, Math.min(right.value, viewport.value.width - size.value.width - 20))
    bottom.value = Math.max(20, Math.min(bottom.value, viewport.value.height - size.value.height - 20))
  }

  function move(event: PointerEvent) {
    if (!pointer || event.pointerId !== pointer.id) return
    const dx = event.clientX - pointer.x
    const dy = event.clientY - pointer.y
    if (!dragging.value && Math.hypot(dx, dy) < 6) return
    if (!dragging.value) {
      dragging.value = true
      suppressClick = true
      pointer.target.setPointerCapture?.(pointer.id)
    }
    event.preventDefault()
    right.value = pointer.right - dx
    bottom.value = pointer.bottom - dy
    clampPosition()
  }

  function finish(event?: PointerEvent) {
    if (event && pointer && event.pointerId !== pointer.id) return
    const previous = pointer
    pointer = null
    dragging.value = false
    if (previous?.target.hasPointerCapture?.(previous.id)) previous.target.releasePointerCapture(previous.id)
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', finish)
    window.removeEventListener('pointercancel', finish)
    window.removeEventListener('blur', cancel)
  }

  function cancel() { finish() }

  function start(event: PointerEvent) {
    if (!event.isPrimary || event.button !== 0 || pointer) return
    suppressClick = false
    pointer = { id: event.pointerId, x: event.clientX, y: event.clientY, right: right.value, bottom: bottom.value, target: event.currentTarget as HTMLElement }
    window.addEventListener('pointermove', move, { passive: false })
    window.addEventListener('pointerup', finish)
    window.addEventListener('pointercancel', finish)
    window.addEventListener('blur', cancel)
  }

  function handleClick(event: MouseEvent) {
    if (!suppressClick || event.detail === 0) return
    suppressClick = false
    event.preventDefault()
    event.stopImmediatePropagation()
  }

  const observer = new ResizeObserver(clampPosition)
  watch(element, (current) => {
    observer.disconnect()
    if (current) {
      observer.observe(current)
      clampPosition()
    } else finish()
  }, { flush: 'post' })
  onMounted(() => window.addEventListener('resize', clampPosition))
  onBeforeUnmount(() => {
    finish()
    observer.disconnect()
    window.removeEventListener('resize', clampPosition)
  })

  return { style, panel, dragging, start, handleClick, cancel }
}
