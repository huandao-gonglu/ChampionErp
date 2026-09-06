import type { PublishPrecheck } from '@/types/workflow'

/** 同时检查汇总与分市场结果，任何阻断项都不能进入发布准备。 */
export function publishPrecheckPassed(precheck: PublishPrecheck | null): boolean {
  if (!precheck?.ok || precheck.errors.length || precheck.errorItems.length) return false
  const scopes = [...(precheck.parent ? [precheck.parent] : []), ...(precheck.marketChecks || [])]
  return scopes.every((scope) => scope.ok && scope.status !== 'blocked' && !scope.errors.length)
}
