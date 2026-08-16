<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import type { UIMessage } from 'ai'
import {
  fetchPydanticConversation,
  fetchPydanticConversations,
  fetchUiMessages,
} from '@/api/aiWork'
import AiChatPanel from '@/components/ai-work/AiChatPanel.vue'
import AiMessageList from '@/components/ai-work/AiMessageList.vue'
import JsonTreeNode from '@/components/ai-work/JsonTreeNode.vue'
import { useAiChatStore } from '@/stores'
import {
  GLOBAL_CHAT_CONVERSATION_PREFIX,
  type PydanticConversationDetailResponse,
  type PydanticConversationSummary,
} from '@/types/aiWork'

type ViewMode = 'chat' | 'raw'
type InspectorMode = 'tree' | 'raw'

/** 左侧历史条目；活动会话尚未持久化时允许前端临时条目。 */
interface ConversationListEntry extends PydanticConversationSummary {
  temporary?: boolean
}

const route = useRoute()
const chatStore = useAiChatStore()

const conversations = ref<PydanticConversationSummary[]>([])
const selectedId = ref('')
const selectedDetail = ref<PydanticConversationDetailResponse | null>(null)
const loadingList = ref(false)
const loadingDetail = ref(false)
const listError = ref('')
const detailError = ref('')
const jsonError = ref('')
const downloadError = ref('')
const viewMode = ref<ViewMode>('chat')
const inspectorMode = ref<InspectorMode>('tree')
const historyMessages = ref<UIMessage[] | null>(null)
const loadingUiMessages = ref(false)
const uiMessagesError = ref('')
const reactivateError = ref('')

let listRequestGeneration = 0
let detailRequestGeneration = 0
let uiMessagesRequestGeneration = 0

const requestedConversationId = computed(() => {
  const value = route.query.conversation_id
  return String(Array.isArray(value) ? value[0] || '' : value || '').trim()
})

const selectedSummary = computed(() => conversations.value.find(
  (conversation) => conversation.conversation_id === selectedId.value,
) || null)

const selectedCreatedAt = computed(() => (
  selectedDetail.value?.created_at || selectedSummary.value?.created_at || ''
))

const selectedUpdatedAt = computed(() => (
  selectedDetail.value?.updated_at || selectedSummary.value?.updated_at || ''
))

const rawJson = computed(() => {
  if (!selectedDetail.value) return ''
  return JSON.stringify(selectedDetail.value.messages, null, 2)
})

const refreshBusy = computed(() => loadingList.value || loadingDetail.value)

/** 选中的 conversation 正是共享 AiChatStore 的活动会话：直接绑定同一个 Chat。 */
const selectedIsActive = computed(() => (
  Boolean(selectedId.value) && chatStore.chat?.id === selectedId.value
))

const liveErrorText = computed(() => chatStore.error?.message || '')

const isGlobalChatSelected = computed(() => (
  selectedId.value.startsWith(GLOBAL_CHAT_CONVERSATION_PREFIX)
))

/** 仅 global.chat 前缀、非活动且没有其他进行中 run 的历史允许重新激活。 */
const canReactivate = computed(() => (
  isGlobalChatSelected.value
  && !selectedIsActive.value
  && !chatStore.isBusy
  && !chatStore.reactivating
))

/** 服务端列表 + 活动会话尚未持久化时的前端临时条目。 */
const displayConversations = computed<ConversationListEntry[]>(() => {
  const entries: ConversationListEntry[] = [...conversations.value]
  const activeId = chatStore.activeConversationId
  const hasServerEntry = entries.some((entry) => entry.conversation_id === activeId)
  if (activeId && chatStore.chat && !hasServerEntry) {
    entries.unshift({
      conversation_id: activeId,
      created_at: '',
      updated_at: '',
      temporary: true,
    })
  }
  return entries
})

