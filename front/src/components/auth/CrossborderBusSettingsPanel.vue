<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { busCommand, fetchBusSettings } from '@/api/fulfillment'
import type { BusSettings, FulfillmentRule } from '@/types/fulfillment'
import { orderPlatformNames } from '@/types/orders'
import FulfillmentRuleDialog from './FulfillmentRuleDialog.vue'
import '@/components/domain/orderCenter.css'
const settings = ref<BusSettings | null>(null)
const tab = ref<'account' | 'rules'>('account')
const secret = ref('')
const userName = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')
const success = ref('')
const editorOpen = ref(false)
const selectedRule = ref<FulfillmentRule>()
const sections = computed(() => settings.value?.catalog.sections || [])
const warehouseCount = computed(() => sections.value.reduce((count, section) => count + section.storehouse_list.length, 0))
async function load() {
  busy.value = true
  try { settings.value = await fetchBusSettings(); userName.value = settings.value.user_name; error.value = '' }
  catch (exc) { error.value = exc instanceof Error ? exc.message : '履约配置读取失败' }
  finally { busy.value = false }
}
async function command(action: 'authorize' | 'catalog') {
  if (busy.value) return
  if (action === 'authorize' && !password.value && userName.value !== (settings.value?.user_name || '')) { error.value = '切换账号时请填写该账号密码'; return }
  busy.value = true; error.value = ''; success.value = ''
  try {
    settings.value = await busCommand(action, action === 'authorize' ? { client_secret: secret.value, user_name: password.value ? userName.value : '', password: password.value } : {})
    secret.value = ''; password.value = ''; success.value = action === 'authorize' ? '授权已验证，合作仓库已读取。' : '合作仓库已刷新。'
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '操作失败' }
  finally { busy.value = false; password.value = '' }
}
function edit(rule?: FulfillmentRule) { selectedRule.value = rule; editorOpen.value = true }
function saved(value: BusSettings) { settings.value = value; editorOpen.value = false; success.value = '默认履约方案已保存。' }
function warehouseName(rule: FulfillmentRule) {
  return settings.value?.catalog.sections?.find(s => s.section_id === rule.section_id)?.storehouse_list.find(w => w.id === rule.warehouse_id)?.name || String(rule.warehouse_id)
}
async function remove(rule: FulfillmentRule) {
  busy.value = true
  try { settings.value = await busCommand('delete-rule', { id: rule.id }) }
  catch (exc) { error.value = exc instanceof Error ? exc.message : '方案删除失败' }
  finally { busy.value = false }
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
    <form v-if="tab === 'account'" class="order-form account-card" @submit.prevent="command('authorize')">
      <div class="order-row"><h3>跨境巴士账号授权</h3><span class="order-badge" :data-tone="settings?.authorized ? 'green' : 'amber'">{{ settings?.authorized ? '已授权' : '未授权' }}</span></div>
      <label>企业密钥<input v-model="secret" class="order-input" type="password" autocomplete="new-password" :placeholder="settings?.secret_configured ? '已保存，留空保留当前密钥' : '填写企业应用的 client_secret'" /></label>
      <label>跨境巴士账号<input v-model="userName" class="order-input" autocomplete="username" placeholder="用户名、手机号或用户编码" /></label>
      <label>跨境巴士账号密码<input v-model="password" class="order-input" type="password" autocomplete="current-password" :placeholder="settings?.authorized ? '重新授权时填写；验证现有授权可留空' : '填写账号密码完成授权'" /></label>
      <p class="order-muted">账号密码仅用于本次授权。访问 Token、刷新令牌及授权记录由服务端管理。</p>
      <p v-if="settings?.expires_at" class="order-muted">Token 到期时间：{{ new Date(settings.expires_at * 1000).toLocaleString('zh-CN') }}，系统将在到期前自动刷新。</p>
      <div class="order-actions"><button class="order-button order-primary" :disabled="busy">{{ busy ? '验证中…' : '验证授权并读取合作仓库' }}</button></div>
      <p v-if="settings?.catalog.checked_at" class="order-muted">已读取 {{ sections.length }} 个合作渠道、{{ warehouseCount }} 个合作仓库，可在“默认履约”中查看。</p>
    </form>
    <div v-else class="account-card">
      <div class="order-row"><h3>默认履约方案</h3><div class="order-actions"><button class="order-button" :disabled="busy || !settings?.authorized" @click="command('catalog')">刷新合作仓库</button><button class="order-button order-primary" :disabled="busy || !settings?.authorized || !settings?.delivery_choices.length" @click="edit()">新增方案</button></div></div>
      <p class="order-muted my-4">按店铺和订单配送方式匹配；报单前可覆盖已确认兼容的仓库与可用服务。</p>
      <section class="cooperation-card mb-6" aria-label="已合作仓库">
        <div class="order-row"><h4>已合作仓库（{{ warehouseCount }}）</h4><span v-if="settings?.catalog.checked_at" class="order-muted">最近读取：{{ new Date(settings.catalog.checked_at * 1000).toLocaleString('zh-CN') }}</span></div>
        <div v-for="section in sections" :key="section.section_id" class="mt-4">
          <p>{{ section.section_name }}</p>
          <p v-for="warehouse in section.storehouse_list" :key="warehouse.id" class="order-muted mt-2">{{ warehouse.name }}<span v-if="warehouse.code"> · 仓库编码 {{ warehouse.code }}</span></p>
          <p v-if="!section.storehouse_list.length" class="order-muted mt-2">该渠道暂无已合作仓库。</p>
        </div>
        <p v-if="!sections.length" class="order-muted mt-4">{{ !settings?.authorized ? '授权后读取合作仓库。' : settings?.catalog.checked_at ? '当前账号暂无已合作仓库，请在跨境巴士确认合作关系后刷新。' : '尚未读取合作仓库，请点击“刷新合作仓库”。' }}</p>
      </section>
      <p v-if="!settings?.authorized" class="order-error">请先完成账号授权。</p>
      <p v-else-if="!settings.delivery_choices.length" class="order-muted py-4">尚无可用配送来源，请先在订单中心同步平台订单。</p>
      <div class="rule-table-wrap">
        <table>
          <thead><tr><th>平台配送来源</th><th>合作渠道 / 收货仓库</th><th>自动预报</th><th>操作</th></tr></thead><tbody>
            <tr v-for="rule in settings?.rules || []" :key="rule.id"><td>{{ orderPlatformNames[rule.platform] }} / {{ rule.account_id }} / {{ rule.fulfillment }} / {{ rule.country }}<p class="order-muted">平台仓：{{ rule.platform_warehouse_id }} · {{ rule.delivery_method_name }}</p></td><td>{{ settings?.catalog.sections?.find(s => s.section_id === rule.section_id)?.section_name }} / {{ warehouseName(rule) }}<p class="order-muted">{{ rule.service_ids.length }} 项增值服务</p></td><td>{{ rule.auto_submit ? '已开启' : '手动提交' }}</td><td><div class="order-actions"><button class="order-button" :disabled="busy" @click="edit(rule)">编辑</button><button class="order-link" :disabled="busy" @click="remove(rule)">删除</button></div></td></tr>
            <tr v-if="!settings?.rules.length"><td colspan="4" class="order-muted py-8">尚未配置默认履约方案；未匹配订单会暂停预报。</td></tr>
          </tbody>
        </table>
      </div>
      <p class="order-muted mt-5">配送信息不一致、合作仓库失效或资料不齐备时，订单详情会提示处理。</p>
    </div>
    <FulfillmentRuleDialog v-if="editorOpen && settings" :settings="settings" :rule="selectedRule" @close="editorOpen = false" @saved="saved" />
  </section>
</template>
<style scoped>
.account-card { padding: 24px; border-radius: 8px; background: var(--order-surface); border: 1px solid var(--order-border); }
h3 { font-size: 18px; font-weight: 600; }
.account-card.order-form { width: 100%; }
.cooperation-card { padding: 16px; border: 1px solid var(--order-border); border-radius: 6px; }
.cooperation-card h4 { font-weight: 600; }
.rule-table-wrap { overflow-x: auto; }
table { width: 100%; text-align: left; }
th { color: var(--order-muted); background: var(--order-bg); font-weight: 400; padding: 12px 16px; }
td { border-bottom: 1px solid var(--order-border); padding: 20px 16px; }
td p { margin-top: 8px; }
</style>
