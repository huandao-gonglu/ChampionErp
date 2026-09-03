export const CHAT_PROBE_USER_MESSAGE = 'hello'

export const JSON_PROBE_SYSTEM_MESSAGE = 'Return one valid JSON object without Markdown.'
export const JSON_PROBE_USER_MESSAGE = 'Return one JSON object. Any keys and values are acceptable.'

export function jsonProbeMessages(): Array<{ role: string; content: string }> {
  return [
    { role: 'system', content: JSON_PROBE_SYSTEM_MESSAGE },
    { role: 'user', content: JSON_PROBE_USER_MESSAGE },
  ]
}

function messageUserContent(value: unknown): string {
  if (!Array.isArray(value)) return ''
  for (const item of value) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) continue
    const record = item as Record<string, unknown>
    if (String(record.role || '').trim().toLowerCase() !== 'user') continue
    const content = String(record.content || '').trim()
    if (content) return content
  }
  return ''
}

/** 返回能力测试在 qiwork 中展示的首次 user 消息。 */
export function capabilityProbeUserMessage(
  model: Record<string, unknown>,
): string | undefined {
  const configured = messageUserContent(model.probe_messages)
  if (configured) return configured

  const capability = String(model.probe_only_capability || '').trim()
  if (capability === 'chat') return CHAT_PROBE_USER_MESSAGE
  if (capability === 'json') return JSON_PROBE_USER_MESSAGE
  if (capability === 'tool_calling') {
    return 'Call the noop tool with the probe token, then return the completion marker.'
  }
  if (capability === 'image_generate') {
    return String(model.probe_image_prompt || '').trim() || 'single small blue square'
  }
  if (capability === 'image_edit') {
    return String(model.probe_image_prompt || '').trim()
      || 'Change the red image to blue while preserving its dimensions.'
  }
  return undefined
}
