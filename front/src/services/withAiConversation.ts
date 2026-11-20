import { useAiChatStore } from '@/stores/aiChat'

/** 页面确认的操作使用原业务接口，普通对话显示服务端实际回执。 */
export async function withAiConversation<T>(
  title: string,
  operation: (conversationId: string) => Promise<T>,
): Promise<T> {
  const chat = useAiChatStore()
  const id = chat.startManualAction(title)
  try {
    const result = await operation(id)
    chat.finishManualAction(id)
    return result
  } catch (error) {
    chat.finishManualAction(id, error)
    throw error
  }
}
