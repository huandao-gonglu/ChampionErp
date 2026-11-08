export const CHAT_PROBE_USER_MESSAGE = 'hello'

export const JSON_PROBE_USER_MESSAGE = 'Return one JSON object. Any keys and values are acceptable.'

export const CAPABILITY_PROBE_DESCRIPTIONS: Readonly<Record<string, string>> = {
  chat: '将发送一条简单问候，确认模型能正常回复文本。',
  json: '将要求模型返回一个 JSON 对象，确认结果可以正确解析。',
  web_search: '将要求模型联网查询成都此刻的天气，并核验返回的来源、日期和天气信息。',
  tool_calling: '将要求模型调用测试工具，并根据工具返回的结果完成回复。',
  image_understanding: '将发送两张测试图片，请模型按顺序识别颜色和形状。',
  image_generate: '将要求模型生成一张蓝色方块测试图，并检查是否返回真实图片。',
  image_edit: '将发送一张红色测试图，请模型将红色改为蓝色，并检查是否返回修改后的图片。',
}

/** 返回能力测试在 AI Work 中展示的系统测试消息。 */
export function capabilityProbeUserMessage(
  model: Record<string, unknown>,
): string | undefined {
  const capability = String(model.probe_only_capability || '').trim()
  if (capability === 'chat') return CHAT_PROBE_USER_MESSAGE
  if (capability === 'json') return JSON_PROBE_USER_MESSAGE
  if (capability === 'web_search') return CAPABILITY_PROBE_DESCRIPTIONS.web_search
  if (capability === 'tool_calling') {
    return 'Call the noop tool with the probe token, then return the completion marker.'
  }
  if (capability === 'image_understanding') return '请按顺序识别两张测试图片的颜色和形状。'
  if (capability === 'image_generate') {
    return 'single small blue square'
  }
  if (capability === 'image_edit') {
    return 'Change the red image to blue while preserving its dimensions.'
  }
  return undefined
}
