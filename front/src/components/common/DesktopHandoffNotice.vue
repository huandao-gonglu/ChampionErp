<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { hasLocalOperations } from '@/utils/desktop'
import { useRouter } from 'vue-router'
import { useAccountStore } from '@/stores/account'
import { desktopBackendOrigin, fetchDesktopInstallers, resolveDesktopHandoff } from '@/api/desktopHandoff'
import { useAiChatStore } from '@/stores/aiChat'
import { fetchUiMessages } from '@/api/aiWork'
import { continueImageOperation } from '@/api/draftImages'
import { withAiConversation } from '@/services/withAiConversation'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { desktopLaunch, receivedDesktopHandoff, dismissDesktopLaunch, retryDesktopLaunch, resetDesktopHandoff, acknowledgeDesktopHandoff, type DesktopHandoffState } from '@/utils/desktopHandoff'

const desktopShell = hasLocalOperations()
const development = import.meta.env.DEV
const router = useRouter()
const account = useAccountStore()
const chat = useAiChatStore()
const downloadsOpen = ref(false)
const installers = ref<{ label: string; version: string; url: string }[]>([])
const downloadError = ref('')
const loading = ref(false)
const receiveError = ref('')
const canRetryReceive = ref(false)
const pending = ref('')
const receiveAttempt = ref(0)
let unsubscribe: (() => void) | undefined
let generation = 0

async function showDownloads() {
  downloadsOpen.value = true
  loading.value = true
  downloadError.value = ''
  try {
    const items = await fetchDesktopInstallers()
    installers.value = items.map(item => ({ ...item, url: new URL(item.url, desktopBackendOrigin()).href }))
    if (!installers.value.length) downloadError.value = '暂无已发布的安装包，请联系管理员。'
  } catch (error) { downloadError.value = error instanceof Error ? error.message : '安装包读取失败，请重试。' }
  finally { loading.value = false }
}

watch(() => account.user?.id, (id, previous) => {
  if (previous && previous !== id) resetDesktopHandoff()
})
watch(() => desktopLaunch.value?.status, status => { if (status === 'opened') downloadsOpen.value = false })
watch([receiveAttempt, () => account.user?.id, () => router.currentRoute.value.meta.public === true], async () => {
  const token = ++generation
  const id = pending.value
  if (!id || !account.user || router.currentRoute.value.meta.public) return
  receiveError.value = ''
  canRetryReceive.value = false
  let resolved = false
  try {
    const data = await resolveDesktopHandoff(id)
    if (token !== generation) return
    resolved = true
    if (data.opened) {
      await window.championDesktop?.finishHandoff?.(id)
      if (receivedDesktopHandoff.value?.requestId === id) receivedDesktopHandoff.value = null
      return
    }
    const state = data.state as DesktopHandoffState
    if (state.chat) {
      const conversation = state.chat.conversationId
      if (conversation) {
        const history = await fetchUiMessages(conversation)
        if (token !== generation) return
        chat.openConversation(history)
      } else chat.startConversation()
    }
    receivedDesktopHandoff.value = { requestId: id, target: data.target, state }
    await router.push(data.target)
    if (token !== generation) return
    if (state.chat) {
      chat.restoreDesktopInput(state.chat)
      await acknowledgeDesktopHandoff(id, 'chat')
    }
    const imageOperation = state.imageOperation
    if (imageOperation) {
      await withAiConversation('继续 AI 图片处理', conversationId => continueImageOperation(imageOperation, conversationId))
      if (token !== generation) return
    }
    // 工作台负责恢复商品/草稿，会话页负责恢复输入；普通设置页在路由完成后即可确认。
    if (!data.state.workflow && !data.state.chat && !data.state.modelSettings) await acknowledgeDesktopHandoff(id)
  } catch (error) {
    if (token !== generation) return
    const failure = error as { status?: number; code?: string } | null
    const received = receivedDesktopHandoff.value
    const canResumeImage = received?.requestId === id && Boolean(received.state.imageOperation)
    canRetryReceive.value = (!resolved || canResumeImage)
      && (!failure?.status || failure.status >= 500 || failure.code === 'DESKTOP_UNAVAILABLE')
    receiveError.value = error instanceof Error ? error.message : '页面接收失败，请回网页重试。'
  }
})
onMounted(async () => {
  function receive(id: string) { pending.value = id; ++receiveAttempt.value }
  unsubscribe = window.championDesktop?.onHandoff?.(receive)
  const id = await window.championDesktop?.pendingHandoff?.()
  if (id) receive(id)
})
onBeforeUnmount(() => { ++generation; unsubscribe?.() })
</script>

<template>
  <aside v-if="!desktopShell && desktopLaunch && !downloadsOpen" role="status" aria-label="打开桌面端" class="fixed bottom-5 right-5 z-[100] max-w-sm rounded-xl border border-accent-200 bg-white p-4 shadow-lg dark:border-dark-700 dark:bg-dark-900">
    <div class="flex items-start justify-between gap-4">
      <p>{{ desktopLaunch.status === 'opened' ? '已在桌面端打开' : desktopLaunch.status === 'opening' ? '正在打开桌面端…' : desktopLaunch.error || (development ? '请在开发桌面登录同一账号。窗口未打开时可以重试。' : '没有打开？可以重试或下载安装。') }}</p>
      <button type="button" class="text-accent-500" aria-label="关闭打开提示" @click="dismissDesktopLaunch">×</button>
    </div>
    <div v-if="desktopLaunch.status === 'waiting' || desktopLaunch.status === 'error'" class="mt-3 flex gap-3">
      <button type="button" class="btn btn-primary" @click="retryDesktopLaunch">重试</button>
      <button v-if="!development" type="button" class="btn btn-outline" @click="showDownloads">下载安装</button>
    </div>
  </aside>
  <aside v-if="receiveError" role="alert" class="fixed bottom-5 right-5 z-[100] max-w-sm rounded-xl border bg-white p-4 shadow-lg dark:bg-dark-900">
    <p>{{ receiveError }}</p>
    <div class="mt-3 flex gap-3">
      <button v-if="canRetryReceive" class="btn btn-primary" @click="receiveAttempt++">重试</button>
      <button class="btn btn-outline" @click="receiveError = ''">关闭</button>
    </div>
  </aside>
  <WorkspaceDialog :open="downloadsOpen" title="安装桌面端" @close="downloadsOpen = false">
    <div class="space-y-4">
      <p>选择适合这台电脑的安装包。安装完成后，回到网页点击“重试”。</p>
      <p v-if="loading" role="status">正在读取安装包…</p>
      <p v-if="downloadError" role="alert">{{ downloadError }}</p>
      <a v-for="item in installers" :key="item.url" class="btn btn-primary block" :href="item.url">{{ item.label }} · {{ item.version }}</a>
      <button v-if="downloadError" class="btn btn-outline" @click="showDownloads">重新读取</button>
    </div>
  </WorkspaceDialog>
</template>
