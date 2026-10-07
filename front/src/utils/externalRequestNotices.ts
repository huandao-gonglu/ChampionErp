import { ref } from 'vue'

export const externalRequestNotice = ref('')
const observed = new Map<string, { until: number; since: number }>()
const reported = new Set<string>()

export function observeExternalOperations(headers: Record<string, unknown>, method = '') {
  if (headers['x-external-request-blocked'] === '1') {
    externalRequestNotice.value = '本次操作因平台请求处于冷却或暂停状态，未能完成。请到平台授权查看原因和恢复方式。'
    return
  }
  if (method.toLowerCase() !== 'post') return
  // 首项是本次同步 HTTP；后续项才是已提交的后台领域任务。
  const ids = String(headers['x-external-operation-ids'] || '').split(',').slice(1)
  for (const id of ids) {
    if (!id) continue
    reported.delete(id)
    observed.set(id, { until: Date.now() + 60 * 60_000, since: Number(headers['x-external-operation-since'] || 0) })
  }
  while (observed.size > 50) observed.delete(observed.keys().next().value!)
}

export function observedExternalOperations() {
  for (const [id, value] of observed) if (value.until <= Date.now()) observed.delete(id)
  return [...observed.keys()]
}

export function acceptExternalNotices(notices: { operation_id: string; message: string; created_at?: number }[]) {
  for (const notice of notices) {
    const observation = observed.get(notice.operation_id)
    if (!observation || (notice.created_at || 0) < observation.since || reported.has(notice.operation_id)) continue
    reported.add(notice.operation_id)
    observed.delete(notice.operation_id)
    externalRequestNotice.value = `你发起的操作未能完成：${notice.message}。请到平台授权查看和处理。`
  }
  if (reported.size > 200) reported.clear()
}
