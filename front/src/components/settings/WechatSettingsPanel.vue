<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { fetchWechatStatus, saveWechatForwardTypes, startWechatLogin, unbindWechat, verifyWechatLogin, type WechatForwardType } from '@/api/wechat'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import type { BackendWechatChannelStatus } from '@/types/workflow.generated'

const { t } = useI18n({ useScope: 'local', messages: {
  zh: {
    title: '微信连接', help: '使用本人微信扫码，绑定成功后自动创建 ERP 对话。网页和微信使用同一份对话记录。',
    newHelp: '在微信发送 /new，即可新建对话并自动切换；原对话记录保留在 AI Work 中。',
    login: '扫码绑定', rebind: '重新绑定', unbind: '解绑微信', qr: '微信绑定二维码', scan: '请使用本人微信扫码，并按微信提示确认。',
    qrLoading: '正在获取二维码…', qrRetry: '重新获取二维码', showQr: '显示二维码', close: '关闭',
    code: '微信配对码', verify: '提交配对码', approval: '沿用 ERP 当前的操作权限与审批设置。需要审批时，请在 ERP 网页确认。',
    delivery: '待发送消息：{count}', loading: '正在读取微信连接…', retry: '重新读取', failed: '微信设置操作失败',
    unbound: '未绑定', connecting: '正在连接', connected: '已连接', logging_in: '正在获取二维码', waiting_scan: '等待扫码',
    waiting_confirm: '等待微信确认', need_verifycode: '等待输入配对码', login_expired: '二维码已失效', expired: '需要重新绑定',
    reconnecting: '正在重连', conversation_missing: '请在微信发送 /new 新建对话', error: '连接失败', busy: '处理中…',
    forwarding: '转发到微信的消息', forwardingHelp: '完整文字产生后发送。保存后生效，只影响消息转发，不影响 ERP 执行。',
    replyTitle: '回复与结果', replyHelp: '普通回答、追问、要求补充信息和最终答复。',
    progressTitle: '中途说明', progressHelp: 'ERP 在执行过程中已经生成的完整说明和阶段结果。',
    approvalTitle: '审批提示', approvalHelp: '需要审批时，提示前往 ERP 网页处理。',
    errorTitle: '失败或中断提示', errorHelp: '运行失败、已停止或输入无法处理的提示。',
    commandTitle: '/new 确认', commandHelp: '确认已创建并绑定新对话；关闭此提示仍会执行 /new。',
    saveForwarding: '保存转发设置', excluded: '思考过程、工具参数及原始回执、逐字片段和网页用户输入不转发。关闭某类后会跳过该类待发消息，重新开启不补发。',
    forwardingEmpty: '全部关闭并保存后，微信发送的文字仍会交给 ERP 处理，但不会收到回复。',
  },
  en: {
    title: 'WeChat connection', help: 'Scan with your own WeChat account. Binding automatically creates an ERP conversation with shared web and WeChat history.',
    newHelp: 'Send /new in WeChat to create and switch to a new conversation. Previous conversations stay in AI Work.',
    login: 'Scan to bind', rebind: 'Bind again', unbind: 'Unbind WeChat', qr: 'WeChat binding QR code', scan: 'Scan with your own WeChat account and confirm in WeChat.',
    qrLoading: 'Getting QR code…', qrRetry: 'Get another QR code', showQr: 'Show QR code', close: 'Close',
    code: 'WeChat pairing code', verify: 'Submit pairing code', approval: 'ERP permissions and approval settings apply. Approve pending operations in the ERP web app.',
    delivery: 'Pending messages: {count}', loading: 'Loading WeChat connection…', retry: 'Reload', failed: 'Could not update WeChat settings',
    unbound: 'Not bound', connecting: 'Connecting', connected: 'Connected', logging_in: 'Getting QR code', waiting_scan: 'Waiting for scan',
    waiting_confirm: 'Waiting for confirmation', need_verifycode: 'Pairing code required', login_expired: 'QR code expired', expired: 'Binding expired',
    reconnecting: 'Reconnecting', conversation_missing: 'Send /new in WeChat', error: 'Connection failed', busy: 'Working…',
    forwarding: 'Messages forwarded to WeChat', forwardingHelp: 'Send complete text when it is available. Choices take effect after saving and only affect forwarding, not ERP execution.',
    replyTitle: 'Replies and results', replyHelp: 'Answers, questions, requests for information, and final results.',
    progressTitle: 'Intermediate updates', progressHelp: 'Complete explanations and interim results already written by ERP during execution.',
    approvalTitle: 'Approval notices', approvalHelp: 'Prompt you to approve an operation in the ERP web app.',
    errorTitle: 'Failure or interruption notices', errorHelp: 'Notices for failed runs, stopped operations, or inputs that cannot be processed.',
    commandTitle: '/new confirmation', commandHelp: 'Confirm that a new conversation is created and bound. /new still runs when this notice is disabled.',
    saveForwarding: 'Save forwarding settings', excluded: 'Reasoning, tool arguments and raw results, text fragments, and web user messages are excluded. Disabling a type skips its queued messages; enabling it again does not resend them.',
    forwardingEmpty: 'After saving with all types disabled, WeChat text still goes to ERP, but no replies will be sent.',
  },
} })
const state = ref<BackendWechatChannelStatus | null>(null)
const forwardOptions: Array<{ value: WechatForwardType; title: string; help: string }> = [
  { value: 'reply', title: 'replyTitle', help: 'replyHelp' },
  { value: 'progress', title: 'progressTitle', help: 'progressHelp' },
  { value: 'approval', title: 'approvalTitle', help: 'approvalHelp' },
  { value: 'error', title: 'errorTitle', help: 'errorHelp' },
  { value: 'command', title: 'commandTitle', help: 'commandHelp' },
]
const selectedForwardTypes = ref<WechatForwardType[]>([])
const forwardingDirty = computed(() => forwardOptions.some(option => (
  selectedForwardTypes.value.includes(option.value) !== (state.value?.forward_types.includes(option.value) ?? false)
)))
const qrOpen = ref(false)
const code = ref('')
const busy = ref(false)
const error = ref('')
const statusLabel = computed(() => t(state.value?.status || 'unbound'))
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
    if (!disposed && !request.signal.aborted && current === revision) error.value = exc instanceof Error ? exc.message : t('failed')
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
    if (!disposed) error.value = exc instanceof Error ? exc.message : t('failed')
  } finally {
    busy.value = false
    if (!disposed) timer = setTimeout(load, 2000)
  }
}
onMounted(load)
onBeforeUnmount(() => { disposed = true; revision++; clearTimeout(timer); controller?.abort() })
</script>

