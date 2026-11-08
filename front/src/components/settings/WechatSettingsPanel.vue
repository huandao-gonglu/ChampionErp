<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { fetchWechatStatus, saveWechatForwardTypes, startWechatLogin, unbindWechat, verifyWechatLogin, type WechatForwardType } from '@/api/wechat'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import type { BackendWechatChannelStatus } from '@/types/workflow.generated'

const state = ref<BackendWechatChannelStatus | null>(null)
const forwardOptions: Array<{ value: WechatForwardType; title: string; help: string }> = [
  { value: 'reply', title: '回复与结果', help: '普通回答、追问、要求补充信息和最终答复。' },
  { value: 'progress', title: '中途说明', help: 'ERP 在执行过程中已经生成的完整说明和阶段结果。' },
  { value: 'approval', title: '审批提示', help: '需要审批时，提示前往 ERP 网页处理。' },
  { value: 'error', title: '失败或中断提示', help: '运行失败、已停止或输入无法处理的提示。' },
  { value: 'command', title: '/new 确认', help: '确认已创建并绑定新对话；关闭此提示仍会执行 /new。' },
]
const selectedForwardTypes = ref<WechatForwardType[]>([])
const forwardingDirty = computed(() => forwardOptions.some(option => (
  selectedForwardTypes.value.includes(option.value) !== (state.value?.forward_types.includes(option.value) ?? false)
)))
const qrOpen = ref(false)
const code = ref('')
const busy = ref(false)
const error = ref('')
const statusLabel = computed(() => statusLabels[state.value?.status || 'unbound'])
let disposed = false
let timer: ReturnType<typeof setTimeout> | undefined
let controller: AbortController | undefined
let revision = 0

function apply(value: BackendWechatChannelStatus) {
  const initial = state.value === null
  const syncForwarding = initial || !forwardingDirty.value
  state.value = value
  if (syncForwarding) selectedForwardTypes.value = [...value.forward_types]
  if (value.bound) qrOpen.value = false
  else if (initial && value.qr_image) qrOpen.value = true
}
function login() {
  qrOpen.value = true
  void perform(startWechatLogin)
}
async function load() {
  clearTimeout(timer)
  if (busy.value || disposed) return
  controller?.abort()
  const request = new AbortController()
  controller = request
  const current = ++revision
  try {
    const value = await fetchWechatStatus(request.signal)
    if (!disposed && current === revision) { apply(value); error.value = '' }
  } catch (exc) {
    if (!disposed && !request.signal.aborted && current === revision) error.value = exc instanceof Error ? exc.message : '微信设置操作失败'
  } finally {
    if (!disposed && !busy.value && current === revision) timer = setTimeout(load, 2000)
  }
}
async function perform(action: () => Promise<BackendWechatChannelStatus>) {
  if (busy.value) return
  busy.value = true
  revision++
  controller?.abort()
  clearTimeout(timer)
  error.value = ''
  try {
    const value = await action()
    if (!disposed) { code.value = ''; apply(value) }
  } catch (exc) {
    if (!disposed) error.value = exc instanceof Error ? exc.message : '微信设置操作失败'
  } finally {
    busy.value = false
    if (!disposed) timer = setTimeout(load, 2000)
  }
}
onMounted(load)
onBeforeUnmount(() => { disposed = true; revision++; clearTimeout(timer); controller?.abort() })

const statusLabels: Record<string, string> = {
  unbound: '未绑定',
  connecting: '正在连接',
  connected: '已连接',
  logging_in: '正在获取二维码',
  waiting_scan: '等待扫码',
  waiting_confirm: '等待微信确认',
  need_verifycode: '等待输入配对码',
  login_expired: '二维码已失效',
  expired: '需要重新绑定',
  reconnecting: '正在重连',
  conversation_missing: '请在微信发送 /new 新建对话',
  error: '连接失败',
}
</script>

