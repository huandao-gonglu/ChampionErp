<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { routerKey } from 'vue-router'
import { fetchAiConfig, loginCanvasAccount, logoutCanvasAccount, toggleCanvasAccount } from '@/api/workflow/settings'
import type { BackendCanvasAccountStatus, BackendCanvasAccountSummary, UnknownRecord } from '@/types/workflow'

const props = defineProps<{
  model: UnknownRecord
  canvasStatus: BackendCanvasAccountStatus
  isBoundToImageTranslate: boolean
  desktopShell: boolean
  localOperations: boolean
  desktopReason: string
  locked: boolean
  isAdmin?: boolean
}>()

const router = inject(routerKey, undefined)

const emit = defineEmits<{
  updateField: [field: string, value: string | boolean]
  accountsUpdated: [status: BackendCanvasAccountStatus]
  bindImageTranslate: []
  handoffDesktop: []
}>()

const pendingAction = ref('')
const feedback = ref<{ type: 'success' | 'error' | 'info'; text: string } | null>(null)

const accounts = computed<BackendCanvasAccountSummary[]>(() => props.canvasStatus.accounts || [])
const enabledAccounts = computed(() => accounts.value.filter(item => item.enabled !== false))
const signedInCount = computed(() => accounts.value.filter(item => item.status === 'signed_in').length)
const desktopBlocked = computed(() => props.desktopShell && !props.localOperations)
const controlsBusy = computed(() => props.locked || Boolean(pendingAction.value))
const managedCanvasUrl = computed(() => fieldText('canvas_url') || fieldText('browser_url'))

function openManagedCanvasSettings() {
  if (router) {
    void router.push('/settings?settings_section=managed')
  }
}

function fieldText(field: string, fallback = ''): string {
  return String(props.model[field] ?? fallback)
}

function eventText(event: Event): string {
  return (event.target as HTMLInputElement).value
}

function eventChecked(event: Event): boolean {
  return (event.target as HTMLInputElement).checked
}

function accountTitle(account: BackendCanvasAccountSummary): string {
  return String(account.email || account.name || '已登录 Google 账号（未识别邮箱）')
}

function accountStatusLabel(account: BackendCanvasAccountSummary): string {
  switch (account.status) {
    case 'signed_in': return '已登录'
    case 'login_required': return '需重新登录'
    case 'rate_limited': return account.cooldown_seconds ? `限流冷却中（${account.cooldown_seconds}s）` : '限流冷却中'
    case 'signed_out': return '未登录'
    default: return '待核验'
  }
}

function accountStatusClass(account: BackendCanvasAccountSummary): string {
  switch (account.status) {
    case 'signed_in': return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-200'
    case 'login_required': return 'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-200'
    case 'rate_limited': return 'bg-orange-100 text-orange-800 dark:bg-orange-500/15 dark:text-orange-200'
    default: return 'bg-slate-200 text-slate-700 dark:bg-dark-700 dark:text-slate-300'
  }
}

function requireDesktopOrRun(action: () => Promise<void>) {
  if (!props.desktopShell) {
    emit('handoffDesktop')
    return
  }
  if (desktopBlocked.value || controlsBusy.value) return
  void action()
}

async function reconcileCanvasStatusFromServer(): Promise<BackendCanvasAccountStatus | null> {
  try {
    const config = await fetchAiConfig()
    const raw = config.raw?.canvas_status
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
    const record = raw as Record<string, unknown>
    const status: BackendCanvasAccountStatus = {
      scope: typeof record.scope === 'string' ? record.scope : 'web_offline',
      desktop_online: record.desktop_online === true,
      accounts: Array.isArray(record.accounts) ? record.accounts as BackendCanvasAccountSummary[] : [],
    }
    emit('accountsUpdated', status)
    return status
  } catch {
    return null
  }
}

