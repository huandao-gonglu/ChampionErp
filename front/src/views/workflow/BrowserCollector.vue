<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { BrowserDebugStatus } from '@/types/workflow'

const props = defineProps<{ status: BrowserDebugStatus | null; loading: boolean }>()
const emit = defineEmits<{ open: []; check: []; profile: []; collect: [saveOnly: boolean, tabUrl: string] }>()
const selectedUrl = ref('')
const tabs = computed(() => props.status?.connected ? props.status.tabs.filter((tab) => /^https?:\/\//i.test(tab.url)) : [])
const selectedTab = computed(() => tabs.value.find((tab) => tab.url === selectedUrl.value))
watch(tabs, (next) => {
  if (next.some((tab) => tab.url === selectedUrl.value)) return
  const supported = next.filter((tab) => ['1688', 'amazon'].includes(tab.platformDetected))
  selectedUrl.value = supported.length === 1 ? supported[0].url : next.length === 1 ? next[0].url : ''
}, { immediate: true })
</script>

<template>
  <section data-testid="collect-active-card" class="card space-y-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div><h3 class="card-title">浏览器采集</h3><p class="muted mt-1">在专用 Chrome 登录并打开商品详情页，选择页面后采集到商品库。</p></div>
      <span class="rounded-full px-3 py-1 text-xs font-medium" :class="props.status?.connected ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200' : 'bg-slate-100 text-slate-600 dark:bg-dark-700 dark:text-accent-200'">{{ props.status ? props.status.connected ? '浏览器已连接' : '浏览器未连接' : '尚未检测' }}</span>
    </div>
    <div class="rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-dark-700 dark:bg-dark-900/60">
      <div class="flex flex-wrap items-center justify-between gap-4">
        <div><p class="text-sm font-semibold">1. 打开浏览器，准备商品页面</p><p class="muted mt-1 text-xs">支持 1688 / Amazon。遇到登录或验证码时，请先在浏览器中完成。</p></div>
        <button class="btn btn-primary" :disabled="props.loading" @click="emit('open')">打开采集浏览器</button>
      </div>
    </div>
    <div>
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div><h4 class="text-sm font-semibold">2. 选择要采集的页面</h4><p class="muted mt-1 text-xs">打开、切换或关闭商品页面后，刷新此列表。</p></div>
        <button class="btn btn-outline" :disabled="props.loading" @click="emit('check')">{{ props.status ? '刷新标签页' : '检测浏览器' }}</button>
      </div>
      <div v-if="!tabs.length" class="mt-4 rounded-2xl border border-dashed border-slate-300 p-8 text-center dark:border-dark-600">
        <p class="text-sm font-medium">{{ props.status?.connected ? '没有可采集的网页' : '等待连接采集浏览器' }}</p>
        <p class="muted mt-2 text-xs">{{ props.status?.nextAction || '点击“打开采集浏览器”，登录并打开商品详情页，再检测浏览器。' }}</p>
      </div>
      <fieldset v-else class="mt-4 space-y-2">
        <legend class="sr-only">选择浏览器标签页</legend>
        <label v-for="(tab, index) in tabs" :key="`${tab.url}-${index}`" class="flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-colors" :class="selectedUrl === tab.url ? 'border-primary-400 bg-primary-50/60 dark:border-primary-500 dark:bg-primary-500/10' : 'border-slate-200 hover:bg-slate-50 dark:border-dark-700 dark:hover:bg-dark-900'">
          <input v-model="selectedUrl" type="radio" name="collect-browser-tab" :value="tab.url" :disabled="props.loading" :aria-label="tab.title || tab.url" class="mt-1" />
          <span class="min-w-0 flex-1"><span class="block text-sm font-medium">{{ tab.title || '未命名页面' }}</span><span class="mt-1 block break-all text-xs text-slate-500 dark:text-accent-300">{{ tab.url }}</span></span>
          <span class="text-xs text-slate-500 dark:text-accent-300">{{ tab.platformDetected === 'unknown' ? '其他' : tab.platformDetected }}</span>
        </label>
      </fieldset>
      <p v-if="tabs.length && !selectedTab" class="mt-3 text-xs text-amber-700 dark:text-amber-300">检测到多个页面，请明确选择一个商品详情页。</p>
    </div>
    <div class="flex flex-wrap items-center justify-between gap-4 border-t border-slate-200 pt-5 dark:border-dark-700">
      <div><h4 class="text-sm font-semibold">3. 采集所选页面</h4><p class="muted mt-1 text-xs">商品资料写入商品库；HTML 快照可用于排查和手动导入。</p></div>
      <div class="flex flex-wrap gap-2">
        <button class="btn btn-primary" :disabled="props.loading || !selectedTab" @click="emit('collect', false, selectedUrl)">采集所选页面</button>
        <button class="btn btn-outline" :disabled="props.loading || !selectedTab" @click="emit('collect', true, selectedUrl)">保存 HTML 快照</button>
      </div>
    </div>
    <details class="text-xs text-slate-500 dark:text-accent-300">
      <summary class="cursor-pointer">连接详情与排查</summary>
      <div class="mt-3 space-y-2"><p>调试端口：{{ props.status?.port || 9222 }} · 网页：{{ tabs.length }}</p><p v-if="props.status?.errorMessage">{{ props.status.errorMessage }}</p><p v-if="props.status?.errorCode">错误码：{{ props.status.errorCode }}</p><button class="btn btn-outline py-1.5 text-xs" :disabled="props.loading" @click="emit('profile')">打开浏览器配置文件夹</button></div>
    </details>
  </section>
</template>
