<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  fetchPydanticConversation,
  fetchPydanticConversations,
} from '@/api/aiWork'
import JsonTreeNode from '@/components/ai-work/JsonTreeNode.vue'
import type {
  PydanticConversationDetailResponse,
  PydanticConversationSummary,
} from '@/types/aiWork'

type InspectorMode = 'tree' | 'raw'

const route = useRoute()
const conversations = ref<PydanticConversationSummary[]>([])
const selectedId = ref('')
const selectedDetail = ref<PydanticConversationDetailResponse | null>(null)
const loadingList = ref(false)
const loadingDetail = ref(false)
const listError = ref('')
const detailError = ref('')
const jsonError = ref('')
const downloadError = ref('')
const inspectorMode = ref<InspectorMode>('tree')

let listRequestGeneration = 0
let detailRequestGeneration = 0

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

async function selectConversation(conversationId: string): Promise<void> {
  if (!conversationId) return
  selectedId.value = conversationId
  await loadConversation(conversationId)
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
    conversations.value = nextConversations

    const currentStillExists = nextConversations.some(
      (conversation) => conversation.conversation_id === selectedId.value,
    )
    const requestedId = requestedConversationId.value
    const nextId = currentStillExists
      ? selectedId.value
      : requestedId || nextConversations[0]?.conversation_id || ''

    if (!nextId) {
      detailRequestGeneration += 1
      selectedId.value = ''
      selectedDetail.value = null
      detailError.value = ''
      jsonError.value = ''
      return
    }
    selectedId.value = nextId
    await loadConversation(nextId)
  } catch (cause) {
    if (generation !== listRequestGeneration) return
    listError.value = formatError(cause, '读取 conversation 列表失败')
  } finally {
    if (generation === listRequestGeneration) loadingList.value = false
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
            <span class="rounded-full bg-violet-100 px-2.5 py-1 text-[11px] font-black text-violet-700 dark:bg-violet-500/15 dark:text-violet-200">
              只读检查器
            </span>
          </div>
          <h1 class="mt-2 text-2xl font-black tracking-tight">Pydantic 对话消息</h1>
          <p class="mt-1 text-sm text-slate-500 dark:text-accent-300">
            直接查看已保存的 ModelMessage JSON，不进行业务语义转换。
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
            <h2 class="text-sm font-black">Conversations</h2>
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

        <div v-if="!conversations.length && !loadingList && !listError" class="p-8 text-center">
          <p class="text-sm font-bold">暂无对话记录</p>
          <p class="mt-2 text-xs text-slate-500 dark:text-accent-300">
            Agent 保存 Pydantic message history 后会出现在这里。
          </p>
        </div>

        <ul v-else class="max-h-[calc(100vh-230px)] divide-y divide-slate-100 overflow-auto dark:divide-dark-800">
          <li v-for="conversation in conversations" :key="conversation.conversation_id">
            <button
              type="button"
              class="w-full px-4 py-3 text-left transition hover:bg-slate-50 dark:hover:bg-dark-800"
              :class="selectedId === conversation.conversation_id ? 'bg-primary-50 dark:bg-primary-500/10' : ''"
              :data-testid="`ai-work-conversation-${conversation.conversation_id}`"
              @click="selectConversation(conversation.conversation_id)"
            >
              <code class="block truncate text-xs font-bold text-slate-800 dark:text-accent-100">
                {{ conversation.conversation_id }}
              </code>
              <span class="mt-2 block text-[11px] text-slate-500 dark:text-accent-300">
                更新：{{ formatTime(conversation.updated_at) }}
              </span>
              <span class="mt-0.5 block text-[11px] text-slate-400">
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
            <p class="mt-2 text-sm text-slate-500 dark:text-accent-300">详情将以通用 JSON tree 展示。</p>
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

              <div v-if="selectedDetail" class="flex flex-wrap items-center gap-2">
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
              </div>
            </div>
          </header>

          <div class="p-5">
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
                role="tabpanel"
                data-testid="ai-work-json-tree"
                class="max-h-[calc(100vh-300px)] min-h-[480px] overflow-auto rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-dark-700 dark:bg-dark-950"
              >
                <JsonTreeNode :value="selectedDetail.messages" label="messages" />
              </div>

              <pre
                v-else
                role="tabpanel"
                data-testid="ai-work-raw-json"
                class="max-h-[calc(100vh-300px)] min-h-[480px] overflow-auto whitespace-pre-wrap break-words rounded-xl bg-slate-950 p-4 font-mono text-xs leading-6 text-slate-100"
              >{{ rawJson }}</pre>

              <p class="mt-3 text-xs text-slate-500 dark:text-accent-300">
                此页面只遍历 JSON 的数组、对象和标量，不解释消息类型、part 或工具调用。
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