function handleAddAccount() {
  requireDesktopOrRun(async () => {
    const previousSignedInProfiles = new Set(
      accounts.value
        .filter(item => item.status === 'signed_in')
        .map(item => String(item.profile || item.id || '')),
    )
    pendingAction.value = 'add'
    feedback.value = { type: 'info', text: '正在打开 Google 登录窗口，请在弹出的内置浏览器中完成登录…' }
    try {
      const result = await loginCanvasAccount({ model: props.model, forceLogin: true })
      if (result.canvasStatus) emit('accountsUpdated', result.canvasStatus)
      if (!result.ok) {
        if (result.gatewayError) {
          const refreshed = await reconcileCanvasStatusFromServer()
          const newlySignedIn = refreshed?.accounts.find(
            item => item.status === 'signed_in' && !previousSignedInProfiles.has(String(item.profile || item.id || '')),
          )
          if (newlySignedIn) {
            feedback.value = { type: 'success', text: `已登录：${accountTitle(newlySignedIn)}` }
            return
          }
        }
        feedback.value = { type: 'error', text: result.error || '添加 Google 账号失败。' }
        return
      }
      const label = result.account ? accountTitle(result.account) : 'Google 账号'
      feedback.value = { type: 'success', text: `已登录：${label}` }
    } catch (error) {
      const refreshed = await reconcileCanvasStatusFromServer()
      const newlySignedIn = refreshed?.accounts.find(
        item => item.status === 'signed_in' && !previousSignedInProfiles.has(String(item.profile || item.id || '')),
      )
      if (newlySignedIn) {
        feedback.value = { type: 'success', text: `已登录：${accountTitle(newlySignedIn)}` }
        return
      }
      feedback.value = { type: 'error', text: error instanceof Error ? error.message : '添加 Google 账号失败。' }
    } finally {
      pendingAction.value = ''
    }
  })
}

function handleCheckAccount(account: BackendCanvasAccountSummary) {
  const profile = String(account.profile || account.id || '')
  if (!profile) return
  requireDesktopOrRun(async () => {
    pendingAction.value = `check:${profile}`
    feedback.value = { type: 'info', text: `正在检查 ${accountTitle(account)} 的登录与 Canvas 通道状态…` }
    try {
      const result = await loginCanvasAccount({ model: props.model, profile, checkOnly: true })
      if (result.canvasStatus) emit('accountsUpdated', result.canvasStatus)
      if (!result.ok) {
        if (result.gatewayError) await reconcileCanvasStatusFromServer()
        feedback.value = { type: 'error', text: result.error || '当前账号未登录或登录已过期，请点击「重新登录」。' }
        return
      }
      const label = result.account ? accountTitle(result.account) : accountTitle(account)
      feedback.value = { type: 'success', text: `通道正常：${label}` }
    } catch (error) {
      feedback.value = { type: 'error', text: error instanceof Error ? error.message : '检查登录状态失败。' }
    } finally {
      pendingAction.value = ''
    }
  })
}

function handleReloginAccount(account: BackendCanvasAccountSummary) {
  const profile = String(account.profile || account.id || '')
  if (!profile) return
  requireDesktopOrRun(async () => {
    pendingAction.value = `relogin:${profile}`
    feedback.value = { type: 'info', text: `正在打开 ${accountTitle(account)} 的 Google 登录窗口…` }
    try {
      const result = await loginCanvasAccount({ model: props.model, profile, forceLogin: true })
      if (result.canvasStatus) emit('accountsUpdated', result.canvasStatus)
      if (!result.ok) {
        if (result.gatewayError) {
          const refreshed = await reconcileCanvasStatusFromServer()
          const matched = refreshed?.accounts.find(
            item => String(item.profile || item.id || '') === profile && item.status === 'signed_in',
          )
          if (matched) {
            feedback.value = { type: 'success', text: `已重新登录：${accountTitle(matched)}` }
            return
          }
        }
        feedback.value = { type: 'error', text: result.error || '重新登录失败。' }
        return
      }
      const label = result.account ? accountTitle(result.account) : accountTitle(account)
      feedback.value = { type: 'success', text: `已重新登录：${label}` }
    } catch (error) {
      const refreshed = await reconcileCanvasStatusFromServer()
      const matched = refreshed?.accounts.find(
        item => String(item.profile || item.id || '') === profile && item.status === 'signed_in',
      )
      if (matched) {
        feedback.value = { type: 'success', text: `已重新登录：${accountTitle(matched)}` }
        return
      }
      feedback.value = { type: 'error', text: error instanceof Error ? error.message : '重新登录失败。' }
    } finally {
      pendingAction.value = ''
    }
  })
}

