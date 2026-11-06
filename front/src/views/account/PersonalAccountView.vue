<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/components/layout/PageHeader.vue'
import NewAPIAccount from '@/components/account/NewAPIAccount.vue'
import { useAccountStore } from '@/stores/account'
import { changeAccountPassword } from '@/api/accounts'

const { t } = useI18n()
const account = useAccountStore()
const current = ref('')
const password = ref('')
const confirmation = ref('')
const busy = ref(false)
const error = ref('')
async function changePassword() {
  if (password.value !== confirmation.value) { error.value = t('account.passwordMismatch'); return }
  await perform(async () => {
    const result = await changeAccountPassword({ current_password: current.value, password: password.value })
    account.reloadIdentity(result?.data?.newapi_sync_pending ? '/login?newapi_sync=pending' : '/login')
  })
}
async function perform(action: () => Promise<unknown>) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await action() } catch (cause) { error.value = cause instanceof Error ? cause.message : t('account.failed') }
  finally { busy.value = false }
}
</script>

<template>
  <main class="min-h-screen bg-accent-50 p-6 text-accent-950 dark:bg-dark-950 dark:text-white md:p-10">
    <div class="mx-auto max-w-3xl space-y-8">
      <PageHeader :title="t('account.personal')">
        <template #actions>
          <RouterLink to="/" class="btn btn-secondary">{{ t('account.back') }}</RouterLink>
          <RouterLink v-if="account.isAdmin" to="/admin/users" class="btn btn-secondary">{{ t('account.users') }}</RouterLink>
          <button class="btn btn-secondary" :disabled="busy" @click="perform(account.logout)">{{ t('account.logout') }}</button>
        </template>
      </PageHeader>
      <section class="rounded-2xl border border-accent-200 bg-white p-6 dark:border-white/10 dark:bg-dark-900">
        <h2 class="text-lg font-bold">{{ account.user?.display_name }}</h2>
        <p class="mt-2 text-sm">{{ account.user?.email }}</p>
        <p class="mt-2 text-sm text-accent-500 dark:text-accent-300">{{ t(account.isAdmin ? 'account.admin' : 'account.user') }}</p>
      </section>
      <NewAPIAccount />
      <form class="space-y-5 rounded-2xl border border-accent-200 bg-white p-6 dark:border-white/10 dark:bg-dark-900" @submit.prevent="changePassword">
        <h2 class="text-lg font-bold">{{ t('account.changePassword') }}</h2>
        <p class="text-sm text-accent-500 dark:text-accent-300">{{ t('account.passwordHint') }} {{ t('account.passwordRevokes') }}</p>
        <p v-if="error" role="alert" class="text-sm text-danger-600 dark:text-danger-300">{{ error }}</p>
        <label class="block text-sm font-semibold">{{ t('account.currentPassword') }}<input v-model="current" type="password" autocomplete="current-password" required maxlength="128" class="input mt-2 w-full" :disabled="busy" /></label>
        <label class="block text-sm font-semibold">{{ t('account.newPassword') }}<input v-model="password" type="password" autocomplete="new-password" required minlength="15" maxlength="128" class="input mt-2 w-full" :disabled="busy" /></label>
        <label class="block text-sm font-semibold">{{ t('account.confirmPassword') }}<input v-model="confirmation" type="password" autocomplete="new-password" required minlength="15" maxlength="128" class="input mt-2 w-full" :disabled="busy" /></label>
        <button type="submit" class="btn btn-primary" :disabled="busy">{{ t(busy ? 'account.submitting' : 'account.changePassword') }}</button>
      </form>
    </div>
  </main>
</template>
