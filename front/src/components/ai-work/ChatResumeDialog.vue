<script setup lang="ts">
import { ref, watch } from 'vue'
import { useAiChatStore } from '@/stores/aiChat'
import { fetchPydanticConversations, fetchUiMessages } from '@/api/aiWork'
import type { PydanticConversationSummary } from '@/types/aiWork'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'

const chat = useAiChatStore()
const rows = ref<PydanticConversationSummary[]>([])
const loading = ref(false)
const opening = ref('')
const error = ref('')
const pageSize = 50
const hasMore = ref(false)
let offset = 0
let session: AbortController | undefined

async function loadPage() {
  if (loading.value || !session) return
  const { signal } = session
  loading.value = true
  error.value = ''
  try {
    const data = await fetchPydanticConversations(pageSize, 'chats', offset, signal)
    if (signal.aborted) return
    if (!data.ok || !Array.isArray(data.conversations)) throw new Error('读取对话列表失败')
    const combined = new Map(rows.value.map(row => [row.conversation_id, row]))
    for (const row of data.conversations) combined.set(row.conversation_id, row)
    rows.value = [...combined.values()]
    offset += data.conversations.length
    hasMore.value = data.conversations.length === pageSize
  } catch {
    if (!signal.aborted) error.value = '对话列表读取失败，请重试。'
  } finally {
    if (!signal.aborted) loading.value = false
  }
}

async function select(id: string) {
  if (opening.value || !session) return
  const previous = chat.activeConversationId
  if (id === previous) { chat.resumeOpen = false; return }
  const { signal } = session
  error.value = ''
  opening.value = id
  try {
    const detail = await fetchUiMessages(id, signal)
    if (signal.aborted || chat.activeConversationId !== previous) return
    if (!detail.ok || detail.conversation_id !== id || !Array.isArray(detail.messages)) throw new Error('对话记录无效')
    chat.openConversation(detail)
    chat.resumeOpen = false
  } catch {
    if (!signal.aborted) error.value = '未能打开这段对话，请重新选择。'
  } finally {
    if (!signal.aborted) opening.value = ''
  }
}

watch(() => chat.resumeOpen, (open, _, cleanup) => {
  rows.value = []
  opening.value = ''
  error.value = ''
  loading.value = false
  hasMore.value = false
  offset = 0
  if (open) {
    const controller = new AbortController()
    session = controller
    cleanup(() => { controller.abort(); session = undefined })
    void loadPage()
  }
}, { immediate: true, flush: 'sync' })
</script>

<template>
  <WorkspaceDialog :open="chat.resumeOpen" title="切换对话" width="32rem" @close="chat.resumeOpen = false">
    <ul v-if="rows.length" class="divide-y divide-slate-200 dark:divide-dark-700">
      <li v-for="row in rows" :key="row.conversation_id">
        <button type="button" class="w-full rounded-lg px-3 py-2 text-left hover:bg-slate-100 dark:hover:bg-dark-800"
          :disabled="!!opening" @click="select(row.conversation_id)">
          <span class="flex items-center justify-between gap-3">
            <span class="truncate font-medium">{{ (row.conversation_id === chat.activeConversationId && chat.activeTitle !== '新对话' && chat.activeTitle) || row.title || '新对话' }}</span>
            <span v-if="row.conversation_id === chat.activeConversationId" class="shrink-0 text-xs text-primary-600">当前对话</span>
          </span>
          <span class="text-xs text-slate-500">{{ opening === row.conversation_id ? '正在打开…' : new Date(row.updated_at).toLocaleString() }}</span>
        </button>
      </li>
    </ul>
    <p v-if="loading" role="status" class="p-3 text-sm">正在读取对话…</p>
    <p v-else-if="!rows.length && !error" class="p-3 text-sm">还没有可继续的对话。</p>
    <p v-if="error" role="alert" class="p-3 text-sm text-rose-600">{{ error }}</p>
    <button v-if="!loading && (hasMore || (!rows.length && error))" type="button" class="btn btn-outline mt-2" @click="loadPage">
      {{ error ? '重试' : '加载更多' }}
    </button>
  </WorkspaceDialog>
</template>