function handleLogoutAccount(account: BackendCanvasAccountSummary) {
  const profile = String(account.profile || account.id || '')
  if (!profile) return
  requireDesktopOrRun(async () => {
    pendingAction.value = `logout:${profile}`
    feedback.value = null
    try {
      const result = await logoutCanvasAccount(profile)
      if (result.canvasStatus) emit('accountsUpdated', result.canvasStatus)
      feedback.value = { type: 'success', text: `已取消登录并清除本机会话：${accountTitle(account)}` }
    } catch (error) {
      feedback.value = { type: 'error', text: error instanceof Error ? error.message : '取消登录失败。' }
    } finally {
      pendingAction.value = ''
    }
  })
}

function handleToggleAccount(account: BackendCanvasAccountSummary, enabled: boolean) {
  const profile = String(account.profile || account.id || '')
  if (!profile) return
  requireDesktopOrRun(async () => {
    pendingAction.value = `toggle:${profile}`
    feedback.value = null
    try {
      const result = await toggleCanvasAccount(profile, enabled)
      if (result.canvasStatus) emit('accountsUpdated', result.canvasStatus)
    } catch (error) {
      feedback.value = { type: 'error', text: error instanceof Error ? error.message : '更新账号状态失败。' }
    } finally {
      pendingAction.value = ''
    }
  })
}

function handleWebInputClick() {
  if (!props.desktopShell) emit('handoffDesktop')
}
</script>

