import type { CSSProperties } from 'vue'

type Bounds = { left: number; top: number; width: number; height: number }

/** 弹窗独立定位；间距由容器内边距提供，鼠标经过气泡与弹窗之间不会退出悬停。 */
export function floatingPanelPosition(anchor: Bounds, viewport: { width: number; height: number }) {
  const margin = 20
  const gap = 12
  const width = Math.min(400, Math.max(0, viewport.width - margin * 2))
  let height = Math.min(560, Math.max(0, viewport.height - margin * 2))
  const space = {
    top: anchor.top - margin - gap,
    bottom: viewport.height - anchor.top - anchor.height - margin - gap,
    left: anchor.left - margin - gap,
    right: viewport.width - anchor.left - anchor.width - margin - gap,
  }
  const side = space.top >= height ? 'top'
    : space.bottom >= height ? 'bottom'
      : space.left >= width ? 'left'
        : space.right >= width ? 'right'
          : space.top >= space.bottom ? 'top' : 'bottom'
  const vertical = side === 'top' || side === 'bottom'
  if (vertical) height = Math.min(height, Math.max(0, space[side]))
  const clamp = (value: number, maximum: number) => Math.max(margin, Math.min(value, maximum))
  let left = clamp(anchor.left + anchor.width - width, viewport.width - width - margin)
  let top = clamp(anchor.top + anchor.height - height, viewport.height - height - margin)
  if (side === 'top') top = anchor.top - height - gap
  if (side === 'bottom') top = anchor.top + anchor.height
  if (side === 'left') left = anchor.left - width - gap
  if (side === 'right') left = anchor.left + anchor.width
  const style: CSSProperties = {
    left: `${left - anchor.left}px`,
    top: `${top - anchor.top}px`,
    width: `${width + (vertical ? 0 : gap)}px`,
    height: `${height + (vertical ? gap : 0)}px`,
    boxSizing: 'border-box',
    paddingBottom: side === 'top' ? `${gap}px` : undefined,
    paddingTop: side === 'bottom' ? `${gap}px` : undefined,
    paddingRight: side === 'left' ? `${gap}px` : undefined,
    paddingLeft: side === 'right' ? `${gap}px` : undefined,
  }
  return { side, style }
}
