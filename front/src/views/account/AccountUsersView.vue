<script setup lang="ts">
import { computed, onMounted, reactive, ref, useId } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import {
  createAccountUser,
  listAccountUsers,
  resetAccountUserPassword,
  setAccountUserOneBoundQuota,
  setAccountUserStatus,
  type AccountUser,
} from '@/api/accounts'

const users = ref<AccountUser[]>([])
const total = ref(0)
const page = ref(1)
const query = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')
const syncPending = ref(false)
const creating = ref(false)
const resetUser = ref<AccountUser | null>(null)
const resetPassword = ref('')
const quotaUser = ref<AccountUser | null>(null)
const quotaTarget = ref<string | number>('')
const quotaNote = ref('')
const quotaError = ref('')
const quotaFormId = useId()
const showActions = computed(() => users.value.some(user => user.role === 'user' || Boolean(user.onebound_quota?.authorized)))
const form = reactive({ email: '', display_name: '', password: '' })
const quotaPreview = computed(() => {
  const current = quotaUser.value?.onebound_quota?.balance
  if (current === null || current === undefined) return null
  const raw = String(quotaTarget.value ?? '').trim()
  if (!/^\d+$/.test(raw)) return null
  const target = Number(raw)
  if (!Number.isSafeInteger(target) || target < 0 || target > 10_000_000) return null
  return { target, delta: target - current }
})

async function load() {
  const data = await listAccountUsers({ page: page.value, q: query.value })
  users.value = data.items
  total.value = data.total
}
async function perform(action: () => Promise<unknown>, message = '') {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await action(); notice.value = message } catch (cause) { error.value = cause instanceof Error ? cause.message : '操作失败，请重试。' }
  finally { busy.value = false }
}
async function create() {
  await perform(async () => {
    await createAccountUser(form)
    Object.assign(form, { email: '', display_name: '', password: '' })
    creating.value = false; page.value = 1
    await load()
  }, '用户已创建。')
}
async function reset() {
  const user = resetUser.value
  if (!user) return
  await perform(async () => {
    const result = await resetAccountUserPassword(user.id, resetPassword.value)
    syncPending.value = result?.data?.newapi_sync_pending === true
    resetPassword.value = ''; resetUser.value = null
  }, '密码已重置，该用户需要重新登录。')
}
async function setStatus(user: AccountUser) {
  const result = await setAccountUserStatus(user.id, user.status === 'enabled' ? 'disabled' : 'enabled')
  syncPending.value = result?.data?.newapi_sync_pending === true
  await load()
}
function openQuotaDialog(user: AccountUser) {
  const quota = user.onebound_quota
  if (!quota?.authorized || quota.balance === null) return
  quotaUser.value = user
  quotaTarget.value = String(Math.max(0, quota.balance))
  quotaNote.value = ''
  quotaError.value = ''
}
function closeQuotaDialog() {
  if (busy.value) return
  quotaUser.value = null
  quotaTarget.value = ''
  quotaNote.value = ''
  quotaError.value = ''
}
async function submitQuota() {
  const user = quotaUser.value
  const quota = user?.onebound_quota
  if (!user || !quota?.authorized || quota.balance === null || busy.value) return
  const preview = quotaPreview.value
  if (!preview) {
    quotaError.value = '请输入 0 至 10000000 之间的整数目标余额。'
    return
  }
  busy.value = true
  quotaError.value = ''
  error.value = ''
  notice.value = ''
  try {
    const result = await setAccountUserOneBoundQuota({
      user_id: user.id,
      target_balance: preview.target,
      expected_balance: quota.balance,
      note: quotaNote.value.trim(),
    })
    quotaUser.value = null
    quotaTarget.value = ''
    quotaNote.value = ''
    await load()
    const buyerName = result.account_name || result.member_id
    if (!result.changed) {
      notice.value = `买家 ${buyerName} 的万邦余额已为 ${result.balance} 次，无需调整。`
    } else {
      const sign = result.delta > 0 ? `+${result.delta}` : String(result.delta)
      notice.value = `已将买家 ${buyerName} 的万邦余额调整为 ${result.balance} 次（${sign}）。`
    }
  } catch (cause) {
    quotaError.value = cause instanceof Error ? cause.message : '调整万邦额度失败，请重试。'
    if ((cause as { status?: number })?.status === 409) {
      await load().catch(() => undefined)
      const latest = users.value.find(row => row.id === user.id)
      if (latest?.onebound_quota?.authorized && latest.onebound_quota.balance !== null) {
        quotaUser.value = latest
      }
    }
  } finally {
    busy.value = false
  }
}
onMounted(() => {
  void perform(load)
})
</script>