<template>
  <div class="min-w-0 space-y-4" data-testid="gemini-canvas-panel">
    <div class="rounded-lg border border-accent-200 bg-white p-4 dark:border-dark-700 dark:bg-dark-900">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div class="flex flex-wrap items-center gap-2">
            <h4 class="font-semibold text-accent-950 dark:text-white">{{ fieldText('name', 'Gemini Canvas 修图') }}</h4>
            <span class="rounded-full bg-primary-50 px-2 py-0.5 text-xs font-medium text-primary-700 dark:bg-primary-500/15 dark:text-primary-200">内置通道</span>
            <span class="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600 dark:bg-dark-800 dark:text-slate-300">图像编辑</span>
          </div>
          <p class="mt-1 text-sm text-accent-500 dark:text-accent-400">
            通过本机桌面端登录的 Google 账号免费修图；启用多个 Google 账号后自动并行（最多 3 路）并在限流时轮换。
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <span
            v-if="isBoundToImageTranslate"
            data-testid="canvas-bound-badge"
            class="rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-200"
          >
            已绑定为图片翻译/重绘模型
          </span>
          <button
            v-else
            data-testid="canvas-bind-image-translate"
            class="btn btn-outline py-1.5 text-sm"
            type="button"
            :disabled="controlsBusy || desktopBlocked"
            @click="emit('bindImageTranslate')"
          >
            设为图片翻译/重绘模型
          </button>
        </div>
      </div>

      <div
        v-if="!desktopShell"
        data-testid="canvas-web-handoff-banner"
        class="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2.5 text-sm text-sky-900 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-100"
      >
        <p v-if="canvasStatus.desktop_online">
          在线桌面已连接（已启用 {{ enabledAccounts.length }} 个 / 已登录 {{ signedInCount }} 个 Google 账号），网页端修图可直接由桌面代执行。登录、取消登录或修改代理需在桌面端操作。
        </p>
        <p v-else>
          当前为网页端视图。Google 账号登录、取消登录与本机代理配置需在桌面端完成。
        </p>
        <button
          data-testid="canvas-open-desktop"
          class="btn btn-primary shrink-0 py-1.5 text-sm"
          type="button"
          @click="emit('handoffDesktop')"
        >
          前往桌面端配置
        </button>
      </div>

      <p v-if="desktopBlocked" role="status" class="mt-3 text-sm text-amber-700 dark:text-amber-300">
        {{ desktopReason }}
      </p>
    </div>

    <div class="rounded-lg border border-accent-200 bg-white p-4 dark:border-dark-700 dark:bg-dark-900">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h4 class="font-semibold text-accent-950 dark:text-white">Google 账号</h4>
          <p class="mt-0.5 text-xs text-accent-500 dark:text-accent-400">
            已启用 {{ enabledAccounts.length }} / 共 {{ accounts.length }} 个账号（每个账号独立隔离本机登录态，互不影响）。
          </p>
        </div>
        <button
          data-testid="canvas-add-account"
          class="btn btn-primary py-1.5 text-sm"
          type="button"
          :disabled="controlsBusy || desktopBlocked"
          @click="handleAddAccount"
        >
          {{ pendingAction === 'add' ? '正在等待登录…' : '+ 添加 Google 账号' }}
        </button>
      </div>

      <div
        v-if="feedback"
        role="status"
        class="mt-3 rounded-lg border px-3 py-2 text-sm"
        :class="feedback.type === 'error'
          ? 'border-rose-200 bg-rose-50 text-rose-900 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-100'
          : feedback.type === 'success'
            ? 'border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-100'
            : 'border-sky-200 bg-sky-50 text-sky-900 dark:border-sky-500/40 dark:bg-sky-500/10 dark:text-sky-100'"
      >
        {{ feedback.text }}
      </div>

      <div v-if="accounts.length" class="mt-3 space-y-2.5">
        <article
          v-for="account in accounts"
          :key="String(account.profile || account.id)"
          data-testid="canvas-account-row"
          class="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-accent-200 bg-accent-50/50 px-3 py-2.5 dark:border-dark-700 dark:bg-dark-950/60"
        >
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <span class="truncate font-semibold text-accent-950 dark:text-white">{{ accountTitle(account) }}</span>
              <span class="rounded-full px-2 py-0.5 text-xs font-medium" :class="accountStatusClass(account)">
                {{ accountStatusLabel(account) }}
              </span>
              <span v-if="account.busy" class="rounded-full bg-sky-100 px-2 py-0.5 text-xs text-sky-800 dark:bg-sky-500/15 dark:text-sky-200">修图中</span>
            </div>
            <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">
              <span v-if="account.email && account.name">{{ account.name }} · </span>
              <span>会话 {{ account.profile }}</span>
              <span v-if="account.last_checked_at"> · 最近核验 {{ account.last_checked_at }}</span>
            </p>
            <p v-if="account.last_error && account.status !== 'signed_in'" class="mt-1 text-xs text-rose-600 dark:text-rose-300">
              {{ account.last_error }}
            </p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <label class="flex cursor-pointer items-center gap-1.5 rounded border border-accent-200 bg-white px-2.5 py-1 text-xs dark:border-dark-700 dark:bg-dark-900">
              <input
                type="checkbox"
                :checked="account.enabled !== false"
                :disabled="controlsBusy || desktopBlocked"
                :aria-label="`启用 ${accountTitle(account)}`"
                @change="handleToggleAccount(account, eventChecked($event))"
              />
              <span>参与轮换</span>
            </label>
            <button
              class="btn btn-outline py-1 text-xs"
              type="button"
              :disabled="controlsBusy || desktopBlocked"
              :data-testid="`canvas-check-${account.profile}`"
              @click="handleCheckAccount(account)"
            >
              {{ pendingAction === `check:${account.profile}` ? '检查中…' : '检查状态' }}
            </button>
            <button
              class="btn btn-outline py-1 text-xs"
              type="button"
              :disabled="controlsBusy || desktopBlocked"
              :data-testid="`canvas-relogin-${account.profile}`"
              @click="handleReloginAccount(account)"
            >
              {{ pendingAction === `relogin:${account.profile}` ? '登录中…' : '重新登录' }}
            </button>
            <button
              class="btn btn-outline py-1 text-xs text-rose-700 dark:text-rose-300"
              type="button"
              :disabled="controlsBusy || desktopBlocked || account.busy"
              :data-testid="`canvas-logout-${account.profile}`"
              @click="handleLogoutAccount(account)"
            >
              {{ pendingAction === `logout:${account.profile}` ? '退出中…' : '取消登录' }}
            </button>
          </div>
        </article>
      </div>

      <div
        v-else
        data-testid="canvas-accounts-empty"
        class="mt-3 rounded-lg border border-dashed border-accent-300 bg-accent-50/40 p-4 text-center text-sm text-accent-500 dark:border-dark-600 dark:bg-dark-950/40 dark:text-accent-400"
      >
        {{ desktopShell || canvasStatus.desktop_online ? '尚未登录 Google 账号。点击上方「+ 添加 Google 账号」即可在内置浏览器中登录。' : '桌面端未连接，前往桌面端查看并登录 Google 账号。' }}
      </div>
    </div>

    <div class="rounded-lg border border-accent-200 bg-white p-4 dark:border-dark-700 dark:bg-dark-900">
      <div class="grid gap-3 md:grid-cols-2">
        <label class="block md:col-span-2">
          <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">本机浏览器代理（所有 Google 账号共用）</span>
          <input
            data-testid="canvas-browser-proxy"
            class="input"
            :value="fieldText('browser_proxy')"
            :disabled="controlsBusy || desktopBlocked"
            :readonly="!desktopShell"
            placeholder="可选，例如 http://127.0.0.1:7890"
            @click="handleWebInputClick"
            @input="desktopShell && emit('updateField', 'browser_proxy', eventText($event))"
          />
          <span class="mt-1 block text-xs text-accent-500 dark:text-accent-400">支持 HTTP、HTTPS 和 SOCKS5，地址中不填写账号密码；修改后点击右上角「保存 AI 设置」生效。</span>
        </label>
        <label class="block">
          <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">显示名称</span>
          <input
            class="input"
            :value="fieldText('name')"
            :disabled="controlsBusy || desktopBlocked"
            :readonly="!desktopShell"
            placeholder="Gemini Canvas 修图"
            @click="handleWebInputClick"
            @input="desktopShell && emit('updateField', 'name', eventText($event))"
          />
        </label>
        <label class="block">
          <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">超时秒数</span>
          <input
            class="input"
            :value="fieldText('timeout_seconds', '180')"
            :disabled="controlsBusy || desktopBlocked"
            :readonly="!desktopShell"
            placeholder="180"
            @click="handleWebInputClick"
            @input="desktopShell && emit('updateField', 'timeout_seconds', eventText($event))"
          />
        </label>
        <details class="md:col-span-2 rounded-lg border border-dashed border-accent-200 bg-accent-50/40 px-3 py-2 text-sm dark:border-dark-700 dark:bg-dark-950/40">
          <summary class="cursor-pointer font-semibold text-accent-700 dark:text-accent-200">高级设置</summary>
          <div class="mt-3 grid gap-3 md:grid-cols-2">
            <div class="block md:col-span-2">
              <div class="mb-1 flex flex-wrap items-center justify-between gap-2">
                <span class="text-xs font-semibold text-accent-600 dark:text-accent-300">Google 修图项目地址（管理员统一配置）</span>
                <button
                  v-if="isAdmin"
                  type="button"
                  data-testid="canvas-open-managed-settings"
                  class="text-xs font-medium text-primary-700 underline hover:text-primary-800 dark:text-primary-300"
                  @click="openManagedCanvasSettings"
                >
                  前往管理员共享设置修改
                </button>
              </div>
              <p
                data-testid="canvas-managed-url"
                class="break-all rounded-md border border-accent-200 bg-white px-3 py-2 font-mono text-xs text-accent-700 dark:border-dark-700 dark:bg-dark-900 dark:text-accent-200"
              >
                {{ managedCanvasUrl || '使用系统默认项目地址' }}
              </p>
              <span class="mt-1 block text-xs text-accent-500 dark:text-accent-400">
                {{ isAdmin ? '所有账号共用此地址，可在「系统设置 → 管理员共享设置」中统一更换。' : '由管理员在「系统设置 → 管理员共享设置」统一维护，所有账号自动生效。' }}
              </span>
            </div>
            <label class="block md:col-span-2">
              <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">图片模型名</span>
              <input
                class="input"
                :value="fieldText('model')"
                :disabled="controlsBusy || desktopBlocked"
                :readonly="!desktopShell"
                placeholder="默认 gemini-3.1-flash-image-preview"
                @click="handleWebInputClick"
                @input="desktopShell && emit('updateField', 'model', eventText($event))"
              />
            </label>
          </div>
        </details>
      </div>
    </div>
  </div>
</template>
