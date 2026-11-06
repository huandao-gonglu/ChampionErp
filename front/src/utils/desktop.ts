/** 本机能力只由桌面 preload 声明，普通网页不探测服务器本机环境。 */
export function hasLocalOperations(): boolean {
  return window.championDesktop?.localOperations === true
}

const localFields = new Set(['command', 'profile', 'sandbox', 'browser_mode', 'browser_profile', 'browser_port', 'browser_url', 'browser_proxy'])

/** 先通过受限桌面 IPC 保存本机字段，业务 HTTP 只提交连接引用。 */
export async function prepareLocalModel(model: Record<string, unknown>): Promise<Record<string, unknown>> {
  if (!['browser', 'cli'].includes(String(model.connection_type))) return model
  const prepared = { ...model }
  const reference = String(model.local_connection_id || model.id || '')
  const fields = Object.fromEntries(Object.entries(model).filter(([key]) => localFields.has(key)).map(([key, value]) => [key, String(value ?? '')]))
  if (hasLocalOperations() && window.championDesktop?.configureModels && Object.keys(fields).length) {
    const saved = await window.championDesktop.configureModels({ [reference]: fields })
    prepared.local_connection_id = reference
    prepared.local_config_fingerprint = saved.fingerprints[reference]
  }
  for (const key of localFields) delete prepared[key]
  return prepared
}
