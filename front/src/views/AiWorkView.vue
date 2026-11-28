<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter, type LocationQueryRaw } from 'vue-router'
import {
  PhArrowLeft,
  PhArrowsClockwise,
  PhCheck,
  PhMagnifyingGlass,
  PhPencilSimple,
  PhPlus,
  PhPushPin,
  PhSidebarSimple,
  PhTrash,
  PhX,
} from '@phosphor-icons/vue'
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
const searchInput = ref<HTMLInputElement>()
const name = ref('')
const searchQuery = ref('')
const sidebarFilter = ref<'all' | 'pinned'>('all')
const sidebarCollapsed = ref(false)
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

const pinnedCount = computed(() => displayConversations.value.filter(item => item.pinned).length)

const filteredConversations = computed<ConversationListEntry[]>(() => {
  const query = searchQuery.value.trim().toLowerCase()
  return displayConversations.value.filter(entry => {
    if (sidebarFilter.value === 'pinned' && !entry.pinned) return false
    if (!query) return true
    const title = conversationTitle(entry.conversation_id, entry.title).toLowerCase()
    return title.includes(query) || entry.conversation_id.toLowerCase().includes(query)
  })
})

interface ConversationTimeGroup {
  key: string
  label: string
  items: ConversationListEntry[]
}

function groupBucketFor(entry: ConversationListEntry, startOfToday: number, startOfYesterday: number): 'pinned' | 'today' | 'yesterday' | 'earlier' {
  if (entry.pinned) return 'pinned'
  if (entry.temporary) return 'today'
  const ts = Date.parse(entry.updated_at || entry.created_at || '')
  if (Number.isNaN(ts) || ts >= startOfToday) return 'today'
  if (ts >= startOfYesterday) return 'yesterday'
  return 'earlier'
}

const groupedConversations = computed<ConversationTimeGroup[]>(() => {
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const startOfYesterday = startOfToday - 86_400_000
  const buckets: Record<'pinned' | 'today' | 'yesterday' | 'earlier', ConversationListEntry[]> = {
    pinned: [],
    today: [],
    yesterday: [],
    earlier: [],
  }
  for (const entry of filteredConversations.value) {
    buckets[groupBucketFor(entry, startOfToday, startOfYesterday)].push(entry)
  }
  const labels: ['pinned' | 'today' | 'yesterday' | 'earlier', string][] = [
    ['pinned', '已置顶'],
    ['today', '今天'],
    ['yesterday', '昨天'],
    ['earlier', '更早'],
  ]
  return labels
    .filter(([key]) => buckets[key].length > 0)
    .map(([key, label]) => ({ key, label, items: buckets[key] }))
})

function isConversationRunning(entry: ConversationListEntry): boolean {
  return Boolean(entry.temporary) || (chatStore.chat?.id === entry.conversation_id && chatStore.isRunning)
}

function formatCompactTime(value: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  if (diffMs >= 0 && diffMs < 60_000) return '刚刚'
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const startOfYesterday = startOfToday - 86_400_000
  const ts = date.getTime()
  if (ts >= startOfToday) {
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
  }
  if (ts >= startOfYesterday) return '昨天'
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return date.getFullYear() === now.getFullYear() ? `${month}/${day}` : `${date.getFullYear()}/${month}/${day}`
}

const selectedMetaTooltip = computed(() => {
  const parts: string[] = []
  if (selectedCreatedAt.value) parts.push(`创建：${formatTime(selectedCreatedAt.value)}`)
  if (selectedUpdatedAt.value) parts.push(`更新：${formatTime(selectedUpdatedAt.value)}`)
  if (selectedDetail.value) parts.push(`消息：${selectedDetail.value.messages.length}`)
  return parts.join(' · ')
})

const selectedMetaCompact = computed(() => {
  const parts: string[] = []
  if (selectedDetail.value) parts.push(`${selectedDetail.value.messages.length} 条消息`)
  const updated = formatCompactTime(selectedUpdatedAt.value)
  if (updated) parts.push(updated === '刚刚' ? '刚刚更新' : `更新 ${updated}`)
  return parts.join(' · ')
})

function startNewChat(): void {
  chatStore.startConversation()
  chatStore.input = ''
}

function handleGlobalShortcut(event: KeyboardEvent): void {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    if (sidebarCollapsed.value) sidebarCollapsed.value = false
    void nextTick(() => {
      searchInput.value?.focus()
      searchInput.value?.select()
    })
  }
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
  window.addEventListener('keydown', handleGlobalShortcut)
  // 明确选择的历史由页面读取；默认入口与气泡共用同一恢复流程。
  if (!requestedConversationId.value) await chatStore.restoreConversation()
  if (!disposed) void refreshConversations()
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleGlobalShortcut)
  disposed = true
  listRequestGeneration++
  detailRequestGeneration++
  uiMessagesRequestGeneration++
})
</script>

