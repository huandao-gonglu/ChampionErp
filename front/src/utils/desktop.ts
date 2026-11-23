import { shallowRef } from 'vue'
import type { BackendOperationAvailability } from '@/types/workflow.generated'

/** 识别桌面外壳；当前连接是否可执行由服务端判断。 */
export function hasLocalOperations(): boolean {
  return window.championDesktop?.localOperations === true
}

const availability = shallowRef<BackendOperationAvailability | null>(null)
export const OPERATION_AVAILABILITY_CHANGED = 'champion-operation-availability-changed'
const pendingReason = shallowRef('正在确认当前端的可用操作…')

export function setOperationAvailability(value: BackendOperationAvailability | null, message = '正在确认当前端的可用操作…') {
  availability.value = value
  pendingReason.value = message
}

export function desktopOperationReason(): string {
  if (!hasLocalOperations()) return ''
  if (!availability.value) return pendingReason.value
  return availability.value.desktop_reason ? '桌面连接不可用，请重新登录桌面端。' : ''
}

export function modelOperationReason(useCase: string): string {
  if (!availability.value) return pendingReason.value
  if (availability.value.desktop_use_cases?.includes(useCase)) return desktopOperationReason()
  return availability.value.blocked_use_cases[useCase] || ''
}

/** 网页入口转为桌面交接，配置错误仍由原入口解释；本账号有可代执行的在线桌面时直接提交。 */
export function modelRequiresDesktop(useCase: string): boolean {
  if (hasLocalOperations() || !(availability.value?.desktop_use_cases || []).includes(useCase)) return false
  return !(availability.value?.delegated_use_cases || []).includes(useCase)
}

const localFields = new Set(['command', 'profile', 'sandbox', 'browser_mode', 'browser_profile', 'browser_port', 'browser_url', 'browser_proxy'])

/** 先通过受限桌面 IPC 保存本机字段，业务 HTTP 只提交连接引用。 */
export async function prepareLocalModel(model: Record<string, unknown>): Promise<Record<string, unknown>> {
  if (!['browser', 'cli'].includes(String(model.connection_type))) return model
  const prepared = { ...model }
  const reference = String(model.local_connection_id || model.id || '')
  const fields = Object.fromEntries(Object.entries(model).filter(([key]) => localFields.has(key)).map(([key, value]) => [key, String(value ?? '')]))
  if (!desktopOperationReason() && window.championDesktop?.configureModels && Object.keys(fields).length) {
    const saved = await window.championDesktop.configureModels({ [reference]: fields })
    prepared.local_connection_id = reference
    prepared.local_config_fingerprint = saved.fingerprints[reference]
  }
  for (const key of localFields) delete prepared[key]
  return prepared
}