function byUpdatedDesc(
  left: PydanticConversationSummary,
  right: PydanticConversationSummary,
): number {
  const leftTime = Date.parse(left.updated_at)
  const rightTime = Date.parse(right.updated_at)
  if (!Number.isNaN(leftTime) && !Number.isNaN(rightTime) && leftTime !== rightTime) {
    return rightTime - leftTime
  }
  return right.updated_at.localeCompare(left.updated_at)
}

function formatTime(value: string): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function formatError(cause: unknown, fallback: string): string {
  const message = cause instanceof Error ? cause.message : String(cause || '')
  return message ? `${fallback}：${message}` : fallback
}

function validateConversationList(value: unknown): PydanticConversationSummary[] {
  if (!Array.isArray(value)) {
    throw new Error('接口返回的 conversations 不是数组')
  }
  for (const conversation of value) {
    if (
      !conversation
      || typeof conversation !== 'object'
      || typeof (conversation as Record<string, unknown>).conversation_id !== 'string'
    ) {
      throw new Error('conversation 列表包含无效条目')
    }
  }
  return value as PydanticConversationSummary[]
}

function validateMessagesJson(messages: unknown): string {
  if (!Array.isArray(messages)) return 'messages 必须是 JSON 数组。'
  try {
    JSON.stringify(messages)
    return ''
  } catch (cause) {
    return formatError(cause, 'messages 无法序列化为 JSON')
  }
}

async function loadConversation(conversationId: string): Promise<void> {
  const generation = ++detailRequestGeneration
  selectedDetail.value = null
  loadingDetail.value = true
  detailError.value = ''
  jsonError.value = ''
  downloadError.value = ''
  try {
    const response = await fetchPydanticConversation(conversationId)
    if (generation !== detailRequestGeneration || selectedId.value !== conversationId) return
    if (!response?.ok) {
      detailError.value = '读取 conversation 失败：接口返回 ok=false。'
      return
    }
    if (response.conversation_id !== conversationId) {
      detailError.value = '读取 conversation 失败：响应 ID 与请求 ID 不一致。'
      return
    }
    const validationError = validateMessagesJson(response.messages)
    if (validationError) {
      jsonError.value = `消息 JSON 格式无效：${validationError}`
      return
    }
    selectedDetail.value = response
  } catch (cause) {
    if (generation !== detailRequestGeneration || selectedId.value !== conversationId) return
    detailError.value = formatError(cause, '读取 conversation 失败')
  } finally {
    if (generation === detailRequestGeneration) loadingDetail.value = false
  }
}

/** 活动会话直接绑定共享 Chat；其他 conversation 读取服务端派生的只读 UIMessage[]。 */
async function loadUiMessages(conversationId: string): Promise<void> {
  historyMessages.value = null
  uiMessagesError.value = ''
  if (chatStore.chat?.id === conversationId) {
    loadingUiMessages.value = false
    return
  }
  const generation = ++uiMessagesRequestGeneration
  loadingUiMessages.value = true
  try {
    const response = await fetchUiMessages(conversationId)
    if (generation !== uiMessagesRequestGeneration || selectedId.value !== conversationId) return
    if (!response?.ok) {
      uiMessagesError.value = '读取历史消息失败：接口返回 ok=false。'
      return
    }
    if (response.conversation_id !== conversationId) {
      uiMessagesError.value = '读取历史消息失败：响应 ID 与请求 ID 不一致。'
      return
    }
    if (!Array.isArray(response.messages)) {
      uiMessagesError.value = '读取历史消息失败：messages 不是数组。'
      return
    }
    historyMessages.value = response.messages
  } catch (cause) {
    if (generation !== uiMessagesRequestGeneration || selectedId.value !== conversationId) return
    uiMessagesError.value = formatError(cause, '读取历史消息失败')
  } finally {
    if (generation === uiMessagesRequestGeneration) loadingUiMessages.value = false
  }
}

async function loadSelection(conversationId: string, resetView = false): Promise<void> {
  selectedId.value = conversationId
  if (resetView) viewMode.value = 'chat'
  reactivateError.value = ''
  await Promise.all([
    loadConversation(conversationId),
    loadUiMessages(conversationId),
  ])
}

