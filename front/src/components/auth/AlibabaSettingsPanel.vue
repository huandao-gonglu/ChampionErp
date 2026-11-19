<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useAccountStore } from '@/stores/account'
import { type AlibabaProvider, bindOneBoundBuyerToken, bindSelfUseToken, completeAlibabaAuthorization, fetchAlibabaAuthorization, startAlibabaAuthorization } from '@/api/alibabaAuthorization'
import { hasLocalOperations } from '@/utils/desktop'
import type { BackendAlibabaAuthorizationStatus } from '@/types/workflow.generated'

const props = defineProps<{ provider: AlibabaProvider }>()
const titles = { alibaba: '企业自用应用', alibaba_crossborder: '跨境 ERP 服务商应用', onebound: '万邦' }
const account = useAccountStore()
const buyerToken = ref('')
const callbackUrl = ref('')
const completing = ref(false)
const manualError = ref('')
const status = ref<BackendAlibabaAuthorizationStatus | null>(null)
const opening = ref(false)
const waiting = ref(false)
const error = ref('')
const browserError = ref('')
let timer: number | undefined
let revision = 0
let reading = false
let disposed = false

async function refresh() {
  if (disposed || opening.value || completing.value || reading) return
  window.clearTimeout(timer)
  const current = revision
  let retryDelay = 2500
  reading = true
  try {
    const next = await fetchAlibabaAuthorization(props.provider)
    if (current !== revision) return
    status.value = next
    waiting.value = next.attempt === 'waiting' && !browserError.value
    error.value = ''
  } catch (exc) {
    if (current !== revision) return
    error.value = exc instanceof Error ? exc.message : '1688 授权操作失败'
    retryDelay = 10000
  } finally {
    if (current === revision) {
      reading = false
      if (waiting.value || error.value) timer = window.setTimeout(refresh, retryDelay)
    }
  }
}

async function connect() {
  if (opening.value || completing.value) return
  callbackUrl.value = manualError.value = ''
  browserError.value = ''
  // 网页必须在点击事件内打开标签页，否则异步取得授权地址后可能被浏览器拦截。
  const desktop = hasLocalOperations()
  const tab = desktop ? null : window.open('about:blank', '_blank')
  if (!desktop && !tab) {
    browserError.value = '请允许此站点打开弹窗，再次点击授权。'
    return
  }
  if (tab) tab.opener = null
  window.clearTimeout(timer)
  revision++
  reading = false
  opening.value = true
  error.value = ''
  try {
    const { url } = await startAlibabaAuthorization(props.provider)
    try {
      if (tab) tab.location.replace(url)
      else if (window.championDesktop?.openAlibabaAuthorization) await window.championDesktop.openAlibabaAuthorization(url)
      else throw new Error()
    } catch {
      tab?.close()
      waiting.value = false
      browserError.value = '无法打开系统浏览器，请再次点击授权。'
      return
    }
    waiting.value = true
  } catch (exc) {
    tab?.close()
    error.value = exc instanceof Error ? exc.message : '1688 授权操作失败'
  } finally {
    opening.value = false
    if (waiting.value && !disposed) timer = window.setTimeout(refresh, 2500)
  }
}

async function complete(method: 'callback' | 'token') {
  const value = (method === 'token' ? buyerToken : callbackUrl).value.trim()
  if (!account.isAdmin || !value || completing.value || opening.value) return
  window.clearTimeout(timer)
  revision++
  reading = false
  completing.value = true
  manualError.value = ''
  try {
    if (method === 'callback') status.value = await completeAlibabaAuthorization(value, props.provider)
    else if (props.provider === 'alibaba') status.value = await bindSelfUseToken(value)
    else status.value = await bindOneBoundBuyerToken(value)
    buyerToken.value = ''
    callbackUrl.value = browserError.value = error.value = ''
    waiting.value = false
  } catch (exc) {
    manualError.value = exc instanceof Error ? exc.message : '1688 授权操作失败'
    if (!disposed) timer = window.setTimeout(refresh, 0)
  } finally {
    completing.value = false
  }
}

