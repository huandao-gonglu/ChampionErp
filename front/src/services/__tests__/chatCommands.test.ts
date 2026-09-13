import { describe, expect, it, vi } from 'vitest'
import { CHAT_COMMANDS, filterCommandsByQuery, matchChatCommand } from '../chatCommands'

describe('会话快捷命令', () => {
  it('命令只覆盖会话操作，审批通过原生工具卡提交', () => {
    expect(CHAT_COMMANDS.map(command => command.name)).toEqual(['new', 'cancel', 'refresh'])
    expect(matchChatCommand('/approve')).toBeNull()
    expect(matchChatCommand('普通消息')).toBeNull()
    expect(filterCommandsByQuery('re').map(command => command.name)).toEqual(['refresh'])
  })
  it('运行期间可以提交取消要求', () => {
    const context = { isBusy: true, startConversation: vi.fn(), stopStreaming: vi.fn(), refreshHistory: vi.fn() }
    const match = matchChatCommand('/cancel')!
    expect(match.command.available(context)).toBe(true)
    match.command.execute(context, '')
    expect(context.stopStreaming).toHaveBeenCalledOnce()
  })
})
