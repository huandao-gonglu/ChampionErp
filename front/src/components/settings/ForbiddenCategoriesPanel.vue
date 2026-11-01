<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { fetchForbiddenCategories, importForbiddenCategories } from '@/api/forbiddenCategories'
import type { BackendForbiddenCategories } from '@/types/workflow.generated'

const { t, locale } = useI18n({ useScope: 'local', messages: {
  zh: {
    title: 'Yandex 禁售类目', help: '当前名单中的 Yandex 类目不能通过类目预检和发布预检。',
    format: 'XLSX 文件需包含 category（类目 ID）和 name（名称）两列，最大 3 MiB。',
    replaceHelp: '每次成功导入会替换当前名单；导入失败时保留原名单。',
    choose: '导入禁售名单', replace: '替换禁售名单', busy: '正在导入…', loading: '正在读取名单…', retry: '重新读取',
    empty: '尚未导入禁售名单', emptyHelp: '导入 Yandex 禁售类目文件后，系统会在预检时拦截名单中的类目。', count: '当前禁售类目：{count} 个', source: '来源文件', time: '导入时间',
    search: '搜索类目 ID 或名称', id: '类目 ID', name: '类目名称', noResults: '没有匹配的类目',
    saved: '已导入 {count} 个禁售类目，下次预检即生效。', failed: '操作失败', invalid: '请选择不超过 3 MiB 的 XLSX 文件',
    enabled: '已启用', list: '已导入类目', matchCount: '匹配 {count} 个类目', formatTitle: '导入格式说明',
    previous: '上一页', next: '下一页', page: '第 {page} / {total} 页', perPage: '每页条数', range: '第 {start}–{end} 条，共 {total} 条',
    clear: '清空搜索', noResultsHelp: '请更换关键词，或清空搜索查看全部已导入类目。',
    scope: '按类目 ID 精确匹配，仅对 Yandex 生效。',
  },
  en: {
    title: 'Yandex forbidden categories', help: 'Yandex categories on the current list cannot pass category or publication checks.',
    format: 'XLSX files must contain category (ID) and name columns, up to 3 MiB.',
    replaceHelp: 'A successful import replaces the current list. Failed imports preserve the existing list.',
    choose: 'Import list', replace: 'Replace list', busy: 'Importing…', loading: 'Loading list…', retry: 'Reload',
    empty: 'No forbidden category list imported', emptyHelp: 'Import a Yandex category file to block these categories during checks.', count: '{count} forbidden categories', source: 'Source file', time: 'Imported at',
    search: 'Search category ID or name', id: 'Category ID', name: 'Category name', noResults: 'No matching categories',
    saved: 'Imported {count} forbidden categories. The next check will use this list.', failed: 'Operation failed', invalid: 'Choose an XLSX file up to 3 MiB',
    enabled: 'Enabled', list: 'Imported categories', matchCount: '{count} matching categories', formatTitle: 'Import format',
    previous: 'Previous', next: 'Next', page: 'Page {page} of {total}', perPage: 'Rows per page', range: '{start}–{end} of {total}',
    clear: 'Clear search', noResultsHelp: 'Try another keyword, or clear your search to view all imported categories.',
    scope: 'Exact category ID matching. Applies to Yandex only.',
  },
} })
const settings = ref<BackendForbiddenCategories | null>(null)
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const search = ref('')
const page = ref(1)
const pageSize = ref(20)
let disposed = false
const filtered = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  return (settings.value?.categories || []).filter(item => !query || item.category_id.includes(query) || item.name.toLocaleLowerCase().includes(query))
})
const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize.value)))
const pageStart = computed(() => (page.value - 1) * pageSize.value)
const visible = computed(() => filtered.value.slice(pageStart.value, pageStart.value + pageSize.value))
const importedAt = computed(() => settings.value?.imported_at ? new Date(settings.value.imported_at).toLocaleString(locale.value === 'zh' ? 'zh-CN' : 'en-US') : '—')
watch([search, pageSize], () => { page.value = 1 }, { flush: 'sync' })
watch(pageCount, count => { page.value = Math.min(page.value, count) }, { flush: 'sync' })
async function load() {
  loading.value = true; error.value = ''
  try { const result = await fetchForbiddenCategories(); if (!disposed) settings.value = result }
  catch (exc) { if (!disposed) error.value = exc instanceof Error ? exc.message : t('failed') }
  finally { if (!disposed) loading.value = false }
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    if (!file.name.toLowerCase().endsWith('.xlsx') || file.size > 3 * 1024 * 1024) throw new Error(t('invalid'))
    const result = await importForbiddenCategories(file)
    if (!disposed) { settings.value = result; search.value = ''; page.value = 1; notice.value = t('saved', { count: result.categories.length }) }
  } catch (exc) { if (!disposed) error.value = exc instanceof Error ? exc.message : t('failed') }
  finally { input.value = ''; if (!disposed) busy.value = false }
}
onMounted(load)
onBeforeUnmount(() => { disposed = true })
</script>

