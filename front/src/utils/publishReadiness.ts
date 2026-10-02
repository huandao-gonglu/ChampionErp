import type { PublishPrecheck } from '@/types/workflow'

/** 同时检查汇总与分市场结果，任何阻断项都不能进入最终发布确认。 */
export function publishPrecheckPassed(precheck: PublishPrecheck | null): boolean {
  if (!precheck?.ok || precheck.errors.length || precheck.errorItems.length) return false
  const scopes = [...(precheck.parent ? [precheck.parent] : []), ...(precheck.marketChecks || [])]
  return scopes.every((scope) => scope.ok && scope.status !== 'blocked' && !scope.errors.length)
}

/** 尚未准备或目标已切换可进入显式准备；最终提交仍要求全部通过。 */
export function publishPreparationAllowed(precheck: PublishPrecheck | null): boolean {
  if (!precheck || publishPrecheckPassed(precheck)) return true
  const pendingCodes = new Set(['IMAGE_NOT_PREPARED', 'IMAGE_DELIVERY_STALE'])
  const scopes = [...(precheck.parent ? [precheck.parent] : []), ...(precheck.marketChecks || [])]
  if (scopes.some((scope) => (!scope.ok || scope.status === 'blocked') && !scope.errors.length)) return false
  const issues = [...precheck.errorItems, ...scopes.flatMap((scope) => scope.errors)]
  return issues.length > 0 && issues.every((issue) => pendingCodes.has(issue.code))
}