async function selectConversation(conversationId: string): Promise<void> {
  if (!conversationId) return
  await loadSelection(conversationId, true)
}

async function refreshConversations(): Promise<void> {
  const generation = ++listRequestGeneration
  loadingList.value = true
  listError.value = ''
  try {
    const response = await fetchPydanticConversations()
    if (generation !== listRequestGeneration) return
    if (!response?.ok) {
      listError.value = '读取 conversation 列表失败：接口返回 ok=false。'
      return
    }
    const nextConversations = validateConversationList(response.conversations)
      .sort(byUpdatedDesc)
    conversations.value = nextConversations

    const currentStillExists = nextConversations.some(
      (conversation) => conversation.conversation_id === selectedId.value,
    )
    const requestedId = requestedConversationId.value
    const activeId = chatStore.activeConversationId || ''
    let nextId = ''
    if (currentStillExists) nextId = selectedId.value
    else if (requestedId) nextId = requestedId
    else if (selectedId.value && selectedId.value === activeId) nextId = selectedId.value
    else if (activeId && chatStore.chat) nextId = activeId
    else nextId = nextConversations[0]?.conversation_id || ''

    if (!nextId) {
      detailRequestGeneration += 1
      uiMessagesRequestGeneration += 1
      selectedId.value = ''
      selectedDetail.value = null
      historyMessages.value = null
      detailError.value = ''
      jsonError.value = ''
      uiMessagesError.value = ''
      return
    }
    await loadSelection(nextId)
  } catch (cause) {
    if (generation !== listRequestGeneration) return
    listError.value = formatError(cause, '读取 conversation 列表失败')
  } finally {
    if (generation === listRequestGeneration) loadingList.value = false
  }
}

async function reactivateSelected(): Promise<void> {
  if (!canReactivate.value) return
  reactivateError.value = ''
  const succeeded = await chatStore.reactivateConversation(selectedId.value)
  if (!succeeded) {
    reactivateError.value = '重新激活失败：无法读取该会话的派生消息。'
  }
}

function downloadRawJson(): void {
  if (!selectedDetail.value || !rawJson.value) return
  downloadError.value = ''
  try {
    const blob = new Blob([rawJson.value], { type: 'application/json;charset=utf-8' })
    const objectUrl = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    const safeId = selectedDetail.value.conversation_id.replace(/[^a-zA-Z0-9._-]/g, '_')
    anchor.href = objectUrl
    anchor.download = `${safeId || 'conversation'}.json`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(objectUrl)
  } catch (cause) {
    downloadError.value = formatError(cause, '下载原始 JSON 失败')
  }
}

// 活动运行完成（store historyVersion 变化）时只刷新一次历史列表。
watch(() => chatStore.historyVersion, () => {
  void refreshConversations()
})

// 支持在页面已挂载时通过 Router 导航切换 conversation。
watch(requestedConversationId, (nextValue) => {
  if (nextValue && nextValue !== selectedId.value) {
    void selectConversation(nextValue)
  }
})

onMounted(() => {
  void refreshConversations()
})
</script>

