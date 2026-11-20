<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter, type LocationQueryRaw } from 'vue-router'
import { PhCheck, PhPencilSimple, PhPushPin, PhTrash, PhX } from '@phosphor-icons/vue'
import type { UIMessage } from 'ai'
import {
  fetchPydanticConversation,
  fetchPydanticConversations,
  fetchUiMessages,
  manageConversation,
  type ChatManagementAction,
} from '@/api/aiWork'
import WorkspaceHint from '@/components/shared/WorkspaceHint.vue'
import AiChatPanel from '@/components/ai-work/AiChatPanel.vue'
import AiMessageList from '@/components/ai-work/AiMessageList.vue'
import AiImageBatchProgress from '@/components/ai-work/AiImageBatchProgress.vue'
import type { ImageBatchSnapshot } from '@/api/imageBatches'
import { useAiPageContext } from '@/composables/useAiPageContext'
import { workflowNavItems } from '@/constants/navigation'
import { useAiChatStore } from '@/stores'
import {
  GLOBAL_CHAT_CONVERSATION_PREFIX,
  type PydanticConversationDetailResponse,
  type PydanticConversationSummary,
} from '@/types/aiWork'

/** 左侧历史条目；活动会话尚未持久化时允许前端临时条目。 */
interface ConversationListEntry extends PydanticConversationSummary {
  temporary?: boolean
}

const route = useRoute()
const router = useRouter()
useAiPageContext(() => ({ page: 'ai_work' }))
const chatStore = useAiChatStore()

const conversations = ref<PydanticConversationSummary[]>([])
const selectedDetail = ref<PydanticConversationDetailResponse | null>(null)
const loadingList = ref(false)
const loadingDetail = ref(false)
const listError = ref('')
const historyMessages = ref<UIMessage[] | null>(null)
const loadingUiMessages = ref(false)
const uiMessagesError = ref('')
const imageBatch = ref<ImageBatchSnapshot | null>(null)

const editor = ref<{ id: string; mode: 'rename' | 'delete' }>()
const nameInput = ref<HTMLInputElement>()
const name = ref('')
const saving = ref(false)
const actionError = ref<{ id: string; message: string }>()

let keepSidebarSelection = false
let listRequestGeneration = 0
let detailRequestGeneration = 0
let uiMessagesRequestGeneration = 0
let disposed = false

const requestedConversationId = computed(() => {
  const value = route.query.conversation_id
  return String(Array.isArray(value) ? value[0] || '' : value || '').trim()
})

const workspaceReturnRoute = computed(() => {
  const value = route.query.workspace_tab
  const tab = String(Array.isArray(value) ? value[0] || '' : value || '').trim()
  return {
    name: 'WorkflowHome',
    query: workflowNavItems.some((item) => item.key === tab) && tab !== 'dashboard'
      ? { tab }
      : {},
  }
})

const selectedId = ref(chatStore.chat?.id === requestedConversationId.value ? requestedConversationId.value : '')

const selectedSummary = computed(() => conversations.value.find(
  (conversation) => conversation.conversation_id === selectedId.value,
) || null)

const selectedCreatedAt = computed(() => (
  selectedDetail.value?.created_at || selectedSummary.value?.created_at || ''
))

const selectedUpdatedAt = computed(() => (
  selectedDetail.value?.updated_at || selectedSummary.value?.updated_at || ''
))

const refreshBusy = computed(() => loadingList.value || loadingDetail.value)

/** 选中的 conversation 正是共享 AiChatStore 的活动会话：直接绑定同一个 Chat。 */
const selectedIsActive = computed(() => (
  Boolean(selectedId.value) && chatStore.chat?.id === selectedId.value
))

const liveErrorText = computed(() => chatStore.error?.message || '')

const isGlobalChatSelected = computed(() => (
  selectedId.value.startsWith(GLOBAL_CHAT_CONVERSATION_PREFIX)
))

/** 服务端列表 + 活动会话/活动业务运行尚未持久化时的前端临时条目。 */
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
  return entries.sort((left, right) => Number(!!right.pinned) - Number(!!left.pinned))
})

function conversationTitle(id: string, saved?: string): string {
  if (id === chatStore.activeConversationId && chatStore.activeTitle !== '新对话') return chatStore.activeTitle
  return saved || (id.startsWith(GLOBAL_CHAT_CONVERSATION_PREFIX) ? '新对话' : '业务任务')
}

