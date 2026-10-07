<script setup lang="ts">
import { computed, inject, ref, onMounted, onBeforeUnmount } from 'vue'
import { routeLocationKey } from 'vue-router'
import { requestPlatformName as platformName } from '@/utils/externalRequestNotices'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { fetchRequestControl, recoverRequestBlock } from '@/api/externalRequests'
import type { RequestBlock, RequestControlStatus } from '@/api/externalRequests'
const status = ref<RequestControlStatus | null>(null)
const error = ref('')
const operationError = ref('')
const message = ref('')
const pending = ref(false)
const selected = ref<RequestBlock | null>(null)
const reason = ref('')
const offset = ref(0)
const now = ref(Date.now() / 1000)
let serverOffset = 0
let poll: ReturnType<typeof setTimeout> | undefined
let clock: ReturnType<typeof setInterval> | undefined
let stopped = false
let generation = 0
const route = inject(routeLocationKey, undefined)
const targetPlatforms = computed(() => {
  const value = route?.query.request_platform
  return (Array.isArray(value) ? value : [value]).filter((item): item is string => typeof item === 'string' && !!item)
})
const isTarget = (platform: string) => targetPlatforms.value.includes(platform)
const blocks = computed(() => [...(status.value?.blocks || [])].sort((a, b) => Number(isTarget(b.platform)) - Number(isTarget(a.platform))))
const targetLabel = computed(() => targetPlatforms.value.map(platformName).join('、'))
const scopeNames: Record<string, string> = { account: '该账号的全部请求', credential: '使用该凭据的请求', interface: '该接口', quota: '共享配额内的请求', operation: '仅原操作，不影响其他操作', request: '仅原操作中相同接口和参数的请求，不影响新操作' }
const needsReason = (block: RequestBlock | null) => block?.recovery_mode === 'confirm' || block?.recovery_mode === 'confirm_request'
function blockState(block: RequestBlock) {
  if (block.resume_at) return `冷却倒计时：${remaining(block.resume_at)}`
  if (block.recovery_mode === 'confirm_request') return '原请求被明确拒绝，无冷却倒计时'
  if (block.recovery_mode === 'verify_result') return '原操作结果待核对'
  return '需要手动处理原因后恢复'
}
const date = (value: number) => new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
function remaining(at: number) {
  const seconds = Math.max(0, Math.ceil(at - now.value))
  return seconds ? `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : '冷却已结束，等待下一次请求检查'
}
async function refresh() {
  const current = ++generation
  try {
    const result = await fetchRequestControl(offset.value)
    if (stopped || current !== generation) return
    status.value = result
    serverOffset = result.server_time - Date.now() / 1000
    now.value = result.server_time
    error.value = ''
  } catch (exc) {
    if (!stopped && current === generation) error.value = exc instanceof Error ? exc.message : '中断状态读取失败'
  }
}
async function tick() {
  await refresh()
  if (!stopped) poll = setTimeout(tick, 5000)
}
function openRecovery(block: RequestBlock) {
  selected.value = block
  reason.value = ''
  message.value = ''
  operationError.value = ''
}
async function recover() {
  if (!selected.value || pending.value) return
  pending.value = true
  operationError.value = ''
  try {
    const result = await recoverRequestBlock(selected.value.id, reason.value)
    message.value = result.message
    selected.value = null
    await refresh()
  } catch (exc) {
    operationError.value = exc instanceof Error ? exc.message : '恢复操作失败'
  } finally { pending.value = false }
}
async function page(delta: number) { offset.value = Math.max(0, offset.value + delta); await refresh() }
onMounted(() => { void tick(); clock = setInterval(() => { now.value = Date.now() / 1000 + serverOffset }, 1000) })
onBeforeUnmount(() => { stopped = true; clearTimeout(poll); clearInterval(clock) })
</script>
<template>
  <section class="space-y-4" aria-label="平台请求中断与恢复">
    <div>
      <h3 class="font-semibold">请求中断与恢复</h3>
      <p class="muted mt-1">统一查看手动操作和后台任务的中断。恢复后可返回原功能重试，后台任务按原计划继续。</p>
    </div>
    <p v-if="error" role="alert" class="text-red-600 dark:text-red-300">{{ error }}</p>
    <p v-if="message" role="status" class="text-green-700 dark:text-green-300">{{ message }}</p>
    <p v-if="targetLabel && status" role="status" class="muted">
      {{ blocks.some(block => isTarget(block.platform)) ? `已将 ${targetLabel} 的中断记录置顶并突出显示。` : `${targetLabel} 当前没有活动阻断，可返回原功能重试。` }}
    </p>
    <article v-for="block in blocks" :key="block.id" :data-highlighted="isTarget(block.platform) || undefined" :class="{ 'ring-2 ring-amber-500': isTarget(block.platform) }" class="rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-900/10">
      <h4 class="font-semibold">{{ platformName(block.platform) }} · {{ block.account }}</h4>
      <p class="mt-2">{{ block.message }}</p>
      <p v-if="block.http_status" class="muted mt-1">当时的响应：HTTP {{ block.http_status }}</p>
      <p class="mt-2 font-medium" aria-live="off">
        {{ blockState(block) }}
      </p>
      <p class="muted mt-1">影响范围：{{ scopeNames[block.scope] || block.scope }}</p>
      <p v-if="block.interface" class="muted mt-1 break-all">影响接口：{{ block.interface }}</p>
      <p class="muted mt-1">开始于 {{ date(block.created_at) }} · 已拦截 {{ block.blocked_count }} 次请求</p>
      <button v-if="['probe', 'confirm', 'confirm_request'].includes(block.recovery_mode)" class="btn btn-primary mt-3" :disabled="pending" @click="openRecovery(block)">
        {{ block.recovery_mode === 'probe' ? '手动恢复' : block.recovery_mode === 'confirm_request' ? '已处理原因，恢复原请求' : '已处理原因，恢复请求' }}
      </button>
      <p v-else-if="block.recovery_mode === 'verify_result'" class="mt-2">请先在原功能核对业务回执，不能直接重放结果未知的写入。</p>
      <p v-else class="muted mt-2">正在检查恢复情况或等待平台规定的冷却时间。</p>
    </article>
    <p v-if="status && !status.blocks.length" class="muted">当前没有阻断中的请求。</p>
    <div class="flex items-center justify-between"><h4 class="font-semibold">中断记录</h4><button class="btn btn-outline" @click="refresh">刷新</button></div>
    <p class="muted">历史记录保留当时的失败，不表示当前仍被阻断。</p>
    <article v-for="item in status?.history || []" :key="item.id" class="rounded-lg border border-accent-200 p-3 dark:border-dark-700">
      <div class="flex flex-wrap justify-between gap-2"><strong>{{ platformName(item.platform) }}</strong><span>{{ date(item.created_at) }}</span></div>
      <p class="mt-1">{{ item.message }}</p>
      <p class="muted mt-1 break-all">{{ item.local_rejection ? '请求被本地暂停' : '请求执行中断' }} · {{ item.interface }}</p>
    </article>
    <div v-if="status" class="flex items-center gap-3">
      <button class="btn btn-outline" :disabled="offset === 0" @click="page(-50)">上一页</button>
      <span>共 {{ status.total }} 条中断记录</span>
      <button class="btn btn-outline" :disabled="offset + 50 >= status.total" @click="page(50)">下一页</button>
    </div>
    <WorkspaceDialog :open="selected !== null" title="恢复平台请求" :close-disabled="pending" @close="selected = null">
      <form class="space-y-4" @submit.prevent="recover">
        <p v-if="selected?.recovery_mode === 'probe'">手动恢复会提前允许下一次只读请求检查网络；成功后恢复，失败则延长冷却。</p>
        <p v-else-if="selected?.recovery_mode === 'confirm_request'">平台已明确拒绝原请求。请确认拒绝原因已处理；恢复只解除原请求的限制，保留失败记录，不会自动重新发送。</p>
        <p v-else>请先修复授权、开通接口权限，或取得平台恢复账号的确认。</p>
        <label v-if="needsReason(selected)" class="block">已处理的原因
          <input v-model="reason" class="input mt-2 w-full" required maxlength="500" :disabled="pending" />
        </label>
        <p v-if="operationError" role="alert" class="text-red-600">{{ operationError }}</p>
        <button class="btn btn-primary" type="submit" :disabled="pending || (needsReason(selected) && !reason.trim())">{{ pending ? '处理中…' : '确认恢复' }}</button>
      </form>
    </WorkspaceDialog>
  </section>
</template>