<template>
  <main class="min-h-screen bg-accent-50 p-6 text-accent-950 dark:bg-dark-950 dark:text-white md:p-10">
    <div class="mx-auto max-w-5xl space-y-6">
      <PageHeader title="用户管理">
        <template #actions>
          <RouterLink to="/account" class="btn btn-secondary">个人账号</RouterLink>
          <RouterLink to="/" class="btn btn-secondary">返回工作台</RouterLink>
          <button class="btn btn-primary" :disabled="busy" @click="creating = !creating">创建普通用户</button>
        </template>
      </PageHeader>
      <p v-if="error" role="alert" class="text-sm text-danger-600 dark:text-danger-300">{{ error }}</p>
      <p v-if="syncPending" role="status" class="text-sm text-warning-700 dark:text-warning-300">ERP 操作已完成；New API 会话或账号状态同步待重试。同步完成前，请勿假定 New API 已退出或停用。</p>
      <p v-if="notice" role="status" class="text-sm text-success-700 dark:text-success-300">{{ notice }}</p>
      <form v-if="creating" class="space-y-4 rounded-2xl border border-accent-200 bg-white p-6 dark:border-white/10 dark:bg-dark-900" @submit.prevent="create">
        <h2 class="font-bold">创建普通用户</h2>
        <label class="block text-sm">显示名称<input v-model="form.display_name" required maxlength="80" autocomplete="off" class="input mt-2 w-full" :disabled="busy" /></label>
        <label class="block text-sm">登录邮箱<input v-model="form.email" type="email" required maxlength="254" autocomplete="off" class="input mt-2 w-full" :disabled="busy" /></label>
        <label class="block text-sm">密码<input v-model="form.password" type="password" required minlength="15" maxlength="128" autocomplete="new-password" class="input mt-2 w-full" :disabled="busy" /></label>
        <p class="text-xs text-accent-500 dark:text-accent-300">使用 15 至 128 个字符，可直接粘贴密码管理器生成的密码。</p>
        <button type="submit" class="btn btn-primary" :disabled="busy">创建普通用户</button>
      </form>
      <form v-if="resetUser" class="space-y-4 rounded-2xl border border-accent-200 bg-white p-6 dark:border-white/10 dark:bg-dark-900" @submit.prevent="reset">
        <h2 class="font-bold">{{ `重置 ${resetUser.email} 的密码` }}</h2>
        <label class="block text-sm">新密码<input v-model="resetPassword" type="password" required minlength="15" maxlength="128" autocomplete="new-password" class="input mt-2 w-full" :disabled="busy" /></label>
        <p class="text-xs text-accent-500 dark:text-accent-300">使用 15 至 128 个字符，可直接粘贴密码管理器生成的密码。 密码变更后，所有设备需要重新登录。</p>
        <button type="submit" class="btn btn-primary" :disabled="busy">重置密码</button>
        <button type="button" class="btn btn-secondary ml-3" :disabled="busy" @click="resetUser = null; resetPassword = ''">取消</button>
      </form>
      <form class="flex gap-3" @submit.prevent="page = 1; perform(load)">
        <input v-model="query" aria-label="按邮箱或名称查询" placeholder="按邮箱或名称查询" class="input flex-1" maxlength="200" />
        <button type="submit" class="btn btn-secondary" :disabled="busy">查询</button>
      </form>
      <section class="overflow-x-auto rounded-2xl border border-accent-200 bg-white dark:border-white/10 dark:bg-dark-900" :aria-busy="busy">
        <table class="w-full text-left text-sm">
          <thead class="border-b border-accent-200 dark:border-white/10">
            <tr>
              <th class="p-4">显示名称</th>
              <th class="p-4">登录邮箱</th>
              <th class="p-4">角色</th>
              <th class="p-4">状态</th>
              <th class="p-4">万邦额度</th>
              <th v-if="showActions" class="p-4">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in users" :key="user.id" class="border-b border-accent-100 dark:border-white/5">
              <td class="p-4">{{ user.display_name }}</td>
              <td class="p-4">{{ user.email }}</td>
              <td class="whitespace-nowrap p-4">{{ user.role === 'admin' ? '管理员' : '普通用户' }}</td>
              <td class="p-4">{{ user.status === 'enabled' ? '已启用' : '已禁用' }}</td>
              <td class="p-4">
                <template v-if="user.onebound_quota?.authorized && user.onebound_quota.balance !== null">
                  <div class="font-semibold tabular-nums" :class="user.onebound_quota.balance <= 0 ? 'text-danger-600 dark:text-danger-300' : ''">
                    {{ user.onebound_quota.balance }} 次
                  </div>
                  <div class="text-xs text-accent-500 dark:text-accent-300">
                    {{ user.onebound_quota.account_name || user.onebound_quota.member_id }}
                    <span v-if="user.onebound_quota.shared_user_count > 1" class="ml-1 text-warning-700 dark:text-warning-300">
                      （{{ user.onebound_quota.shared_user_count }} 人共用）
                    </span>
                  </div>
                </template>
                <span v-else class="text-xs text-accent-500 dark:text-accent-300">未授权</span>
              </td>
              <td v-if="showActions" class="space-x-3 whitespace-nowrap p-4">
                <button v-if="user.onebound_quota?.authorized && user.onebound_quota.balance !== null" class="font-semibold text-primary-600 dark:text-primary-300" :disabled="busy" @click="openQuotaDialog(user)">调整额度</button>
                <button v-if="user.role === 'user'" class="font-semibold text-primary-600 dark:text-primary-300" :disabled="busy" @click="perform(async () => { await setStatus(user) })">{{ (user.status === 'enabled' ? '禁用' : '启用') }}</button>
                <button v-if="user.role === 'user'" class="font-semibold text-primary-600 dark:text-primary-300" :disabled="busy" @click="resetUser = user; resetPassword = ''">重置密码</button>
              </td>
            </tr>
            <tr v-if="!users.length"><td :colspan="showActions ? 6 : 5" class="p-8 text-center text-accent-500 dark:text-accent-300">{{ (busy ? '正在加载…' : '没有匹配的用户。') }}</td></tr>
          </tbody>
        </table>
      </section>
      <div class="flex items-center justify-between text-sm">
        <span>{{ `共 ${total} 位用户` }}</span>
        <div class="flex items-center gap-4">
          <button class="btn btn-secondary" :disabled="page <= 1 || busy" @click="page--; perform(load)">上一页</button><span>{{ page }}</span>
          <button class="btn btn-secondary" :disabled="page * 30 >= total || busy" @click="page++; perform(load)">下一页</button>
        </div>
      </div>
    </div>

    <WorkspaceDialog
      :open="Boolean(quotaUser)"
      title="调整万邦调用额度"
      width="30rem"
      :close-disabled="busy"
      @close="closeQuotaDialog"
    >
      <form v-if="quotaUser && quotaUser.onebound_quota" :id="quotaFormId" class="space-y-4" @submit.prevent="submitQuota">
        <div class="space-y-1 rounded-xl border border-accent-200 bg-accent-50/70 p-3 text-sm dark:border-dark-700 dark:bg-dark-800/60">
          <div>ERP 账号：<strong>{{ quotaUser.display_name }}</strong>（{{ quotaUser.email }}）</div>
          <div>1688 买家：<strong>{{ quotaUser.onebound_quota.account_name || quotaUser.onebound_quota.member_id }}</strong>（{{ quotaUser.onebound_quota.member_id }}）</div>
          <div class="tabular-nums">当前余额：<strong>{{ quotaUser.onebound_quota.balance }}</strong> 次</div>
        </div>
        <p
          v-if="quotaUser.onebound_quota.shared_user_count > 1"
          role="status"
          class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
        >
          当前共有 {{ quotaUser.onebound_quota.shared_user_count }} 个 ERP 账号授权了同一个 1688 买家，本次调整将同步生效于这些账号。
        </p>
        <label class="block text-sm">
          目标余额（次）
          <input
            v-model="quotaTarget"
            data-testid="quota-target-input"
            type="number"
            min="0"
            max="10000000"
            step="1"
            required
            class="input mt-2 w-full tabular-nums"
            :disabled="busy"
          />
        </label>
        <p v-if="quotaPreview" class="text-xs text-accent-600 tabular-nums dark:text-accent-300">
          <template v-if="quotaPreview.delta === 0">与当前余额一致，提交后不会写入新流水。</template>
          <template v-else-if="quotaPreview.delta > 0">系统将自动记入 <strong>+{{ quotaPreview.delta }}</strong> 次调整流水。</template>
          <template v-else>系统将自动记入 <strong>{{ quotaPreview.delta }}</strong> 次调整流水。</template>
        </p>
        <label class="block text-sm">
          调整备注（可选）
          <input
            v-model="quotaNote"
            data-testid="quota-note-input"
            type="text"
            maxlength="200"
            placeholder="例如：手工补发初始采集额度"
            class="input mt-2 w-full"
            :disabled="busy"
          />
        </label>
        <p v-if="quotaError" role="alert" class="text-sm text-danger-600 dark:text-danger-300">{{ quotaError }}</p>
      </form>
      <template #footer>
        <div class="flex justify-end gap-2">
          <button type="button" class="btn btn-secondary" :disabled="busy" @click="closeQuotaDialog">取消</button>
          <button type="submit" :form="quotaFormId" class="btn btn-primary" :disabled="busy || !quotaPreview">
            {{ busy ? '保存中…' : '确认调整' }}
          </button>
        </div>
      </template>
    </WorkspaceDialog>
  </main>
</template>
