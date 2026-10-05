import { describe, expect, it } from 'vitest'
import { deadline } from '../orderPresentation'
const now = Date.parse('2026-10-13T08:00:00Z')
describe('发货日期精度', () => {
  it('只有日期时不补造零点，也不显示超时或小时倒计时', () => {
    expect(deadline('2026-10-13', now)).toEqual({ text: '2026-10-13', note: '仅提供日期', urgent: false })
    expect(deadline('2026-10-13', now + 86400000)).toEqual({ text: '2026-10-13', note: '仅提供日期', urgent: false })
  })
  it('没有时区的时分保留平台原文，不按电脑时区推算', () => {
    expect(deadline('2026-10-13T12:30:00', now)).toEqual({ text: '2026-10-13 12:30', note: '平台时间（未提供时区）', urgent: false })
  })
  it('带时区的截止才计算剩余小时', () => {
    expect(deadline('2026-10-13T12:00:00+03:00', now)).toMatchObject({ note: '剩余 1 小时', urgent: true })
  })
  it('缺失或无效日期不能被展示为有效截止', () => {
    for (const value of [undefined, '', 'invalid', '2026-02-30']) expect(deadline(value, now)).toEqual({ text: '未提供', note: '', urgent: false })
  })
})
