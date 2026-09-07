import { describe, expect, it } from 'vitest'
import { groupPrecheckIssues } from '@/utils/precheckIssues'
import type { PrecheckIssue } from '@/types/workflow'

describe('groupPrecheckIssues', () => {
  const issue: PrecheckIssue = { code: 'MISSING', field: 'attributes.COLOR', message: '缺少颜色', severity: 'error', nextAction: '填写属性' }

  it('汇总同类问题，按 SKU 身份合并明细且不修改输入', () => {
    const first = { skuId: 'first', sku: 'SELL-1', name: '同名规格' }
    const second = { skuId: 'second', sku: 'SELL-2', name: '同名规格' }
    const input = [{ ...issue, affectedSkus: [first] }, { ...issue, affectedSkus: [first, second] }]
    const original = JSON.stringify(input)

    expect(groupPrecheckIssues(input)).toEqual([{ ...issue, affectedSkus: [first, second] }])
    expect(JSON.stringify(input)).toBe(original)
  })

  it('不同字段、原因、级别、建议和整组范围不合并', () => {
    const issues = [issue, { ...issue, field: 'attributes.SIZE' }, { ...issue, message: '颜色值无效' }, { ...issue, severity: 'warning' }, { ...issue, nextAction: '刷新类目' }, { ...issue, affectedSkus: [{ skuId: 'first', sku: 'SELL-1', name: '红色' }] }]
    expect(groupPrecheckIssues(issues)).toHaveLength(issues.length)
  })

  it('重复的主问题保留不同的关联说明，不修改输入', () => {
    const first = { ...issue, code: 'EMPTY_VARIANTS', field: 'sku_items', message: '差异属性为空' }
    const second = { ...issue, code: 'OTHER_CHECK', field: 'sku_items', message: '另一个关联校验' }
    const input = [{ ...issue, relatedIssues: [first] }, { ...issue, relatedIssues: [first, second] }]
    const original = JSON.stringify(input)

    const result = groupPrecheckIssues(input)

    expect(result).toHaveLength(1)
    expect(result[0].relatedIssues).toEqual([first, second])
    expect(JSON.stringify(input)).toBe(original)
  })
})