function editRow(row: PydanticConversationSummary, mode: 'rename' | 'delete') {
  actionError.value = undefined
  editor.value = { id: row.conversation_id, mode }
  if (mode === 'rename') {
    name.value = conversationTitle(row.conversation_id, row.title)
    void nextTick(() => {
      nameInput.value?.focus()
      nameInput.value?.select()
    })
  }
}

async function change(row: PydanticConversationSummary, action: ChatManagementAction) {
  if (saving.value) return
  const id = row.conversation_id
  const title = action === 'rename' ? name.value.trim() : undefined
  if (action === 'rename' && !title) return
  saving.value = true
  actionError.value = undefined
  try {
    await manageConversation(id, action, title)
    editor.value = undefined
    keepSidebarSelection = action === 'delete' && selectedId.value !== id
    try {
      chatStore.applyConversationChange(id, action, title)
    } finally {
      keepSidebarSelection = false
    }
    if (disposed) return
    if (action === 'delete') {
      conversations.value = conversations.value.filter(item => item.conversation_id !== id)
      if (selectedId.value === id) {
        const next = chatStore.activeConversationId || chatStore.startConversation()
        await selectConversation(next)
      }
    } else {
      const item = conversations.value.find(entry => entry.conversation_id === id)
      if (item && action === 'rename') item.title = title
      else if (item) item.pinned = action === 'pin'
    }
  } catch (cause) {
    actionError.value = { id, message: formatError(cause, '对话操作失败') }
  } finally {
    saving.value = false
  }
}

function byUpdatedDesc(
  left: PydanticConversationSummary,
  right: PydanticConversationSummary,
): number {
  if (Boolean(left.pinned) !== Boolean(right.pinned)) return left.pinned ? -1 : 1
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
    throw new Error('对话列表格式异常')
  }
  for (const conversation of value) {
    if (
      !conversation
      || typeof conversation !== 'object'
      || typeof (conversation as Record<string, unknown>).conversation_id !== 'string'
    ) {
      throw new Error('对话列表包含无效记录')
    }
  }
  return value as PydanticConversationSummary[]
}

async function loadConversation(conversationId: string): Promise<void> {
  const generation = ++detailRequestGeneration
  selectedDetail.value = null
  loadingDetail.value = true
  try {
    const response = await fetchPydanticConversation(conversationId)
    if (generation !== detailRequestGeneration || selectedId.value !== conversationId) return
    if (!response?.ok || response.conversation_id !== conversationId || !Array.isArray(response.messages)) return
    selectedDetail.value = response
  } catch {
    // 头部元数据读取失败时沿用列表摘要，不影响独立加载的对话内容。
  } finally {
    if (generation === detailRequestGeneration) loadingDetail.value = false
  }
}

/** 普通对话读取后即可输入；业务 Agent 记录只展示历史。 */
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
      uiMessagesError.value = '读取历史消息失败，请稍后重试。'
      return
    }
    if (response.conversation_id !== conversationId) {
      uiMessagesError.value = '返回的记录与所选对话不一致，请刷新后重试。'
      return
    }
    if (!Array.isArray(response.messages)) {
      uiMessagesError.value = '历史消息格式异常，请稍后重试。'
      return
    }
    if (conversationId.startsWith(GLOBAL_CHAT_CONVERSATION_PREFIX)) {
      chatStore.openConversation(response)
    } else {
      historyMessages.value = response.messages
    }
  } catch (cause) {
    if (generation !== uiMessagesRequestGeneration || selectedId.value !== conversationId) return
    uiMessagesError.value = formatError(cause, '读取历史消息失败')
  } finally {
    if (generation === uiMessagesRequestGeneration) loadingUiMessages.value = false
  }
}

/**
 * 活动会话的规范 detail：列表已有服务端条目时读取，用于头部时间与消息数量；
 * 尚未持久化时跳过请求，避免产生必然 404 并误报“对话不存在”。
 */
async function loadActiveConversationDetail(conversationId: string): Promise<void> {
  const persisted = conversations.value.some(
    (conversation) => conversation.conversation_id === conversationId,
  )
  if (persisted) {
    await loadConversation(conversationId)
    return
  }
  detailRequestGeneration += 1
  selectedDetail.value = null
  loadingDetail.value = false
}

/**
 * 选择 conversation：拆成“活动会话”与“持久化历史”两个分支。
 * 活动会话直接绑定共享 Chat 的内存实时消息，不请求 /ui-messages；
 * 避免用同一个 Promise.all() 混合实时内存态与可能尚不存在的服务端历史。
 */
