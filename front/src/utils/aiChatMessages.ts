import { toRaw } from 'vue'
import type { UIMessage } from 'ai'

function sameFields(left: object, right: object): boolean {
  const a = left as Record<string, unknown>
  const b = right as Record<string, unknown>
  const keys = Object.keys(a)
  return keys.length === Object.keys(b).length && keys.every(key => a[key] === b[key])
}

/** SDK 会原地追加当前 part；浅快照捕获变化，历史及大工具回执共享引用。 */
export function projectChatMessages(source: UIMessage[], startedAt: string, previous: UIMessage[] = []): UIMessage[] {
  const byId = new Map(previous.map(message => [message.id, message]))
  let latestUser = source.length - 1
  for (; latestUser >= 0 && source[latestUser]?.role !== 'user'; latestUser--) { /* 仅定位本轮用户消息。 */ }
  return source.map((message, index) => {
    const raw = toRaw(message)
    const old = byId.get(raw.id)
    const parts = raw.parts.map((part, partIndex) => {
      const current = toRaw(part)
      const prior = old?.parts[partIndex]
      return prior && sameFields(current, prior) ? prior : { ...current }
    })
    let metadata = raw.metadata
    if (index === latestUser && startedAt && !(metadata as Record<string, unknown> | undefined)?.ai_turn) {
      const prior = old?.metadata as { ai_turn?: { started_at?: string } } | undefined
      metadata = prior?.ai_turn?.started_at === startedAt
        ? prior : { ...(metadata ?? {}), ai_turn: { started_at: startedAt } }
    }
    const next = { ...raw, metadata, parts: old && parts.length === old.parts.length
      && parts.every((part, i) => part === old.parts[i]) ? old.parts : parts }
    return old && sameFields(next, old) ? old : next
  })
}
