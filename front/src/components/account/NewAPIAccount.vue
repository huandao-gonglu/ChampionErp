<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAccountStore } from '@/stores/account'
import { authorizeNewAPI, fetchNewAPIInfo, previewNewAPI, launchNewAPI } from '@/api/accounts'

const route = useRoute()
const account = useAccountStore()
const info = ref<Awaited<ReturnType<typeof fetchNewAPIInfo>> | null>(null)
const code = ref(typeof route.query.newapi_code === 'string' ? route.query.newapi_code : '')
const password = ref('')
const preview = ref<Awaited<ReturnType<typeof previewNewAPI>> | null>(null)
const busy = ref(false)
const error = ref('')
const approved = ref(false)
const sensitive = ref(Boolean(route.query.newapi_code))
watch(code, () => { preview.value = null; password.value = ''; approved.value = false })

async function perform(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await action() } catch (cause) { error.value = cause instanceof Error ? cause.message : '操作失败，请重试。' }
  finally { busy.value = false; password.value = '' }
}
onMounted(async () => {
  await perform(async () => { info.value = await fetchNewAPIInfo() })
  if (route.query.newapi_error) error.value = '登录交接未完成，请重新点击打开。'
  else if (['keys', 'wallet', 'usage'].includes(String(route.query.newapi_target)) && info.value?.configured) await open()
})
async function open() {
  const requested = String(route.query.newapi_target || 'keys')
  const target = requested === 'wallet' || requested === 'usage' ? requested : 'keys'
  await perform(async () => {
    if (window.championDesktop?.openNewAPI) { await window.championDesktop.openNewAPI(target); return }
    const launch = await launchNewAPI(target)
    const form = document.createElement('form')
    form.method = 'POST'; form.action = launch.url
    const ticket = document.createElement('input')
    ticket.type = 'hidden'; ticket.name = 'ticket'; ticket.setAttribute('value', launch.ticket)
    form.append(ticket); document.body.append(form)
    try { form.submit() } finally { form.remove() }
  })
}
async function inspect() {
  await perform(async () => { preview.value = await previewNewAPI(code.value.trim().toUpperCase()) })
}
async function approve() {
  if (!preview.value) return
  const approvedCode = preview.value.code
  await perform(async () => {
    await authorizeNewAPI({ code: approvedCode, ...(preview.value?.target === 'proof' ? { password: password.value } : {}) })
    approved.value = true; preview.value = null
  })
}

const targetLabels: Record<string, string> = {
  keys: '进入 API Key 管理',
  wallet: '进入钱包与充值记录',
  usage: '进入模型用量账单',
  proof: '重新验证 ERP 密码以完成 New API 敏感操作',
}
</script>

<template>
  <section class="space-y-4 rounded-2xl border border-accent-200 bg-white p-6 dark:border-white/10 dark:bg-dark-900">
    <h2 class="text-lg font-bold">Token 与账单</h2>
    <p v-if="error" role="alert" class="text-sm text-danger-600 dark:text-danger-300">{{ error }}</p>
    <p v-if="info && !info.configured" class="text-sm">Token 与账单服务尚未配置。</p>
    <template v-if="info?.configured">
      <p class="text-sm">使用当前 ERP 账号管理模型 Key、余额与账单。</p>
      <button data-test="newapi-open" type="button" class="btn btn-secondary" :disabled="busy" @click="open">打开 Token 与账单</button>
      <p v-if="info.sync_pending" role="status" class="text-sm text-warning-700 dark:text-warning-300">ERP 操作已完成；New API 会话或账号状态同步待重试。同步完成前，请勿假定 New API 已退出或停用。</p>
      <form v-if="sensitive" class="space-y-4" @submit.prevent="inspect">
        <label class="block text-sm font-semibold">浏览器中的 12 位授权码<input v-model="code" class="input mt-2 w-full font-mono uppercase" autocomplete="off" minlength="12" maxlength="12" required :disabled="busy" /></label>
        <button type="submit" class="btn btn-secondary" :disabled="busy">核对授权</button>
      </form>
      <form v-if="preview" class="space-y-4" @submit.prevent="approve">
        <p class="text-sm">{{ `使用 ${account.user?.email} 授权浏览器中的 ${preview.code}。确认该码与浏览器显示一致。` }}</p>
        <p class="text-sm font-semibold">{{ targetLabels[preview.target] }}</p>
        <label v-if="preview.target === 'proof'" class="block text-sm font-semibold">密码<input v-model="password" type="password" autocomplete="current-password" required maxlength="128" class="input mt-2 w-full" :disabled="busy" /></label>
        <button type="submit" class="btn btn-primary" :disabled="busy">确认授权</button>
      </form>
      <p v-if="approved" role="status" class="text-sm">已授权，请返回 New API 浏览器页面。</p>
    </template>
  </section>
</template>
