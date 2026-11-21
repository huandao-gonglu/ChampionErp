export const CAPABILITY_PROBE_DESCRIPTIONS: Readonly<Record<string, string>> = {
  chat: '将发送一条简单问候，确认模型能正常回复文本。',
  json: '将要求模型返回一个 JSON 对象，确认结果可以正确解析。',
  web_search: '将要求模型联网查询成都此刻的天气，并核验返回的来源、日期和天气信息。',
  tool_calling: '将要求模型调用测试工具，并根据工具返回的结果完成回复。',
  image_understanding: '将发送两张测试图片，请模型按顺序识别颜色和形状。',
  image_generate: '将要求模型生成一张蓝色方块测试图，并检查是否返回真实图片。',
  image_edit: '将发送一张红色测试图，请模型将红色改为蓝色，并检查是否返回修改后的图片。',
}
