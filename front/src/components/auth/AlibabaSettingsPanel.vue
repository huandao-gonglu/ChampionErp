<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useAccountStore } from '@/stores/account'
import {
  type AlibabaProvider,
  type OneBoundQuotaView,
  bindSelfUseToken,
  completeAlibabaAuthorization,
  fetchAlibabaAuthorization,
  fetchOneBoundQuota,
  restoreOneBoundKey,
  startAlibabaAuthorization,
} from '@/api/alibabaAuthorization'
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
const quota = ref<OneBoundQuotaView | null>(null)
const restoringKey = ref(false)
const restoreNotice = ref('')
const opening = ref(false)
const waiting = ref(false)
const error = ref('')
const browserError = ref('')
let timer: number | undefined
let revision = 0
let reading = false
let disposed = false

async function refreshQuota() {
  if (props.provider !== 'onebound' || disposed) return
  try {
    const next = await fetchOneBoundQuota()
    if (!disposed) quota.value = next
  } catch {
    /* 额度读取失败不阻塞授权主流程。 */
  }
}

async function restoreKey() {
  if (restoringKey.value || !account.isAdmin) return
  restoringKey.value = true
  restoreNotice.value = ''
  error.value = ''
  try {
    const result = await restoreOneBoundKey()
    quota.value = result.quota
    restoreNotice.value = result.restored ? '已恢复万邦企业 Key 使用' : '万邦企业 Key 当前已处于可用状态'
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '恢复万邦企业 Key 失败'
  } finally {
    restoringKey.value = false
  }
}

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
    if (props.provider === 'onebound' && !waiting.value) callbackUrl.value = ''
    error.value = ''
    if (props.provider === 'onebound') void refreshQuota()
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
  const allowed = method === 'token'
    ? account.isAdmin && props.provider === 'alibaba'
    : props.provider === 'onebound' ? waiting.value : account.isAdmin
  if (!allowed || !value || completing.value || opening.value) return
  window.clearTimeout(timer)
  revision++
  reading = false
  completing.value = true
  manualError.value = ''
  try {
    if (method === 'callback') status.value = await completeAlibabaAuthorization(value, props.provider)
    else status.value = await bindSelfUseToken(value)
    buyerToken.value = ''
    callbackUrl.value = browserError.value = error.value = ''
    waiting.value = false
    if (props.provider === 'onebound') void refreshQuota()
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
      <span v-if="status" aria-live="polite">{{ status.authorized && !status.needs_reauthorization ? '已连接' : '未连接' }}</span>
    </div>
    <p v-if="status?.active" class="text-sm text-accent-500">当前业务使用此方案</p>
    <p v-if="provider === 'alibaba_crossborder'" class="text-sm text-accent-500">请先在 1688 订购本服务，再连接采购账号。</p>
    <p v-if="status && !status.application_configured" role="status">授权服务暂未配置，请联系管理员。</p>
    <p v-else-if="status && !status.can_authorize" role="status">暂时无法发起登录授权，请联系管理员配置授权服务。</p>
    <p v-if="status?.needs_reauthorization" role="status">请重新连接 1688 账号。</p>
    <p v-if="status?.account_name">当前账号：{{ status.account_name }}</p>
    <p v-else-if="status?.authorized" class="text-sm">当前授权未记录账号名称。</p>
    <p v-if="status?.expires_at" class="text-sm">授权到期时间：{{ new Date(status.expires_at * 1000).toLocaleString() }} <span v-if="status.refreshable">使用时自动续期。</span></p>
    <div
      v-if="provider === 'onebound' && quota && (quota.buyer || quota.key)"
      data-testid="onebound-quota-panel"
      class="space-y-2 rounded-md border border-accent-200/80 bg-white/80 p-3 text-sm dark:border-dark-700 dark:bg-dark-900/60"
    >
      <div v-if="quota.buyer" class="space-y-1">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <span class="font-medium text-accent-900 dark:text-accent-100">当前买家万邦额度</span>
          <span
            class="font-semibold tabular-nums"
            :class="quota.buyer.balance <= 0 ? 'text-rose-600 dark:text-rose-400' : 'text-primary-700 dark:text-primary-300'"
          >
            余额 {{ quota.buyer.balance }} 次
          </span>
        </div>
        <p class="text-xs text-accent-600 tabular-nums dark:text-accent-400">
          基础额度 {{ quota.buyer.granted }} · 下单收入 {{ quota.buyer.earned }} · 累计支出 {{ quota.buyer.spent_total }}（交易 {{ quota.buyer.spent_trade }} / 授权 {{ quota.buyer.spent_auth }} / 采集 {{ quota.buyer.spent_collect }}）
        </p>
      </div>
      <div v-if="quota.key && account.isAdmin" class="space-y-1.5 border-t border-accent-200/70 pt-2 dark:border-dark-700">
        <div class="flex flex-wrap items-center justify-between gap-2 text-xs text-accent-700 tabular-nums dark:text-accent-300">
          <span>
            企业 Key 今日用量：{{ quota.key.today_estimated }}<template v-if="quota.key.daily_max !== null"> / {{ quota.key.daily_max }}</template>（保留额度 {{ quota.key.reserve }}）
          </span>
          <span v-if="quota.key.expires" :class="quota.key.expired ? 'font-semibold text-rose-600 dark:text-rose-400' : quota.key.expiring_soon ? 'font-semibold text-amber-700 dark:text-amber-300' : ''">
            到期日：{{ quota.key.expires }}<template v-if="quota.key.expired">（已过期）</template><template v-else-if="quota.key.expiring_soon">（剩余 {{ quota.key.days_until_expiry }} 天）</template>
          </span>
        </div>
        <div v-if="quota.key.exhausted_until" class="text-xs text-amber-700 dark:text-amber-300">
          今日调用次数已达上限，预计恢复时间：{{ new Date(quota.key.exhausted_until * 1000).toLocaleString() }}
        </div>
        <div v-if="quota.key.disabled" class="flex flex-wrap items-center justify-between gap-2 rounded border border-rose-200 bg-rose-50 px-2.5 py-2 text-xs text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
          <span>{{ quota.key.disabled_message || '万邦企业 Key 已暂停使用' }}</span>
          <button
            data-testid="restore-onebound-key"
            type="button"
            class="btn btn-secondary !py-1 text-xs"
            :disabled="restoringKey"
            @click="restoreKey"
          >
            {{ restoringKey ? '恢复中…' : '已处理，恢复使用' }}
          </button>
        </div>
        <p v-if="restoreNotice" role="status" class="text-xs text-green-700 dark:text-green-400">{{ restoreNotice }}</p>
      </div>
    </div>
    <button data-testid="connect-1688" type="button" class="btn btn-primary" :disabled="opening || completing || !status?.can_authorize" @click="connect">
      {{ (status?.authorized || status?.needs_reauthorization || waiting ? '重新授权' : '连接 1688 账号') }}
    </button>
    <p v-if="waiting && provider !== 'onebound'" role="status">等待浏览器完成授权…</p>
    <p v-if="!waiting && (status?.attempt === 'failed' || status?.attempt === 'expired')" role="status">本次授权未完成，请重新授权。</p>
    <div v-if="provider === 'onebound' ? waiting : account.isAdmin && status" class="space-y-2">
      <p v-if="provider === 'onebound'" class="text-sm text-accent-500">在浏览器登录 1688 并授权后，立即复制地址栏中的完整地址。回跳页可能空白。</p>
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
    <div v-if="provider === 'alibaba' && account.isAdmin && status?.application_configured" class="space-y-2">
      <label class="block text-sm" for="alibaba-buyer-token">企业自用应用「授权配置」中的已授权 Token</label>
      <input
        id="alibaba-buyer-token" v-model="buyerToken" class="input w-full" type="password"
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
