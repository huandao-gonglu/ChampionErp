import { describe, expect, it } from 'vitest'
import {
  capabilityProbeUserMessage,
  CHAT_PROBE_USER_MESSAGE,
  jsonProbeMessages,
  JSON_PROBE_USER_MESSAGE,
} from '@/constants/aiCapabilityProbe'

describe('AI 能力探测 user 消息', () => {
  it('chat 与 JSON 返回实际发送的首次 user 消息', () => {
    expect(capabilityProbeUserMessage({
      probe_only_capability: 'chat',
    })).toBe(CHAT_PROBE_USER_MESSAGE)
    expect(capabilityProbeUserMessage({
      probe_only_capability: 'json',
      probe_messages: jsonProbeMessages(),
    })).toBe(JSON_PROBE_USER_MESSAGE)
  })

  it('优先显示调用方配置的首次 user 消息', () => {
    expect(capabilityProbeUserMessage({
      probe_only_capability: 'web_search',
      probe_messages: [
        { role: 'system', content: 'system prompt' },
        { role: 'user', content: '查询成都实时天气' },
      ],
    })).toBe('查询成都实时天气')
  })

  it('为 Function Call 与图片探针提供 user 气泡', () => {
    expect(capabilityProbeUserMessage({
      probe_only_capability: 'tool_calling',
    })).toContain('noop tool')
    expect(capabilityProbeUserMessage({
      probe_only_capability: 'image_edit',
      probe_image_prompt: 'Turn it blue.',
    })).toBe('Turn it blue.')
  })
})
