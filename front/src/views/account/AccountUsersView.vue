<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/components/layout/PageHeader.vue'
import { createAccountUser, listAccountUsers, resetAccountUserPassword, setAccountUserStatus, type AccountUser } from '@/api/accounts'

const { t } = useI18n()
const users = ref<AccountUser[]>([])
const total = ref(0)
const page = ref(1)
const query = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')
const creating = ref(false)
const resetUser = ref<AccountUser | null>(null)
const resetPassword = ref('')
const form = reactive({ email: '', display_name: '', password: '' })
let generation = 0
async function load() {
  const revision = ++generation
  const data = await listAccountUsers({ page: page.value, q: query.value })
  if (revision !== generation) return
  users.value = data.items; total.value = data.total
}
async function perform(action: () => Promise<unknown>, message = '') {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await action(); notice.value = message } catch (cause) { error.value = cause instanceof Error ? cause.message : t('account.failed') }
  finally { busy.value = false }
}
async function create() {
  await perform(async () => {
    await createAccountUser(form)
    Object.assign(form, { email: '', display_name: '', password: '' })
    creating.value = false; page.value = 1
    await load()
  }, t('account.created'))
}
async function reset() {
  const user = resetUser.value
  if (!user) return
  await perform(async () => {
    await resetAccountUserPassword(user.id, resetPassword.value)
    resetPassword.value = ''; resetUser.value = null
  }, t('account.passwordReset'))
}
onMounted(() => { void perform(load) })
</script>

<template>
  <main class="min-h-screen bg-accent-50 p-6 text-accent-950 dark:bg-dark-950 dark:text-white md:p-10">
    <div class="mx-auto max-w-5xl space-y-6">
      <PageHeader :title="t('account.users')">
        <template #actions>
          <RouterLink to="/account" class="btn btn-secondary">{{ t('account.personal') }}</RouterLink>
          <RouterLink to="/" class="btn btn-secondary">{{ t('account.back') }}</RouterLink>
          <button class="btn btn-primary" :disabled="busy" @click="creating = !creating">{{ t('account.createUser') }}</button>
        </template>
      </PageHeader>
      <p v-if="error" role="alert" class="text-sm text-danger-600 dark:text-danger-300">{{ error }}</p>
      <p v-if="notice" role="status" class="text-sm text-success-700 dark:text-success-300">{{ notice }}</p>
      <form v-if="creating" class="space-y-4 rounded-2xl border border-accent-200 bg-white p-6 dark:border-white/10 dark:bg-dark-900" @submit.prevent="create">
        <h2 class="font-bold">{{ t('account.createUser') }}</h2>
        <label class="block text-sm">{{ t('account.name') }}<input v-model="form.display_name" required maxlength="80" autocomplete="off" class="input mt-2 w-full" :disabled="busy" /></label>
        <label class="block text-sm">{{ t('account.email') }}<input v-model="form.email" type="email" required maxlength="254" autocomplete="off" class="input mt-2 w-full" :disabled="busy" /></label>
        <label class="block text-sm">{{ t('account.password') }}<input v-model="form.password" type="password" required minlength="15" maxlength="128" autocomplete="new-password" class="input mt-2 w-full" :disabled="busy" /></label>
        <p class="text-xs text-accent-500 dark:text-accent-300">{{ t('account.passwordHint') }}</p>
        <button type="submit" class="btn btn-primary" :disabled="busy">{{ t('account.createUser') }}</button>
      </form>
      <form v-if="resetUser" class="space-y-4 rounded-2xl border border-accent-200 bg-white p-6 dark:border-white/10 dark:bg-dark-900" @submit.prevent="reset">
        <h2 class="font-bold">{{ t('account.resetFor', { email: resetUser.email }) }}</h2>
        <label class="block text-sm">{{ t('account.newPassword') }}<input v-model="resetPassword" type="password" required minlength="15" maxlength="128" autocomplete="new-password" class="input mt-2 w-full" :disabled="busy" /></label>
        <p class="text-xs text-accent-500 dark:text-accent-300">{{ t('account.passwordHint') }} {{ t('account.passwordRevokes') }}</p>
        <button type="submit" class="btn btn-primary" :disabled="busy">{{ t('account.resetPassword') }}</button>
        <button type="button" class="btn btn-secondary ml-3" :disabled="busy" @click="resetUser = null; resetPassword = ''">{{ t('account.cancel') }}</button>
      </form>
      <form class="flex gap-3" @submit.prevent="page = 1; perform(load)">
        <input v-model="query" :aria-label="t('account.search')" :placeholder="t('account.search')" class="input flex-1" maxlength="200" />
        <button type="submit" class="btn btn-secondary" :disabled="busy">{{ t('account.searchButton') }}</button>
      </form>
      <section class="overflow-x-auto rounded-2xl border border-accent-200 bg-white dark:border-white/10 dark:bg-dark-900" :aria-busy="busy">
        <table class="w-full text-left text-sm">
          <thead class="border-b border-accent-200 dark:border-white/10"><tr><th class="p-4">{{ t('account.name') }}</th><th class="p-4">{{ t('account.email') }}</th><th class="p-4">{{ t('account.status') }}</th><th class="p-4">{{ t('account.actions') }}</th></tr></thead>
          <tbody>
            <tr v-for="user in users" :key="user.id" class="border-b border-accent-100 dark:border-white/5">
              <td class="p-4">{{ user.display_name }}</td><td class="p-4">{{ user.email }}</td><td class="p-4">{{ t(user.status === 'enabled' ? 'account.enabled' : 'account.disabled') }}</td>
              <td class="space-x-3 whitespace-nowrap p-4">
                <button class="font-semibold text-primary-600 dark:text-primary-300" :disabled="busy" @click="perform(async () => { await setAccountUserStatus(user.id, user.status === 'enabled' ? 'disabled' : 'enabled'); await load() })">{{ t(user.status === 'enabled' ? 'account.disable' : 'account.enable') }}</button>
                <button class="font-semibold text-primary-600 dark:text-primary-300" :disabled="busy" @click="resetUser = user; resetPassword = ''">{{ t('account.resetPassword') }}</button>
              </td>
            </tr>
            <tr v-if="!users.length"><td colspan="4" class="p-8 text-center text-accent-500 dark:text-accent-300">{{ t(busy ? 'account.loading' : 'account.empty') }}</td></tr>
          </tbody>
        </table>
      </section>
      <div class="flex items-center justify-between text-sm">
        <span>{{ t('account.total', { total }) }}</span>
        <div class="flex items-center gap-4">
          <button class="btn btn-secondary" :disabled="page <= 1 || busy" @click="page--; perform(load)">{{ t('account.previous') }}</button><span>{{ page }}</span>
          <button class="btn btn-secondary" :disabled="page * 30 >= total || busy" @click="page++; perform(load)">{{ t('account.next') }}</button>
        </div>
      </div>
    </div>
  </main>
</template>
