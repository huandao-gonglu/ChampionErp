import type { PrecheckIssue } from '@/types/workflow'

export function precheckIssueIdentity(issue: PrecheckIssue): string {
  return JSON.stringify([
    issue.code, issue.field, issue.message, issue.severity, issue.nextAction,
    Boolean(issue.affectedSkus?.length),
  ])
}

/** 每个展示范围内汇总相同问题，保留全部受影响规格，计数直接取展示列表。 */
export function groupPrecheckIssues(issues: PrecheckIssue[]): PrecheckIssue[] {
  const groups = new Map<string, PrecheckIssue>()
  for (const issue of issues) {
    const key = precheckIssueIdentity(issue)
    const existing = groups.get(key)
    if (!existing) {
      groups.set(key, {
        ...issue,
        affectedSkus: [...(issue.affectedSkus || [])],
        ...(issue.relatedIssues ? { relatedIssues: [...issue.relatedIssues] } : {}),
      })
      continue
    }
    const skus = existing.affectedSkus!
    const seen = new Set(skus.map((sku) => sku.skuId))
    for (const sku of issue.affectedSkus || []) {
      if (!seen.has(sku.skuId)) {
        skus.push(sku)
        seen.add(sku.skuId)
      }
    }
    if (issue.relatedIssues?.length) {
      const related = existing.relatedIssues ||= []
      const relatedKeys = new Set(related.map(precheckIssueIdentity))
      for (const detail of issue.relatedIssues) {
        const detailKey = precheckIssueIdentity(detail)
        if (!relatedKeys.has(detailKey)) {
          related.push(detail)
          relatedKeys.add(detailKey)
        }
      }
    }
  }
  return [...groups.values()]
}
