import { describe, expect, it } from 'vitest'
import {
  capabilityProbeUserMessage,
  CAPABILITY_PROBE_DESCRIPTIONS,
  CHAT_PROBE_USER_MESSAGE,
  JSON_PROBE_USER_MESSAGE,
} from '@/constants/aiCapabilityProbe'

describe('AI 能力探测 user 消息', () => {
  it('chat 与 JSON 返回实际发送的首次 user 消息', () => {
    expect(capabilityProbeUserMessage({
      probe_only_capability: 'chat',
    })).toBe(CHAT_PROBE_USER_MESSAGE)
    expect(capabilityProbeUserMessage({
      probe_only_capability: 'json',
    })).toBe(JSON_PROBE_USER_MESSAGE)
  })

  it('旧请求中的自定义内容不会替换系统测试消息', () => {
    expect(capabilityProbeUserMessage({
      probe_only_capability: 'json',
      probe_messages: [
        { role: 'system', content: 'system prompt' },
        { role: 'user', content: '自定义测试内容' },
      ],
    })).toBe(JSON_PROBE_USER_MESSAGE)
    expect(capabilityProbeUserMessage({
      probe_only_capability: 'web_search',
    })).toBe(CAPABILITY_PROBE_DESCRIPTIONS.web_search)
  })

  it('为 Function Call 与图片探针提供 user 气泡', () => {
    expect(capabilityProbeUserMessage({
      probe_only_capability: 'tool_calling',
    })).toContain('noop tool')
    expect(capabilityProbeUserMessage({
      probe_only_capability: 'image_edit',
      probe_image_prompt: 'Turn it blue.',
    })).toBe('Change the red image to blue while preserving its dimensions.')
  })
})