onMounted(() => {
  void refresh()
  window.addEventListener('focus', refresh)
})
onUnmounted(() => {
  disposed = true
  revision++
  window.clearTimeout(timer)
  window.removeEventListener('focus', refresh)
})
</script>
<template>
  <section class="space-y-4 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70" :aria-label="titles[provider] + ' 1688 授权'">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h3 class="font-semibold text-accent-950 dark:text-white">{{ titles[provider] }} · 1688 授权</h3>
      <span v-if="status" aria-live="polite">{{ status.binding_pending ? '待核验' : (status.authorized && !status.needs_reauthorization ? '已连接' : '未连接') }}</span>
    </div>
    <p v-if="status?.active" class="text-sm text-accent-500">当前业务使用此方案</p>
    <p v-if="provider === 'alibaba_crossborder'" class="text-sm text-accent-500">请先在 1688 订购本服务，再连接采购账号。</p>
    <p v-if="status && !status.application_configured" role="status">授权服务暂未配置，请联系管理员。</p>
    <p v-else-if="status?.binding_pending" role="status">万邦买家身份待核验，请联系管理员。</p>
    <p v-else-if="status && provider === 'onebound' && !status.authorized" role="status">ERP 暂未接入万邦的买家登录。</p>
    <p v-else-if="status && provider !== 'onebound' && !status.can_authorize" role="status">暂时无法发起登录授权，请联系管理员配置授权服务。</p>
    <p v-if="status?.needs_reauthorization" role="status">授权已过期或应用配置已变化，请重新连接账号。</p>
    <p v-if="status?.account_name">当前账号：{{ status.account_name }}</p>
    <p v-else-if="status?.authorized" class="text-sm">当前授权未记录账号名称。</p>
    <p v-if="status?.expires_at" class="text-sm">授权到期时间：{{ new Date(status.expires_at * 1000).toLocaleString() }} <span v-if="status.refreshable">使用时自动续期。</span></p>
    <button v-if="provider !== 'onebound'" data-testid="connect-1688" type="button" class="btn btn-primary" :disabled="opening || completing || !status?.can_authorize" @click="connect">
      {{ (status?.authorized || waiting ? '重新授权' : '连接 1688 账号') }}
    </button>
    <p v-if="waiting" role="status">等待浏览器完成授权…</p>
    <p v-else-if="status?.attempt === 'failed' || status?.attempt === 'expired'" role="status">本次授权未完成，请重新授权。</p>
    <div v-if="account.isAdmin && status && provider !== 'onebound'" class="space-y-2">
      <label class="block text-sm" :for="provider + '-callback-url'">授权后的完整回调地址</label>
      <input
        :id="provider + '-callback-url'" v-model="callbackUrl" type="password" maxlength="16384" autocomplete="off"
        spellcheck="false" class="input w-full" placeholder="粘贴浏览器地址栏中的完整 URL" :disabled="completing"
        @keydown.enter.prevent="complete('callback')"
      />
      <button type="button" class="btn btn-secondary" :disabled="opening || completing || !callbackUrl.trim()" @click="complete('callback')">
        {{ (completing ? '正在完成授权…' : '完成授权') }}
      </button>
    </div>
    <div v-if="account.isAdmin && status?.application_configured && provider !== 'alibaba_crossborder'" class="space-y-2">
      <label class="block text-sm" :for="provider + '-buyer-token'">{{ provider === 'alibaba' ? '企业自用应用「授权配置」中的已授权 Token' : '万邦应用授权取得的 1688 买家 Token' }}</label>
      <input
        :id="provider + '-buyer-token'" v-model="buyerToken" class="input w-full" type="password"
        autocomplete="off" spellcheck="false" maxlength="4096" :disabled="completing" @keydown.enter.prevent="complete('token')"
      />
      <button type="button" class="btn btn-secondary" :disabled="opening || completing || !buyerToken.trim()" @click="complete('token')">
        {{ completing ? '正在绑定…' : '绑定到我的账号' }}
      </button>
    </div>
    <p v-if="manualError" role="alert" class="text-red-600">{{ manualError }}</p>
    <p v-if="browserError || error" role="alert" class="text-red-600">{{ browserError || error }}</p>
  </section>
</template>