<template>
  <div class="min-h-screen bg-accent-100 text-accent-950 dark:bg-dark-950 dark:text-accent-100">
    <header class="border-b border-slate-200 bg-white/95 px-4 py-4 shadow-sm backdrop-blur dark:border-dark-700 dark:bg-dark-900/95 md:px-6">
      <div class="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-3">
            <a href="/" class="text-sm font-bold text-primary-600 hover:text-primary-500 dark:text-primary-300">
              返回工作台
            </a>
            <span class="text-slate-300 dark:text-dark-600">/</span>
            <span class="rounded-full bg-primary-100 px-2.5 py-1 text-[11px] font-black text-primary-700 dark:bg-primary-500/15 dark:text-primary-200">
              全局对话
            </span>
            <span class="rounded-full bg-violet-100 px-2.5 py-1 text-[11px] font-black text-violet-700 dark:bg-violet-500/15 dark:text-violet-200">
              原始消息可检查
            </span>
          </div>
          <h1 class="mt-2 text-2xl font-black tracking-tight">AI Work 对话</h1>
          <p class="mt-1 text-sm text-slate-500 dark:text-accent-300">
            活动对话实时流式展示；历史由官方 dump_messages() 派生，原始 Pydantic JSON 在辅助标签检查。
          </p>
        </div>
        <button
          type="button"
          class="btn btn-outline min-w-24"
          data-testid="ai-work-refresh"
          :disabled="refreshBusy"
          @click="refreshConversations"
        >
          {{ refreshBusy ? '刷新中…' : '手动刷新' }}
        </button>
      </div>
    </header>

    <main class="mx-auto grid max-w-[1600px] gap-5 p-4 md:p-6 lg:grid-cols-[340px_minmax(0,1fr)]">
      <aside class="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-dark-700 dark:bg-dark-900">
        <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-dark-700">
          <div>
            <h2 class="text-sm font-black">历史对话</h2>
            <p class="mt-0.5 text-xs text-slate-500 dark:text-accent-300">
              {{ conversations.length }} 条已保存记录
            </p>
          </div>
          <span v-if="loadingList" class="text-xs text-slate-400">读取中…</span>
        </div>

        <p
          v-if="listError"
          role="alert"
          class="m-4 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
        >
          {{ listError }}
        </p>

        <div v-if="!displayConversations.length && !loadingList && !listError" class="p-8 text-center">
          <p class="text-sm font-bold">暂无对话记录</p>
          <p class="mt-2 text-xs text-slate-500 dark:text-accent-300">
            在浮动对话发送第一条消息后会出现在这里。
          </p>
        </div>

        <ul v-else class="max-h-[calc(100vh-230px)] divide-y divide-slate-100 overflow-auto dark:divide-dark-800">
          <li v-for="conversation in displayConversations" :key="conversation.conversation_id">
            <button
              type="button"
              class="w-full px-4 py-3 text-left transition hover:bg-slate-50 dark:hover:bg-dark-800"
              :class="selectedId === conversation.conversation_id ? 'bg-primary-50 dark:bg-primary-500/10' : ''"
              :data-testid="conversation.temporary
                ? 'ai-work-conversation-temporary'
                : `ai-work-conversation-${conversation.conversation_id}`"
              @click="selectConversation(conversation.conversation_id)"
            >
              <span class="flex items-center gap-2">
                <code class="block min-w-0 flex-1 truncate text-xs font-bold text-slate-800 dark:text-accent-100">
                  {{ conversation.conversation_id }}
                </code>
                <span
                  v-if="conversation.temporary"
                  class="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-700 dark:bg-amber-500/15 dark:text-amber-200"
                >
                  进行中
                </span>
                <span
                  v-else-if="chatStore.chat?.id === conversation.conversation_id"
                  class="shrink-0 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200"
                >
                  活动
                </span>
              </span>
              <span v-if="!conversation.temporary" class="mt-2 block text-[11px] text-slate-500 dark:text-accent-300">
                更新：{{ formatTime(conversation.updated_at) }}
              </span>
              <span v-if="!conversation.temporary" class="mt-0.5 block text-[11px] text-slate-400">
                创建：{{ formatTime(conversation.created_at) }}
              </span>
            </button>
          </li>
        </ul>
      </aside>

      <section class="min-w-0 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-dark-700 dark:bg-dark-900">
        <div v-if="!selectedId" class="flex min-h-[560px] items-center justify-center p-8 text-center">
          <div>
            <p class="text-base font-black">请选择一条 conversation</p>
            <p class="mt-2 text-sm text-slate-500 dark:text-accent-300">
              或在浮动对话中发送消息，开始新的全局对话。
            </p>
          </div>
        </div>

        <template v-else>
          <header class="border-b border-slate-200 px-5 py-4 dark:border-dark-700">
            <div class="flex flex-wrap items-start justify-between gap-4">
              <div class="min-w-0">
                <p class="text-xs font-black uppercase tracking-[0.14em] text-primary-600 dark:text-primary-300">
                  Conversation
                </p>
                <code class="mt-1 block break-all text-sm font-bold" data-testid="ai-work-selected-id">
                  {{ selectedId }}
                </code>
                <div class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500 dark:text-accent-300">
                  <span>创建：{{ formatTime(selectedCreatedAt) }}</span>
                  <span>更新：{{ formatTime(selectedUpdatedAt) }}</span>
                  <span v-if="selectedDetail">消息：{{ selectedDetail.messages.length }}</span>
                </div>
              </div>

              <div class="flex flex-wrap items-center gap-2">
                <div class="inline-flex rounded-lg border border-slate-200 p-1 dark:border-dark-700" role="tablist" aria-label="视图切换">
                  <button
                    type="button"
                    role="tab"
                    class="rounded-md px-3 py-1.5 text-xs font-bold"
                    :class="viewMode === 'chat' ? 'bg-slate-900 text-white dark:bg-white dark:text-dark-950' : 'text-slate-500 dark:text-accent-300'"
                    :aria-selected="viewMode === 'chat'"
                    data-testid="ai-work-view-chat-tab"
                    @click="viewMode = 'chat'"
                  >
                    对话
                  </button>
                  <button
                    type="button"
                    role="tab"
                    class="rounded-md px-3 py-1.5 text-xs font-bold"
                    :class="viewMode === 'raw' ? 'bg-slate-900 text-white dark:bg-white dark:text-dark-950' : 'text-slate-500 dark:text-accent-300'"
                    :aria-selected="viewMode === 'raw'"
                    data-testid="ai-work-view-raw-tab"
                    @click="viewMode = 'raw'"
                  >
                    原始消息
                  </button>
                </div>

                <template v-if="viewMode === 'raw' && selectedDetail">
                  <div class="inline-flex rounded-lg border border-slate-200 p-1 dark:border-dark-700" role="tablist" aria-label="JSON 查看方式">
                    <button
                      type="button"
                      role="tab"
                      class="rounded-md px-3 py-1.5 text-xs font-bold"
                      :class="inspectorMode === 'tree' ? 'bg-slate-900 text-white dark:bg-white dark:text-dark-950' : 'text-slate-500 dark:text-accent-300'"
                      :aria-selected="inspectorMode === 'tree'"
                      data-testid="ai-work-tree-tab"
                      @click="inspectorMode = 'tree'"
                    >
                      JSON tree
                    </button>
                    <button
                      type="button"
                      role="tab"
                      class="rounded-md px-3 py-1.5 text-xs font-bold"
                      :class="inspectorMode === 'raw' ? 'bg-slate-900 text-white dark:bg-white dark:text-dark-950' : 'text-slate-500 dark:text-accent-300'"
                      :aria-selected="inspectorMode === 'raw'"
                      data-testid="ai-work-raw-tab"
                      @click="inspectorMode = 'raw'"
                    >
                      原始 JSON
                    </button>
                  </div>
                  <button type="button" class="btn btn-outline px-3 py-2 text-xs" data-testid="ai-work-download" @click="downloadRawJson">
                    下载 JSON
                  </button>
                </template>
              </div>
            </div>
          </header>

          <!-- 对话视图：活动会话绑定共享 Chat；其他历史只读展示 -->
          <div v-if="viewMode === 'chat'" role="tabpanel" data-testid="ai-work-chat-view" class="p-5">
            <div
              v-if="selectedIsActive"
              class="h-[calc(100vh-320px)] min-h-[480px]"
              data-testid="ai-work-live-chat"
            >
              <AiChatPanel
                :messages="chatStore.messages"
                :busy="chatStore.isBusy"
                :error="liveErrorText"
                :input="chatStore.input"
                @update:input="chatStore.input = $event"
                @send="chatStore.sendMessage()"
                @stop="chatStore.stopStreaming()"
              />
            </div>

            <template v-else>
              <div
                v-if="loadingUiMessages"
                class="flex min-h-[480px] items-center justify-center text-sm text-slate-500 dark:text-accent-300"
                data-testid="ai-work-ui-loading"
              >
                正在读取历史消息…
              </div>

              <p
                v-else-if="uiMessagesError"
                role="alert"
                data-testid="ai-work-ui-messages-error"
                class="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
              >
                {{ uiMessagesError }}
              </p>

              <template v-else-if="historyMessages">
                <p class="mb-3 text-xs text-slate-400" data-testid="ai-work-history-readonly">
                  只读历史：由服务端官方 dump_messages() 派生，不参与实时流。
                </p>

                <AiMessageList
                  v-if="historyMessages.length"
                  :messages="historyMessages"
                />

                <div
                  v-else
                  class="rounded-2xl border border-dashed border-slate-300 p-8 text-center text-sm font-bold text-slate-500 dark:border-dark-600 dark:text-accent-300"
                  data-testid="ai-work-history-empty"
                >
                  该 conversation 暂无消息。
                </div>

                <div class="mt-4 flex flex-wrap items-center gap-3">
                  <button
                    v-if="canReactivate || chatStore.reactivating"
                    type="button"
                    class="btn btn-outline"
                    data-testid="ai-work-reactivate"
                    :disabled="!canReactivate"
                    @click="reactivateSelected"
                  >
                    {{ chatStore.reactivating ? '正在重新激活…' : '继续此对话' }}
                  </button>
                  <p
                    v-if="reactivateError"
                    role="alert"
                    class="text-sm text-rose-600 dark:text-rose-300"
                    data-testid="ai-work-reactivate-error"
                  >
                    {{ reactivateError }}
                  </p>
                  <p v-else-if="!isGlobalChatSelected" class="text-xs text-slate-400">
                    该会话属于其他业务 Agent，仅提供只读展示。
                  </p>
                </div>
              </template>
            </template>
          </div>

          <!-- 原始消息视图：规范 Pydantic JSON 检查与下载 -->
          <div v-else role="tabpanel" data-testid="ai-work-raw-view" class="p-5">
            <div v-if="loadingDetail" class="flex min-h-[480px] items-center justify-center text-sm text-slate-500 dark:text-accent-300">
              正在读取 Pydantic message history…
            </div>

            <p
              v-else-if="detailError"
              role="alert"
              data-testid="ai-work-detail-error"
              class="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
            >
              {{ detailError }}
            </p>

            <p
              v-else-if="jsonError"
              role="alert"
              data-testid="ai-work-json-error"
              class="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100"
            >
              {{ jsonError }}
            </p>

            <template v-else-if="selectedDetail">
              <div
                v-if="inspectorMode === 'tree'"
                data-testid="ai-work-json-tree"
                class="max-h-[calc(100vh-300px)] min-h-[480px] overflow-auto rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-dark-700 dark:bg-dark-950"
              >
                <JsonTreeNode :value="selectedDetail.messages" label="messages" />
              </div>

              <pre
                v-else
                data-testid="ai-work-raw-json"
                class="max-h-[calc(100vh-300px)] min-h-[480px] overflow-auto whitespace-pre-wrap break-words rounded-xl bg-slate-950 p-4 font-mono text-xs leading-6 text-slate-100"
              >{{ rawJson }}</pre>

              <p class="mt-3 text-xs text-slate-500 dark:text-accent-300">
                此标签只遍历 JSON 的数组、对象和标量，不解释消息类型、part 或工具调用。
              </p>
            </template>

            <p
              v-if="downloadError"
              role="alert"
              class="mt-3 text-sm text-rose-600 dark:text-rose-300"
            >
              {{ downloadError }}
            </p>
          </div>
        </template>
      </section>
    </main>
  </div>
</template>