async function loadSelection(conversationId: string): Promise<void> {
  // 切到内存中的新对话也要作废旧历史请求，避免迟到响应重新打开旧 Chat。
  uiMessagesRequestGeneration += 1
  selectedId.value = conversationId
  if (chatStore.chat?.id === conversationId) {
    historyMessages.value = null
    uiMessagesError.value = ''
    loadingUiMessages.value = false
    await loadActiveConversationDetail(conversationId)
    return
  }
  await Promise.all([
    loadConversation(conversationId),
    loadUiMessages(conversationId),
  ])
}

async function selectConversation(conversationId: string): Promise<void> {
  if (!conversationId) return
  const selection = loadSelection(conversationId)
  const query: LocationQueryRaw = { ...route.query, conversation_id: conversationId }
  // 空白对话尚无服务端历史，不生成刷新后必然 404 的链接。
  if (chatStore.chat?.id === conversationId && !chatStore.conversationLinkId) delete query.conversation_id
  delete query.presentation_id
  await router.replace({ query })
  await selection
}

async function refreshConversations(): Promise<void> {
  const generation = ++listRequestGeneration
  loadingList.value = true
  listError.value = ''
  try {
    const response = await fetchPydanticConversations(100, 'chats')
    if (generation !== listRequestGeneration) return
    if (!response?.ok) {
      listError.value = '读取对话列表失败，请稍后重试。'
      return
    }
    const nextConversations = validateConversationList(response.conversations)
      .sort(byUpdatedDesc)
    conversations.value = nextConversations

    const requestedId = requestedConversationId.value
    const activeId = chatStore.activeConversationId || ''
    let nextId = ''
    // 活动会话尚未出现在列表时保持选中。
    // 时保持选中；旧 URL 和未完成的导航不得覆盖用户刚创建的新对话。
    if (selectedId.value) nextId = selectedId.value
    else if (requestedId) nextId = requestedId
    else if (activeId && chatStore.chat) nextId = activeId

    if (!nextId) {
      detailRequestGeneration += 1
      uiMessagesRequestGeneration += 1
      selectedId.value = ''
      selectedDetail.value = null
      historyMessages.value = null
      uiMessagesError.value = ''
      return
    }
    await loadSelection(nextId)
  } catch (cause) {
    if (generation !== listRequestGeneration) return
    listError.value = formatError(cause, '读取对话列表失败')
  } finally {
    if (generation === listRequestGeneration) loadingList.value = false
  }
}

// 活动运行完成（store historyVersion 变化）时只刷新一次历史列表。
watch([() => chatStore.historyVersion, () => chatStore.conversationListVersion], () => {
  void refreshConversations()
})

// /new 先替换共享 Chat，再同步页面选择；同步执行使旧请求来不及重新激活旧会话。
// 普通历史读取会先选中再打开 Chat，因此不会重复加载或抢占业务任务展示。
watch(() => chatStore.chat?.id, (conversationId) => {
  if (chatStore.restoring || keepSidebarSelection) return
  if (conversationId && conversationId !== selectedId.value) {
    void selectConversation(conversationId)
  }
}, { flush: 'sync' })

// 支持通过 Router 导航切换对话。
watch(requestedConversationId, (nextValue) => {
  if (nextValue && nextValue !== selectedId.value) {
    void selectConversation(nextValue)
  }
})

onMounted(async () => {
  // 明确选择的历史由页面读取；默认入口与气泡共用同一恢复流程。
  if (!requestedConversationId.value) await chatStore.restoreConversation()
  if (!disposed) void refreshConversations()
})
onBeforeUnmount(() => {
  disposed = true
  listRequestGeneration++
  detailRequestGeneration++
  uiMessagesRequestGeneration++
})
</script>

