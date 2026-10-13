<script setup lang="ts">
import { computed, ref } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { fetchOrderChatHistory, sendOrderChatMessage } from '@/api/orderChat'
import type { BackendOrderChatMessage } from '@/types/workflow.generated'

const props = defineProps<{ orderId: string; orderNumber: string }>()
defineEmits<{ close: [] }>()
const messages = ref<BackendOrderChatMessage[]>([])
const draft = ref('')
const loading = ref(false)
const sending = ref(false)
const loaded = ref(false)
const error = ref('')
const notice = ref('')
const busy = computed(() => loading.value || sending.value)
const length = computed(() => Array.from(draft.value).length)
const senderNames: Record<string, string> = { PARTNER: '店铺', CUSTOMER: '买家', MARKET: 'Yandex 系统', SUPPORT: '平台客服' }

function messageTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

async function refresh() {
  if (busy.value) return
  loading.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await fetchOrderChatHistory(props.orderId)
    messages.value = result.messages
    loaded.value = true
    notice.value = '消息已更新'
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '获取消息失败，请重试'
  } finally {
    loading.value = false
  }
}

async function send() {
  if (busy.value || !draft.value.trim() || length.value > 4096) return
  sending.value = true
  error.value = ''
  notice.value = ''
  try {
    await sendOrderChatMessage(props.orderId, draft.value)
    draft.value = ''
    notice.value = '消息已发送，点击“获取最新消息”查看最新对话'
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '发送未完成，请获取最新消息核对后再操作'
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <WorkspaceDialog :open="true" title="联系买家" width="680px" class="order-ui" :close-disabled="busy" @close="$emit('close')">
    <div class="order-row mb-4 gap-3">
      <p class="order-muted">订单 {{ orderNumber }}</p>
      <button type="button" class="order-button" :disabled="busy" @click="refresh">{{ loading ? '正在获取…' : '获取最新消息' }}</button>
    </div>
    <p v-if="error" role="alert" class="order-error mb-3">{{ error }}</p>
    <p v-if="notice" role="status" class="order-muted mb-3">{{ notice }}</p>
    <div class="chat-history" :aria-busy="loading" aria-label="买家聊天记录">
      <p v-if="!loaded" class="order-muted p-5">点击“获取最新消息”查看聊天记录。</p>
      <p v-else-if="!messages.length" class="order-muted p-5">暂无聊天记录，可以发送第一条消息。</p>
      <ol v-else class="space-y-3 p-3">
        <li v-for="message in messages" :key="message.id" class="chat-message" :class="{ 'chat-own': message.sender === 'PARTNER' }">
          <div class="order-muted mb-2 text-xs">{{ senderNames[message.sender] || '平台消息' }} · {{ messageTime(message.created_at) }}</div>
          <p v-if="message.text" class="whitespace-pre-wrap break-words">{{ message.text }}</p>
          <a v-for="(attachment, index) in message.attachments" :key="index" :href="attachment.url" target="_blank" rel="noopener noreferrer" class="order-link block mt-2">{{ attachment.name }}</a>
        </li>
      </ol>
    </div>
    <form class="mt-4" @submit.prevent="send">
      <label for="order-buyer-message" class="block mb-2 text-sm">发送给买家</label>
      <textarea id="order-buyer-message" v-model="draft" class="input chat-input" rows="3" :disabled="busy" placeholder="输入订单相关消息" />
      <div class="order-row mt-3 gap-3">
        <span class="order-muted text-xs" :class="{ 'text-red-600': length > 4096 }">{{ length }} / 4096</span>
        <button type="submit" class="order-button order-primary" :disabled="busy || !draft.trim() || length > 4096">{{ sending ? '正在发送…' : '发送消息' }}</button>
      </div>
    </form>
  </WorkspaceDialog>
</template>

<style scoped>
.chat-history { height: clamp(280px, 45dvh, 420px); overflow-y: auto; border: 1px solid var(--order-border); border-radius: 8px; }
.chat-input { height: 96px; min-height: 96px; max-height: 144px; resize: vertical; overflow-y: auto; }
.chat-message { max-width: 92%; padding: 12px; border-radius: 8px; background: var(--order-soft); overflow-wrap: anywhere; font-size: 14px; }
.chat-own { margin-left: auto; border: 1px solid var(--order-border); }
</style>