<template>
  <div class="flex h-screen overflow-hidden bg-white text-accent-950 dark:bg-dark-900 dark:text-accent-100">
    <!-- 左侧精简侧栏（整合返回工作台 + 新对话 + 搜索 + 分组单行历史，可一键收起） -->
    <aside
      v-if="!sidebarCollapsed"
      class="flex w-[264px] shrink-0 flex-col border-r border-slate-200 bg-slate-50/90 dark:border-dark-800 dark:bg-dark-950/90"
      data-testid="ai-work-sidebar"
    >
      <div class="flex h-11 shrink-0 items-center justify-between gap-2 border-b border-slate-200/80 px-3 dark:border-dark-800">
        <RouterLink
          :to="workspaceReturnRoute"
          class="inline-flex min-w-0 items-center gap-1.5 text-xs font-bold text-primary-600 transition hover:text-primary-500 dark:text-primary-300"
          data-testid="ai-work-back"
        >
          <PhArrowLeft :size="14" class="shrink-0" aria-hidden="true" />
          <span class="truncate">返回工作台</span>
        </RouterLink>
        <div class="flex shrink-0 items-center gap-1">
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-lg bg-primary-600 px-2 py-1 text-xs font-semibold text-white transition hover:bg-primary-500"
            data-testid="ai-work-new-chat"
            @click="startNewChat"
          >
            <PhPlus :size="13" weight="bold" aria-hidden="true" />
            新对话
          </button>
          <button
            type="button"
            class="flex size-7 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-200/70 hover:text-slate-700 dark:text-accent-400 dark:hover:bg-dark-800 dark:hover:text-white"
            aria-label="收起历史侧栏"
            title="收起历史侧栏"
            data-testid="ai-work-sidebar-toggle"
            @click="sidebarCollapsed = true"
          >
            <PhSidebarSimple :size="16" aria-hidden="true" />
          </button>
        </div>
      </div>

      <!-- 搜索与轻量筛选 -->
      <div class="shrink-0 space-y-1.5 border-b border-slate-200/70 px-2.5 py-2 dark:border-dark-800">
        <div class="relative flex items-center">
          <PhMagnifyingGlass :size="13" class="pointer-events-none absolute left-2.5 text-slate-400 dark:text-accent-500" aria-hidden="true" />
          <input
            ref="searchInput"
            v-model="searchQuery"
            type="search"
            aria-label="搜索历史对话"
            :placeholder="conversations.length ? `搜索 ${conversations.length} 条历史对话…` : '搜索历史对话…'"
            data-testid="ai-work-search"
            class="w-full rounded-lg border border-slate-200 bg-white py-1 pl-7 pr-7 text-xs text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-primary-400 dark:border-dark-700 dark:bg-dark-900 dark:text-accent-100 dark:placeholder:text-accent-500 dark:focus:border-primary-500/60"
          />
          <button
            v-if="searchQuery"
            type="button"
            aria-label="清空搜索"
            class="absolute right-1.5 rounded p-0.5 text-slate-400 hover:text-slate-700 dark:hover:text-white"
            @click="searchQuery = ''"
          >
            <PhX :size="12" aria-hidden="true" />
          </button>
        </div>
        <div v-if="pinnedCount || loadingList" class="flex items-center justify-between gap-1 text-[11px]">
          <div v-if="pinnedCount" class="flex items-center gap-1">
            <button
              type="button"
              class="rounded-md px-2 py-0.5 font-medium transition"
              :class="sidebarFilter === 'all'
                ? 'bg-primary-500/15 text-primary-700 dark:text-primary-300'
                : 'text-slate-500 hover:text-slate-700 dark:text-accent-400 dark:hover:text-accent-200'"
              @click="sidebarFilter = 'all'"
            >
              全部 ({{ conversations.length }})
            </button>
            <button
              type="button"
              class="rounded-md px-2 py-0.5 font-medium transition"
              :class="sidebarFilter === 'pinned'
                ? 'bg-primary-500/15 text-primary-700 dark:text-primary-300'
                : 'text-slate-500 hover:text-slate-700 dark:text-accent-400 dark:hover:text-accent-200'"
              @click="sidebarFilter = 'pinned'"
            >
              已置顶 ({{ pinnedCount }})
            </button>
          </div>
          <span v-if="loadingList" class="ml-auto text-[11px] text-slate-400 dark:text-accent-500">同步中…</span>
        </div>
      </div>

      <p
        v-if="listError"
        role="alert"
        class="m-2.5 rounded-xl border border-rose-200 bg-rose-50 p-2.5 text-xs text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
      >
        {{ listError }}
      </p>

      <div v-if="!displayConversations.length && !loadingList && !listError" class="px-5 py-8 text-center">
        <p class="text-xs font-bold text-slate-700 dark:text-accent-200">暂无对话记录</p>
        <p class="mt-1.5 text-[11px] leading-5 text-slate-500 dark:text-accent-400">
          发送第一条消息后会自动保存在这里。
        </p>
      </div>

      <div v-else-if="!groupedConversations.length && !loadingList" class="px-5 py-8 text-center text-xs text-slate-400 dark:text-accent-500">
        没有匹配的对话记录
      </div>

      <div v-else class="ai-work-conversation-list flex-1 overflow-y-auto px-2 py-2">
        <div v-for="group in groupedConversations" :key="group.key" class="mb-2.5 last:mb-0">
          <p class="px-2 pb-1 text-[11px] font-medium text-slate-400 dark:text-accent-500">
            {{ group.label }}
          </p>
          <ul class="space-y-0.5">
            <li
              v-for="conversation in group.items"
              :key="conversation.conversation_id"
              class="group/row rounded-lg transition"
              :class="selectedId === conversation.conversation_id
                ? 'bg-primary-500/12 ring-1 ring-inset ring-primary-500/25 dark:bg-primary-500/15'
                : 'hover:bg-slate-200/60 dark:hover:bg-dark-800/80'"
            >
              <div class="flex items-center gap-1">
                <form
                  v-if="editor?.id === conversation.conversation_id && editor.mode === 'rename'"
                  :id="`rename-${conversation.conversation_id}`"
                  class="min-w-0 flex-1 px-2 py-1.5"
                  @submit.prevent="change(conversation, 'rename')"
                >
                  <input
                    :ref="el => { nameInput = el as HTMLInputElement | undefined }"
                    v-model="name"
                    aria-label="对话名称"
                    maxlength="80"
                    class="w-full min-w-0 rounded-md border border-primary-400 bg-white px-2 py-1 text-xs font-semibold text-slate-900 outline-none dark:bg-dark-900 dark:text-white"
                    :disabled="saving"
                    @keydown.esc.stop.prevent="!saving && (editor = undefined)"
                  >
                </form>
                <button
                  v-else
                  type="button"
                  class="flex min-w-0 flex-1 items-center gap-1.5 px-2 py-1.5 text-left"
                  :title="`${conversationTitle(conversation.conversation_id, conversation.title)}${conversation.updated_at ? `\n更新：${formatTime(conversation.updated_at)}` : ''}`"
                  :data-testid="conversation.temporary
                    ? 'ai-work-conversation-temporary'
                    : `ai-work-conversation-${conversation.conversation_id}`"
                  @click="selectConversation(conversation.conversation_id)"
                >
                  <span
                    v-if="isConversationRunning(conversation)"
                    class="size-1.5 shrink-0 rounded-full bg-primary-500"
                    aria-hidden="true"
                  />
                  <span
                    class="min-w-0 flex-1 truncate text-[13px] leading-5"
                    :class="selectedId === conversation.conversation_id
                      ? 'font-semibold text-slate-900 dark:text-white'
                      : 'text-slate-700 dark:text-accent-200'"
                  >
                    {{ conversationTitle(conversation.conversation_id, conversation.title) }}
                  </span>
                  <span
                    v-if="conversation.temporary"
                    class="shrink-0 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300"
                  >
                    进行中
                  </span>
                  <span
                    v-else
                    class="shrink-0 text-[11px] tabular-nums text-slate-400 dark:text-accent-500"
                    :class="conversation.conversation_id.startsWith(GLOBAL_CHAT_CONVERSATION_PREFIX)
                      ? 'group-hover/row:hidden group-focus-within/row:hidden'
                      : ''"
                  >
                    {{ formatCompactTime(conversation.updated_at || conversation.created_at) }}
                  </span>
                </button>
                <div
                  v-if="!conversation.temporary && conversation.conversation_id.startsWith(GLOBAL_CHAT_CONVERSATION_PREFIX)"
                  class="mr-1 shrink-0 items-center"
                  :class="editor?.id === conversation.conversation_id && editor.mode === 'rename'
                    ? 'flex'
                    : 'hidden group-hover/row:flex group-focus-within/row:flex'"
                >
                  <template v-if="editor?.id === conversation.conversation_id && editor.mode === 'rename'">
                    <WorkspaceHint label="保存名称" content="保存名称">
                      <template #trigger="{ attrs }">
                        <button
                          v-bind="attrs"
                          type="submit"
                          :form="`rename-${conversation.conversation_id}`"
                          class="rounded p-1 text-primary-600 hover:bg-slate-200/70 dark:text-primary-300 dark:hover:bg-dark-700"
                          aria-label="保存名称"
                          :disabled="saving || !name.trim()"
                        >
                          <PhCheck :size="14" />
                        </button>
                      </template>
                    </WorkspaceHint>
                    <WorkspaceHint label="取消重命名" content="取消重命名">
                      <template #trigger="{ attrs }">
                        <button
                          v-bind="attrs"
                          type="button"
                          class="rounded p-1 text-slate-400 hover:bg-slate-200/70 hover:text-slate-700 dark:hover:bg-dark-700 dark:hover:text-white"
                          aria-label="取消重命名"
                          :disabled="saving"
                          @click="editor = undefined"
                        >
                          <PhX :size="14" />
                        </button>
                      </template>
                    </WorkspaceHint>
                  </template>
                  <template v-else>
                    <WorkspaceHint :label="conversation.pinned ? '取消置顶' : '置顶对话'" :content="conversation.pinned ? '取消置顶' : '置顶对话'">
                      <template #trigger="{ attrs }">
                        <button
                          v-bind="attrs"
                          type="button"
                          class="rounded p-1 text-slate-400 hover:bg-slate-200/80 hover:text-slate-800 dark:text-accent-400 dark:hover:bg-dark-700 dark:hover:text-white"
                          :class="{ 'text-primary-600 dark:text-primary-400': conversation.pinned }"
                          :disabled="saving"
                          :aria-label="conversation.pinned ? '取消置顶' : '置顶对话'"
                          :aria-pressed="!!conversation.pinned"
                          @click="change(conversation, conversation.pinned ? 'unpin' : 'pin')"
                        >
                          <PhPushPin :size="14" :weight="conversation.pinned ? 'fill' : 'regular'" />
                        </button>
                      </template>
                    </WorkspaceHint>
                    <WorkspaceHint label="重命名对话" content="重命名对话">
                      <template #trigger="{ attrs }">
                        <button
                          v-bind="attrs"
                          type="button"
                          class="rounded p-1 text-slate-400 hover:bg-slate-200/80 hover:text-slate-800 dark:text-accent-400 dark:hover:bg-dark-700 dark:hover:text-white"
                          aria-label="重命名对话"
                          :disabled="saving"
                          @click="editRow(conversation, 'rename')"
                        >
                          <PhPencilSimple :size="14" />
                        </button>
                      </template>
                    </WorkspaceHint>
                    <WorkspaceHint label="删除对话" content="删除对话">
                      <template #trigger="{ attrs }">
                        <button
                          v-bind="attrs"
                          type="button"
                          class="rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600 dark:text-accent-400 dark:hover:bg-rose-500/15 dark:hover:text-rose-300"
                          aria-label="删除对话"
                          :disabled="saving"
                          @click="editRow(conversation, 'delete')"
                        >
                          <PhTrash :size="14" />
                        </button>
                      </template>
                    </WorkspaceHint>
                  </template>
                </div>
              </div>
              <div v-if="editor?.id === conversation.conversation_id && editor.mode === 'delete'" class="px-2.5 pb-2 pt-1">
                <p class="mb-2 text-xs text-slate-600 dark:text-accent-200">删除这段对话及其消息？删除后无法恢复。</p>
                <div class="flex justify-end gap-1.5">
                  <button type="button" class="rounded-md border border-slate-200 px-2 py-1 text-xs hover:bg-slate-100 dark:border-dark-600 dark:hover:bg-dark-800" :disabled="saving" @click="editor = undefined">取消</button>
                  <button type="button" class="rounded-md border border-rose-300 px-2 py-1 text-xs font-medium text-rose-600 hover:bg-rose-50 dark:border-rose-500/40 dark:text-rose-300 dark:hover:bg-rose-500/15" :disabled="saving" @click="change(conversation, 'delete')">确认删除</button>
                </div>
              </div>
              <p v-if="actionError?.id === conversation.conversation_id" role="alert" class="px-2.5 pb-2 text-xs text-rose-600 dark:text-rose-300">{{ actionError.message }}</p>
            </li>
          </ul>
        </div>
      </div>
    </aside>

    <!-- 右侧无外框对话主区（仅保留一条 44px 单行顶栏，最大化内容视口） -->
    <section class="flex min-w-0 flex-1 flex-col bg-white dark:bg-dark-900">
      <header class="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-slate-200/80 px-4 dark:border-dark-800">
        <div class="flex min-w-0 items-center gap-2">
          <template v-if="sidebarCollapsed">
            <button
              type="button"
              class="flex size-7 shrink-0 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-800 dark:text-accent-300 dark:hover:bg-dark-800 dark:hover:text-white"
              aria-label="展开历史侧栏"
              title="展开历史侧栏"
              data-testid="ai-work-sidebar-toggle"
              @click="sidebarCollapsed = false"
            >
              <PhSidebarSimple :size="16" aria-hidden="true" />
            </button>
            <RouterLink
              :to="workspaceReturnRoute"
              class="inline-flex shrink-0 items-center gap-1 text-xs font-bold text-primary-600 hover:text-primary-500 dark:text-primary-300"
              data-testid="ai-work-back"
            >
              <PhArrowLeft :size="14" aria-hidden="true" />
              工作台
            </RouterLink>
            <span class="text-slate-300 dark:text-dark-700">/</span>
          </template>
          <h1
            v-if="selectedId"
            class="min-w-0 truncate text-sm font-semibold text-slate-900 dark:text-white"
            data-testid="ai-work-selected-id"
            :data-conversation-id="selectedId"
          >
            {{ conversationTitle(selectedId, selectedSummary?.title) }}
          </h1>
          <h1 v-else class="text-sm font-semibold text-slate-900 dark:text-white">
            AI 对话
          </h1>
          <span
            v-if="selectedIsActive && chatStore.isRunning"
            class="shrink-0 rounded-full bg-primary-500/15 px-2 py-0.5 text-[10px] font-semibold text-primary-700 dark:text-primary-300"
          >
            运行中
          </span>
        </div>

        <div class="flex shrink-0 items-center gap-2 text-xs">
          <span
            v-if="selectedId && selectedMetaCompact"
            class="hidden text-slate-400 dark:text-accent-500 md:inline"
            :title="selectedMetaTooltip"
          >
            {{ selectedMetaCompact }}
          </span>
          <button
            v-if="sidebarCollapsed"
            type="button"
            class="inline-flex items-center gap-1 rounded-lg bg-primary-600 px-2.5 py-1 text-xs font-semibold text-white transition hover:bg-primary-500"
            data-testid="ai-work-new-chat"
            @click="startNewChat"
          >
            <PhPlus :size="13" weight="bold" aria-hidden="true" />
            新对话
          </button>
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-50 disabled:opacity-50 dark:border-dark-700 dark:text-accent-200 dark:hover:bg-dark-800"
            data-testid="ai-work-refresh"
            :disabled="refreshBusy"
            @click="refreshConversations"
          >
            <PhArrowsClockwise :size="13" :class="{ 'motion-safe:animate-spin': refreshBusy }" aria-hidden="true" />
            {{ refreshBusy ? '刷新中…' : '刷新' }}
          </button>
        </div>
      </header>

      <!-- 主对话区：铺满剩余全部高度 -->
      <div v-if="!selectedId" class="min-h-0 flex-1">
        <AiChatPanel
          layout="page"
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

      <div v-else data-testid="ai-work-chat-view" class="flex min-h-0 flex-1 flex-col">
        <div v-if="!isGlobalChatSelected" class="w-full shrink-0 px-6 pt-3">
          <AiImageBatchProgress
            :conversation-id="selectedId"
            @change="imageBatch = $event"
          />
        </div>
        <div
          v-if="selectedIsActive"
          class="min-h-0 flex-1"
          data-testid="ai-work-live-chat"
        >
          <AiChatPanel
            layout="page"
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

        <div v-else-if="!imageBatch" class="min-h-0 flex-1 overflow-y-auto px-6 py-4">
          <div class="w-full">
            <div
              v-if="loadingUiMessages"
              class="flex min-h-[360px] items-center justify-center text-sm text-slate-500 dark:text-accent-300"
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
              <p class="mb-3 text-xs text-slate-400 dark:text-accent-500" data-testid="ai-work-history-readonly">
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

              <p v-if="!isGlobalChatSelected" class="mt-4 text-xs text-slate-400 dark:text-accent-500">
                这是任务记录，无法在此继续对话。
              </p>
            </template>
          </div>
        </div>
      </div>
    </section>
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