<template>
  <div class="min-h-screen bg-accent-100 text-accent-950 dark:bg-dark-950 dark:text-accent-100">
    <header class="border-b border-slate-200 bg-white/95 px-4 py-4 shadow-sm backdrop-blur dark:border-dark-700 dark:bg-dark-900/95 md:px-6">
      <div class="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-3">
            <RouterLink
              :to="workspaceReturnRoute"
              class="text-sm font-bold text-primary-600 hover:text-primary-500 dark:text-primary-300"
              data-testid="ai-work-back"
            >
              返回工作台
            </RouterLink>
            <span class="text-slate-300 dark:text-dark-600">/</span>
            <span class="rounded-full bg-primary-100 px-2.5 py-1 text-[11px] font-black text-primary-700 dark:bg-primary-500/15 dark:text-primary-200">
              AI 对话
            </span>
          </div>
          <h1 class="mt-2 text-2xl font-black tracking-tight">AI 对话</h1>
        </div>
        <button
          type="button" class="btn btn-outline ml-auto" data-testid="ai-work-new-chat"
          @click="chatStore.startConversation(); chatStore.input = ''"
        >
          新对话
        </button>
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

        <ul v-else class="ai-work-conversation-list max-h-[calc(100vh-230px)] divide-y divide-slate-100 overflow-auto dark:divide-dark-800">
          <li v-for="conversation in displayConversations" :key="conversation.conversation_id"
            :class="selectedId === conversation.conversation_id ? 'bg-primary-50 dark:bg-primary-500/10' : ''">
            <div class="flex items-center">
              <form v-if="editor?.id === conversation.conversation_id && editor.mode === 'rename'"
                :id="`rename-${conversation.conversation_id}`" class="min-w-0 flex-1 px-4 py-3"
                @submit.prevent="change(conversation, 'rename')">
                <input :ref="el => { nameInput = el as HTMLInputElement | undefined }" v-model="name"
                  aria-label="对话名称" maxlength="80" class="input w-full min-w-0 text-sm font-bold"
                  :disabled="saving" @keydown.esc.stop.prevent="!saving && (editor = undefined)">
                <span class="mt-2 block text-[11px] text-slate-500 dark:text-accent-300">更新：{{ formatTime(conversation.updated_at) }}</span>
                <span class="mt-0.5 block text-[11px] text-slate-400">创建：{{ formatTime(conversation.created_at) }}</span>
              </form>
              <button
                v-else type="button"
                class="min-w-0 flex-1 px-4 py-3 text-left transition hover:bg-slate-50 dark:hover:bg-dark-800"
                :data-testid="conversation.temporary
                  ? 'ai-work-conversation-temporary'
                  : `ai-work-conversation-${conversation.conversation_id}`"
                @click="selectConversation(conversation.conversation_id)"
              >
                <span class="flex items-center gap-2">
                  <span class="block min-w-0 flex-1 truncate text-sm font-bold text-slate-800 dark:text-accent-100">
                    {{ conversationTitle(conversation.conversation_id, conversation.title) }}
                  </span>
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
              <div v-if="!conversation.temporary && conversation.conversation_id.startsWith(GLOBAL_CHAT_CONVERSATION_PREFIX)"
                class="mr-2 flex shrink-0 items-center">
                <template v-if="editor?.id === conversation.conversation_id && editor.mode === 'rename'">
                  <WorkspaceHint label="保存名称" content="保存名称">
                    <template #trigger="{ attrs }">
                      <button v-bind="attrs" type="submit" :form="`rename-${conversation.conversation_id}`"
                        class="rounded-lg p-1.5 text-primary-600 hover:bg-slate-100 dark:hover:bg-dark-800"
                        aria-label="保存名称" :disabled="saving || !name.trim()"><PhCheck :size="18" /></button>
                    </template>
                  </WorkspaceHint>
                  <WorkspaceHint label="取消重命名" content="取消重命名">
                    <template #trigger="{ attrs }">
                      <button v-bind="attrs" type="button" class="rounded-lg p-1.5 hover:bg-slate-100 dark:hover:bg-dark-800"
                        aria-label="取消重命名" :disabled="saving" @click="editor = undefined"><PhX :size="18" /></button>
                    </template>
                  </WorkspaceHint>
                </template>
                <template v-else>
                  <WorkspaceHint :label="conversation.pinned ? '取消置顶' : '置顶对话'" :content="conversation.pinned ? '取消置顶' : '置顶对话'">
                    <template #trigger="{ attrs }">
                      <button v-bind="attrs" type="button" class="rounded-lg p-1.5 hover:bg-slate-100 dark:hover:bg-dark-800"
                        :class="{ 'text-primary-600 dark:text-primary-400': conversation.pinned }" :disabled="saving"
                        :aria-label="conversation.pinned ? '取消置顶' : '置顶对话'" :aria-pressed="!!conversation.pinned"
                        @click="change(conversation, conversation.pinned ? 'unpin' : 'pin')">
                        <PhPushPin :size="18" :weight="conversation.pinned ? 'fill' : 'regular'" />
                      </button>
                    </template>
                  </WorkspaceHint>
                  <WorkspaceHint label="重命名对话" content="重命名对话">
                    <template #trigger="{ attrs }">
                      <button v-bind="attrs" type="button" class="rounded-lg p-1.5 hover:bg-slate-100 dark:hover:bg-dark-800"
                        aria-label="重命名对话" :disabled="saving" @click="editRow(conversation, 'rename')">
                        <PhPencilSimple :size="18" />
                      </button>
                    </template>
                  </WorkspaceHint>
                  <WorkspaceHint label="删除对话" content="删除对话">
                    <template #trigger="{ attrs }">
                      <button v-bind="attrs" type="button" class="rounded-lg p-1.5 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-dark-800"
                        aria-label="删除对话" :disabled="saving" @click="editRow(conversation, 'delete')">
                        <PhTrash :size="18" />
                      </button>
                    </template>
                  </WorkspaceHint>
                </template>
              </div>
            </div>
            <div v-if="editor?.id === conversation.conversation_id && editor.mode === 'delete'" class="px-3 pb-3">
              <p class="mb-2 text-sm">删除这段对话及其消息？删除后无法恢复。</p>
              <div class="flex justify-end gap-2">
                <button type="button" class="btn btn-outline" :disabled="saving" @click="editor = undefined">取消</button>
                <button type="button" class="btn btn-outline text-rose-600" :disabled="saving" @click="change(conversation, 'delete')">确认删除</button>
              </div>
            </div>
            <p v-if="actionError?.id === conversation.conversation_id" role="alert" class="px-3 pb-3 text-sm text-rose-600">{{ actionError.message }}</p>
          </li>
        </ul>
      </aside>

      <section class="min-w-0 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-dark-700 dark:bg-dark-900">
        <div v-if="!selectedId" class="h-[calc(100vh-240px)] min-h-[560px] p-5">
          <AiChatPanel
            :messages="chatStore.messages" :busy="chatStore.isBusy" :error="liveErrorText" :input="chatStore.input"
            :conversation-id="chatStore.chat?.id ?? ''" :history-version="chatStore.historyVersion"
            @update:input="chatStore.input = $event" @send="chatStore.sendMessage()" @stop="chatStore.stopStreaming()"
          />
        </div>

        <template v-else>
          <header class="border-b border-slate-200 px-5 py-4 dark:border-dark-700">
            <div class="flex flex-wrap items-start justify-between gap-4">
              <div class="min-w-0">
                <p class="text-xs font-black uppercase tracking-[0.14em] text-primary-600 dark:text-primary-300">
                  对话
                </p>
                <h2 class="mt-1 break-words text-sm font-bold" data-testid="ai-work-selected-id" :data-conversation-id="selectedId">
                  {{ conversationTitle(selectedId, selectedSummary?.title) }}
                </h2>
                <div class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500 dark:text-accent-300">
                  <span>创建：{{ formatTime(selectedCreatedAt) }}</span>
                  <span>更新：{{ formatTime(selectedUpdatedAt) }}</span>
                  <span v-if="selectedDetail">消息：{{ selectedDetail.messages.length }}</span>
                </div>
              </div>
            </div>
          </header>

          <!-- 普通对话使用共享 Chat；旧执行记录仍可通过原链接查看。 -->
          <div data-testid="ai-work-chat-view" class="p-5">
            <AiImageBatchProgress
              v-if="!isGlobalChatSelected"
              :conversation-id="selectedId"
              @change="imageBatch = $event"
            />
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
                :conversation-id="chatStore.chat?.id ?? ''"
                :history-version="chatStore.historyVersion"
                @update:input="chatStore.input = $event"
                @send="chatStore.sendMessage()"
                @stop="chatStore.stopStreaming()"
              />
            </div>

            <template v-else-if="!imageBatch">
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
                  以下为已保存的对话。
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
                  这段对话暂无消息。
                </div>

                <p v-if="!isGlobalChatSelected" class="mt-4 text-xs text-slate-400">
                  这是任务记录，无法在此继续对话。
                </p>
              </template>
            </template>
          </div>
        </template>
      </section>
    </main>
  </div>
</template>

<style scoped>
/* 列表仍可滚动，选中背景铺满侧栏，避免透明滚动条轨道留出右侧空隙。 */
.ai-work-conversation-list {
  scrollbar-width: none;
}
.ai-work-conversation-list::-webkit-scrollbar {
  display: none;
}
</style>