<template>
  <div class="min-w-0 space-y-5" :aria-busy="busy || loading">
    <div><h3 class="font-semibold">{{ t('title') }}</h3><p class="muted mt-1 text-sm">{{ t('help') }}</p></div>
    <p v-if="loading" role="status" class="muted py-4 text-sm">{{ t('loading') }}</p>
    <p v-if="error" role="alert" class="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-300">{{ error }} <button v-if="!settings" type="button" class="ml-2 underline" :disabled="loading" @click="load">{{ t('retry') }}</button></p>
    <p v-if="notice" role="status" class="text-sm text-primary-700 dark:text-primary-300">{{ notice }}</p>

    <section v-show="!loading" class="rounded-lg border border-accent-200 bg-accent-50/50 p-4 sm:p-5 dark:border-dark-700 dark:bg-dark-950/40">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div v-if="settings?.categories.length" class="min-w-0 space-y-2">
          <span class="inline-flex rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">{{ t('enabled') }}</span>
          <p class="text-lg font-semibold tabular-nums">{{ t('count', { count: settings.categories.length }) }}</p>
          <p class="muted text-xs">{{ t('scope') }}</p>
        </div>
        <div v-else-if="settings" class="min-w-0 space-y-2">
          <h4 class="font-medium">{{ t('empty') }}</h4>
          <p class="muted max-w-xl text-sm">{{ t('emptyHelp') }}</p>
        </div>
        <fieldset :disabled="busy || loading || !settings" class="shrink-0">
          <label class="btn inline-flex cursor-pointer focus-within:ring-2 focus-within:ring-primary-500" :class="[settings?.categories.length ? 'btn-secondary' : 'btn-primary', { 'pointer-events-none opacity-50': busy || loading || !settings }]">
            {{ busy ? t('busy') : settings?.categories.length ? t('replace') : t('choose') }}
            <input type="file" accept=".xlsx" class="sr-only" @change="upload" />
          </label>
        </fieldset>
      </div>
      <dl v-if="settings?.categories.length" class="mt-4 grid min-w-0 gap-4 border-t border-accent-200 pt-4 text-sm sm:grid-cols-2 dark:border-dark-700">
        <div class="min-w-0"><dt class="muted text-xs">{{ t('source') }}</dt><dd class="mt-1 break-all">{{ settings.filename }}</dd></div>
        <div class="min-w-0"><dt class="muted text-xs">{{ t('time') }}</dt><dd class="mt-1 tabular-nums">{{ importedAt }}</dd></div>
      </dl>
      <p v-if="settings?.categories.length" class="muted mt-4 text-xs">{{ t('replaceHelp') }}</p>
      <details v-if="settings" class="mt-3 text-xs text-accent-500 dark:text-accent-400">
        <summary class="w-fit cursor-pointer rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500">{{ t('formatTitle') }}</summary>
        <p class="mt-2 leading-5">{{ t('format') }}</p>
      </details>
    </section>

    <section v-if="settings?.categories.length" :aria-label="t('list')" class="min-w-0 space-y-3">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-baseline gap-3"><h4 class="text-sm font-semibold">{{ t('list') }}</h4><span v-if="search.trim()" class="muted text-xs" aria-live="polite">{{ t('matchCount', { count: filtered.length }) }}</span></div>
        <label class="w-full text-sm sm:w-72"><span class="sr-only">{{ t('search') }}</span><input v-model="search" type="search" class="input w-full" :placeholder="t('search')" /></label>
      </div>
      <div class="overflow-hidden rounded-lg border border-accent-200 dark:border-dark-700">
        <table class="w-full table-fixed text-left text-sm">
          <caption class="sr-only">{{ t('list') }}</caption>
          <thead class="bg-accent-50 dark:bg-dark-800"><tr><th scope="col" class="px-4 py-3 font-medium">{{ t('name') }}</th><th scope="col" class="w-32 px-4 py-3 font-medium sm:w-40">{{ t('id') }}</th></tr></thead>
          <tbody><tr v-for="item in visible" :key="item.category_id" class="border-t border-accent-100 hover:bg-accent-50/60 dark:border-dark-800 dark:hover:bg-dark-800/50"><td class="break-words px-4 py-2.5 leading-6">{{ item.name }}</td><td class="break-all px-4 py-2.5 align-top font-mono text-xs leading-6 text-accent-500 dark:text-accent-400">{{ item.category_id }}</td></tr></tbody>
        </table>
        <div v-if="!visible.length" class="space-y-2 border-t border-accent-200 px-4 py-10 text-center dark:border-dark-700">
          <p class="text-sm font-medium">{{ t('noResults') }}</p><p class="muted text-xs">{{ t('noResultsHelp') }}</p>
          <button type="button" class="text-sm text-primary-700 underline dark:text-primary-300" @click="search = ''">{{ t('clear') }}</button>
        </div>
      </div>
      <div v-if="filtered.length" class="flex flex-wrap items-center justify-between gap-3 text-xs">
        <p class="muted tabular-nums" aria-live="polite">{{ t('range', { start: pageStart + 1, end: pageStart + visible.length, total: filtered.length }) }}</p>
        <div class="flex flex-wrap items-center gap-3">
          <label class="flex items-center gap-2 muted">{{ t('perPage') }}<select v-model.number="pageSize" class="input w-20 py-1 text-xs"><option :value="20">20</option><option :value="50">50</option><option :value="100">100</option></select></label>
          <nav :aria-label="t('list')" class="flex items-center gap-2">
            <button type="button" class="btn btn-secondary px-2 py-1 text-xs" :disabled="page === 1" @click="page--">{{ t('previous') }}</button>
            <span class="whitespace-nowrap tabular-nums">{{ t('page', { page, total: pageCount }) }}</span>
            <button type="button" class="btn btn-secondary px-2 py-1 text-xs" :disabled="page === pageCount" @click="page++">{{ t('next') }}</button>
          </nav>
        </div>
      </div>
    </section>
  </div>
</template>
