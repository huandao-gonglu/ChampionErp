<script setup lang="ts">
import { computed, inject, onMounted, ref, watch } from 'vue'
import { routeLocationKey } from 'vue-router'
import LogisticsSettingsPanel from '@/components/settings/LogisticsSettingsPanel.vue'
import { busCommand, fetchBusSettings } from '@/api/fulfillment'
import type { BusSettings } from '@/types/fulfillment'
import '@/components/domain/orderCenter.css'
const settings = ref<BusSettings | null>(null)
const tab = ref<'account' | 'rules'>('account')
const route = inject(routeLocationKey, undefined)
watch(() => route?.query.bus_section, section => {
  tab.value = section === 'rules' ? 'rules' : 'account'
}, { immediate: true })
const secret = ref('')
const userName = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')
const success = ref('')
const sections = computed(() => settings.value?.catalog.sections || [])
const warehouseCount = computed(() => sections.value.reduce((count, section) => count + section.storehouse_list.length, 0))
async function load() {
  busy.value = true
  try { settings.value = await fetchBusSettings(); userName.value = settings.value.user_name; error.value = '' }
  catch (exc) { error.value = exc instanceof Error ? exc.message : '履约配置读取失败' }
  finally { busy.value = false }
}
async function authorize() {
  if (busy.value) return
  if (!password.value && userName.value !== (settings.value?.user_name || '')) { error.value = '切换账号时请填写该账号密码'; return }
  busy.value = true; error.value = ''; success.value = ''
  try {
    settings.value = await busCommand('authorize', { client_secret: secret.value, user_name: password.value ? userName.value : '', password: password.value })
    secret.value = ''; password.value = ''; success.value = '授权已验证，合作仓库已读取。'
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '操作失败' }
  finally { busy.value = false; password.value = '' }
}
onMounted(load)
</script>
<template>
  <section class="order-ui bus-settings" aria-label="跨境巴士配置">
    <nav class="order-actions mb-6" aria-label="跨境巴士设置">
      <button class="order-button" :class="{ 'order-primary': tab === 'account' }" @click="tab = 'account'">账号授权</button>
      <button class="order-button" :class="{ 'order-primary': tab === 'rules' }" @click="tab = 'rules'">默认履约</button>
    </nav>
    <p v-if="error" class="order-error" role="alert">{{ error }} <button class="order-link" :disabled="busy" @click="load">重新读取</button></p>
    <p v-if="success" class="order-success" role="status">{{ success }}</p>
    <p v-if="busy && !settings" class="order-muted">正在读取配置…</p>
    <form v-if="tab === 'account'" class="order-form account-card" @submit.prevent="authorize">
      <div class="order-row"><h3>跨境巴士账号授权</h3><span class="order-badge" :data-tone="settings?.authorized ? 'green' : 'amber'">{{ settings?.authorized ? '已授权' : '未授权' }}</span></div>
      <label>企业密钥<input v-model="secret" class="order-input" type="password" autocomplete="new-password" :placeholder="settings?.secret_configured ? '已保存，留空保留当前密钥' : '填写企业应用的 client_secret'" /></label>
      <label>跨境巴士账号<input v-model="userName" class="order-input" autocomplete="username" placeholder="用户名、手机号或用户编码" /></label>
      <label>跨境巴士账号密码<input v-model="password" class="order-input" type="password" autocomplete="current-password" :placeholder="settings?.authorized ? '重新授权时填写；验证现有授权可留空' : '填写账号密码完成授权'" /></label>
      <p class="order-muted">账号密码仅用于本次授权。访问 Token、刷新令牌及授权记录由服务端管理。</p>
      <p v-if="settings?.expires_at" class="order-muted">Token 到期时间：{{ new Date(settings.expires_at * 1000).toLocaleString('zh-CN') }}，系统将在到期前自动刷新。</p>
      <div class="order-actions"><button class="order-button order-primary" :disabled="busy">{{ busy ? '验证中…' : '验证授权并读取合作仓库' }}</button></div>
      <p v-if="settings?.catalog.checked_at" class="order-muted">已读取 {{ sections.length }} 个合作渠道、{{ warehouseCount }} 个合作仓库，可在“默认履约”中查看。</p>
    </form>
    <LogisticsSettingsPanel v-else class="account-card" />
  </section>
</template>
<style scoped>
.account-card { padding: 24px; border-radius: 8px; background: var(--order-surface); border: 1px solid var(--order-border); }
h3 { font-size: 18px; font-weight: 600; }
.account-card.order-form { width: 100%; }
</style>