<template>
  <section class="space-y-5 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70" aria-labelledby="wechat-settings-title">
    <h3 id="wechat-settings-title" class="font-semibold">微信连接</h3>
    <p class="muted text-sm">使用本人微信扫码，绑定成功后自动创建 ERP 对话。网页和微信使用同一份对话记录。</p>
    <p v-if="!state && !error" role="status">正在读取微信连接…</p>
    <p v-if="error || state?.error" role="alert" class="text-sm text-red-600 dark:text-red-400">{{ error || state?.error }} <button type="button" class="underline" :disabled="busy" @click="load">重新读取</button></p>
    <template v-if="state">
      <p role="status" class="text-sm font-medium">{{ statusLabel }}</p>
      <div class="flex flex-wrap gap-3">
        <button type="button" class="btn btn-primary" :disabled="busy" data-testid="wechat-login" @click="login">{{ busy ? '处理中…' : state.bound ? '重新绑定' : '扫码绑定' }}</button>
        <button v-if="state.qr_image" type="button" class="btn btn-secondary" @click="qrOpen = true">显示二维码</button>
        <button v-if="state.bound || state.qr_image || state.status === 'logging_in'" type="button" class="btn btn-secondary" :disabled="busy" data-testid="wechat-unbind" @click="perform(unbindWechat)">解绑微信</button>
      </div>
      <p class="text-sm">在微信发送 /new，即可新建对话并自动切换；原对话记录保留在 AI Work 中。</p>
      <p v-if="state.pending_deliveries" class="muted text-sm">{{ `待发送消息：${state.pending_deliveries}` }}</p>
      <p class="muted text-sm">沿用 ERP 当前的操作权限与审批设置。需要审批时，请在 ERP 网页确认。</p>
      <form class="space-y-3 border-t border-accent-200 pt-4 dark:border-dark-700" data-testid="wechat-forwarding" @submit.prevent="perform(() => saveWechatForwardTypes([...selectedForwardTypes]))">
        <fieldset class="space-y-3" :disabled="busy">
          <legend class="mb-2 font-semibold">转发到微信的消息</legend>
          <p class="muted text-sm">完整文字产生后发送。保存后生效，只影响消息转发，不影响 ERP 执行。</p>
          <label v-for="option in forwardOptions" :key="option.value" class="flex cursor-pointer items-start gap-3 text-sm">
            <input v-model="selectedForwardTypes" type="checkbox" :value="option.value" :data-testid="`wechat-forward-${option.value}`" class="mt-1 h-4 w-4 accent-primary-600" />
            <span><span class="block font-medium">{{ option.title }}</span><span class="muted mt-1 block">{{ option.help }}</span></span>
          </label>
        </fieldset>
        <p class="muted text-sm">思考过程、工具参数及原始回执、逐字片段和网页用户输入不转发。关闭某类后会跳过该类待发消息，重新开启不补发。</p>
        <p v-if="selectedForwardTypes.length === 0" role="status" class="text-sm text-amber-700 dark:text-amber-300">全部关闭并保存后，微信发送的文字仍会交给 ERP 处理，但不会收到回复。</p>
        <button type="submit" class="btn btn-primary" :disabled="busy || !forwardingDirty" data-testid="wechat-save-forwarding">{{ busy ? '处理中…' : '保存转发设置' }}</button>
      </form>
    </template>
  </section>
  <WorkspaceDialog v-if="qrOpen" :open="true" title="微信绑定二维码" width="28rem" :close-disabled="busy" @close="qrOpen = false">
    <template #close>关闭</template>
    <div class="space-y-4">
      <p v-if="error || state?.error" role="alert" class="text-sm text-red-600 dark:text-red-400">{{ error || state?.error }}</p>
      <img v-if="state?.qr_image" :src="state.qr_image" alt="微信绑定二维码" class="mx-auto h-64 w-64 rounded-lg bg-white p-2" />
      <p v-else-if="!error && !state?.error" role="status">正在获取二维码…</p>
      <p class="text-sm">{{ state?.qr_image ? '请使用本人微信扫码，并按微信提示确认。' : statusLabel }}</p>
      <form v-if="state?.status === 'need_verifycode'" class="space-y-3" @submit.prevent="perform(() => verifyWechatLogin(code))">
        <label for="wechat-code" class="block space-y-2 text-sm"><span>微信配对码</span><input id="wechat-code" v-model="code" class="input" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]+" maxlength="12" required :disabled="busy" /></label>
        <button type="submit" class="btn btn-primary" :disabled="busy || !/^\d{1,12}$/.test(code)">提交配对码</button>
      </form>
      <button v-if="error || state?.status === 'login_expired' || state?.status === 'error'" type="button" class="btn btn-secondary" :disabled="busy" @click="login">重新获取二维码</button>
    </div>
  </WorkspaceDialog>
</template>
