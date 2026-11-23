import { describe, expect, it } from 'vitest'
import { attributeConstraintError, attributeConstraintHint } from '../categoryConstraints'
describe('平台值限制提示', () => {
  it('按 Unicode 字符计数，检查枚举集合并保留原文', () => {
    const value = { values: [{ value: '🙂'.repeat(100) }, { value: 'а'.repeat(104) }] }
    expect(attributeConstraintError({ maxLength: 100 }, value)).toContain('第 2 个值：当前 104 个字符')
    expect(attributeConstraintHint({ maxLength: 100 }, value)).toContain('100、104')
    expect(value.values[1]?.value.length).toBe(104)
    expect(attributeConstraintError({}, 'а'.repeat(2001))).toBe('')
  })
  it('保留零下限和数值范围，拒绝非数值', () => {
    const limits = { valueType: 'numeric', minValue: '0', maxValue: '10' }
    expect(attributeConstraintError(limits, '0')).toBe('')
    expect(attributeConstraintError(limits, '-1')).toContain('不能小于 0')
    expect(attributeConstraintError(limits, '11')).toContain('不能大于 10')
    expect(attributeConstraintError(limits, 'NaN')).toContain('有限')
    expect(attributeConstraintError(limits, '0x10')).toContain('有限')
    expect(attributeConstraintHint(limits, '0')).toContain('最小值 0')
  })
  it('统一识别 decimal / Decimal / number_unit 与 Integer 类型约束', () => {
    expect(attributeConstraintError({ valueType: 'decimal', minValue: '0.5' }, '0.2')).toContain('不能小于 0.5')
    expect(attributeConstraintError({ valueType: 'Decimal', maxValue: '10' }, 'abc')).toContain('数字')
    expect(attributeConstraintError({ valueType: 'number_unit', maxValue: '100' }, { value: '120', unit: 'g' })).toContain('不能大于 100')
    expect(attributeConstraintError({ valueType: 'Integer' }, '1.5')).toContain('整数')
  })
})