<template>
  <section class="space-y-5 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70" aria-labelledby="wechat-settings-title">
    <h3 id="wechat-settings-title" class="font-semibold">{{ t('title') }}</h3>
    <p class="muted text-sm">{{ t('help') }}</p>
    <p v-if="!state && !error" role="status">{{ t('loading') }}</p>
    <p v-if="error || state?.error" role="alert" class="text-sm text-red-600 dark:text-red-400">{{ error || state?.error }} <button type="button" class="underline" :disabled="busy" @click="load">{{ t('retry') }}</button></p>
    <template v-if="state">
      <p role="status" class="text-sm font-medium">{{ statusLabel }}</p>
      <div class="flex flex-wrap gap-3">
        <button type="button" class="btn btn-primary" :disabled="busy" data-testid="wechat-login" @click="login">{{ busy ? t('busy') : state.bound ? t('rebind') : t('login') }}</button>
        <button v-if="state.qr_image" type="button" class="btn btn-secondary" @click="qrOpen = true">{{ t('showQr') }}</button>
        <button v-if="state.bound || state.qr_image || state.status === 'logging_in'" type="button" class="btn btn-secondary" :disabled="busy" data-testid="wechat-unbind" @click="perform(unbindWechat)">{{ t('unbind') }}</button>
      </div>
      <p class="text-sm">{{ t('newHelp') }}</p>
      <p v-if="state.pending_deliveries" class="muted text-sm">{{ t('delivery', { count: state.pending_deliveries }) }}</p>
      <p class="muted text-sm">{{ t('approval') }}</p>
      <form class="space-y-3 border-t border-accent-200 pt-4 dark:border-dark-700" data-testid="wechat-forwarding" @submit.prevent="perform(() => saveWechatForwardTypes([...selectedForwardTypes]))">
        <fieldset class="space-y-3" :disabled="busy">
          <legend class="mb-2 font-semibold">{{ t('forwarding') }}</legend>
          <p class="muted text-sm">{{ t('forwardingHelp') }}</p>
          <label v-for="option in forwardOptions" :key="option.value" class="flex cursor-pointer items-start gap-3 text-sm">
            <input v-model="selectedForwardTypes" type="checkbox" :value="option.value" :data-testid="`wechat-forward-${option.value}`" class="mt-1 h-4 w-4 accent-primary-600" />
            <span><span class="block font-medium">{{ t(option.title) }}</span><span class="muted mt-1 block">{{ t(option.help) }}</span></span>
          </label>
        </fieldset>
        <p class="muted text-sm">{{ t('excluded') }}</p>
        <p v-if="selectedForwardTypes.length === 0" role="status" class="text-sm text-amber-700 dark:text-amber-300">{{ t('forwardingEmpty') }}</p>
        <button type="submit" class="btn btn-primary" :disabled="busy || !forwardingDirty" data-testid="wechat-save-forwarding">{{ busy ? t('busy') : t('saveForwarding') }}</button>
      </form>
    </template>
  </section>
  <WorkspaceDialog v-if="qrOpen" :open="true" :title="t('qr')" width="28rem" :close-disabled="busy" @close="qrOpen = false">
    <template #close>{{ t('close') }}</template>
    <div class="space-y-4">
      <p v-if="error || state?.error" role="alert" class="text-sm text-red-600 dark:text-red-400">{{ error || state?.error }}</p>
      <img v-if="state?.qr_image" :src="state.qr_image" :alt="t('qr')" class="mx-auto h-64 w-64 rounded-lg bg-white p-2" />
      <p v-else-if="!error && !state?.error" role="status">{{ t('qrLoading') }}</p>
      <p class="text-sm">{{ state?.qr_image ? t('scan') : statusLabel }}</p>
      <form v-if="state?.status === 'need_verifycode'" class="space-y-3" @submit.prevent="perform(() => verifyWechatLogin(code))">
        <label for="wechat-code" class="block space-y-2 text-sm"><span>{{ t('code') }}</span><input id="wechat-code" v-model="code" class="input" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]+" maxlength="12" required :disabled="busy" /></label>
        <button type="submit" class="btn btn-primary" :disabled="busy || !/^\d{1,12}$/.test(code)">{{ t('verify') }}</button>
      </form>
      <button v-if="error || state?.status === 'login_expired' || state?.status === 'error'" type="button" class="btn btn-secondary" :disabled="busy" @click="login">{{ t('qrRetry') }}</button>
    </div>
  </WorkspaceDialog>
</template>
