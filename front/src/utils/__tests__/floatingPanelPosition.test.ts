import { describe, expect, it } from 'vitest'
import { floatingPanelPosition } from '../floatingPanelPosition'

describe('AI 浮窗自适应位置', () => {
  it.each([
    [20, 20, 'bottom'],
    [1204, 20, 'bottom'],
    [20, 724, 'top'],
    [1204, 724, 'top'],
    [20, 350, 'right'],
    [1204, 350, 'left'],
  ])('气泡位于 (%i, %i) 时选择 %s', (left, top, side) => {
    const result = floatingPanelPosition({ left: Number(left), top: Number(top), width: 56, height: 56 }, { width: 1280, height: 800 })
    expect(result.side).toBe(side)
  })

  it.each([[1280, 800], [768, 1024], [360, 640], [640, 360]])('在 %i × %i 视口各区域中不越界、不覆盖气泡', (width, height) => {
    for (const left of [20, (width - 56) / 2, width - 76]) {
      for (const top of [20, (height - 56) / 2, height - 76]) {
        const { style } = floatingPanelPosition({ left, top, width: 56, height: 56 }, { width, height })
        const x = left + Number.parseFloat(String(style.left))
        const y = top + Number.parseFloat(String(style.top))
        const right = x + Number.parseFloat(String(style.width))
        const bottom = y + Number.parseFloat(String(style.height))
        expect(x).toBeGreaterThanOrEqual(20)
        expect(y).toBeGreaterThanOrEqual(20)
        expect(right).toBeLessThanOrEqual(width - 20)
        expect(bottom).toBeLessThanOrEqual(height - 20)
        expect(right <= left || x >= left + 56 || bottom <= top || y >= top + 56).toBe(true)
      }
    }
  })
})
