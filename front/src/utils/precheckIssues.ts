import type { PrecheckIssue } from '@/types/workflow'

export function precheckIssueIdentity(issue: PrecheckIssue): string {
  return JSON.stringify([
    issue.code, issue.field, issue.message, issue.severity, issue.nextAction,
    Boolean(issue.affectedSkuIds?.length),
  ])
}

/** 每个展示范围内汇总相同问题，保留去重身份范围，计数直接取展示列表。 */
export function groupPrecheckIssues(issues: PrecheckIssue[]): PrecheckIssue[] {
  const groups = new Map<string, PrecheckIssue>()
  for (const issue of issues) {
    const key = precheckIssueIdentity(issue)
    const existing = groups.get(key)
    if (!existing) {
      groups.set(key, {
        ...issue,
        affectedSkuIds: [...(issue.affectedSkuIds || [])],
        affectedAssetIds: [...(issue.affectedAssetIds || [])],
        ...(issue.relatedIssues ? { relatedIssues: [...issue.relatedIssues] } : {}),
      })
      continue
    }
    const skus = existing.affectedSkuIds!
    const seen = new Set(skus)
    for (const sku of issue.affectedSkuIds || []) {
      if (!seen.has(sku)) {
        skus.push(sku)
        seen.add(sku)
      }
    }
    existing.affectedAssetIds = [...new Set([...(existing.affectedAssetIds || []), ...(issue.affectedAssetIds || [])])]
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
