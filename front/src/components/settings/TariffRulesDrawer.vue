<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { fetchTariffRules, type TariffRules, type TariffSettings } from '@/api/managedSettings'

const props = defineProps<{ platform: TariffSettings['platform']; scope: 'effective' | 'shared' }>()
const emit = defineEmits<{ close: [] }>()
const details = ref<TariffRules | null>(null)
const loading = ref(false)
const error = ref('')
const search = ref('')
const page = ref(1)
const pageSize = 20
let disposed = false
const title = computed(() => `${props.platform === 'ozon' ? 'Ozon OGL' : 'Yandex'} ${props.scope === 'shared' ? '管理员默认运费表' : '当前运费表'}`)
const filtered = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  return (details.value?.rules || []).filter(rule => !query || [rule.name, rule.carrier, rule.service, rule.transport, rule.group].some(value => value.toLocaleLowerCase().includes(query)))
})
const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize)))
const visible = computed(() => filtered.value.slice((page.value - 1) * pageSize, page.value * pageSize))
watch(search, () => { page.value = 1 })
const permission = (value: boolean | null) => value === null ? '未明确准入' : value ? '允许' : '禁止'
function range(min: string, max: string, unit: string) {
  if (min && max) return `${min}–${max} ${unit}`
  if (max) return `≤ ${max} ${unit}`
  if (min) return `≥ ${min} ${unit}`
  return '表内未注明'
}
async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const result = await fetchTariffRules(props.platform, props.scope)
    if (!disposed) { details.value = result; page.value = 1 }
  } catch (cause) {
    if (!disposed) error.value = cause instanceof Error ? cause.message : '运费规则读取失败'
  } finally { if (!disposed) loading.value = false }
}
onMounted(load)
onBeforeUnmount(() => { disposed = true })
</script>

<template>
  <WorkspaceDialog :open="true" :title="title" variant="drawer" width="1200px" @close="emit('close')">
    <div class="space-y-5 py-5" :aria-busy="loading">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <p v-if="details" class="text-sm">{{ details.source === 'personal' ? '个人自定义' : '管理员默认' }} · FBS · {{ details.rules.length }} 条规则</p>
        <button type="button" class="btn btn-secondary" :disabled="loading" @click="load">重新读取</button>
      </div>
      <p v-if="loading" role="status">正在读取运费规则…</p>
      <p v-if="error" role="alert" class="text-sm text-red-600 dark:text-red-400">{{ error }}</p>
      <template v-if="details">
        <dl class="grid gap-x-5 gap-y-2 text-sm sm:grid-cols-[6rem_1fr]">
          <dt class="muted">版本</dt><dd class="break-all">{{ details.version }}</dd>
          <dt class="muted">来源文件</dt><dd class="break-all">{{ details.filename || '未记录' }}<span v-if="details.sheet" class="muted ml-2">{{ details.sheet }}</span></dd>
          <dt class="muted">生效日期</dt><dd>{{ details.effective_date || '未提供' }}</dd>
          <dt class="muted">导入时间</dt><dd>{{ details.imported_at ? new Date(details.imported_at).toLocaleString('zh-CN') : '未记录' }}</dd>
        </dl>
        <label class="block text-sm">搜索物流商、渠道或包裹类别<input v-model="search" type="search" class="input mt-2 w-full" /></label>
        <p class="muted text-sm">费用统一按每克展示，重量单位为克，尺寸单位为厘米。金额保留运费表币种。</p>
        <div class="overflow-x-auto rounded-lg border border-accent-200 dark:border-dark-700">
          <table class="w-full min-w-[1080px] text-left text-sm">
            <caption class="sr-only">{{ title }}规则明细</caption>
            <thead class="bg-accent-50 dark:bg-dark-950"><tr><th scope="col" class="p-3">渠道</th><th scope="col" class="p-3">费用</th><th scope="col" class="p-3">重量范围</th><th scope="col" class="p-3">货值范围</th><th scope="col" class="p-3">包装限制</th><th scope="col" class="p-3">准入</th><th scope="col" class="p-3">计费重量</th></tr></thead>
            <tbody>
              <tr v-for="rule in visible" :key="rule.id" class="border-t border-accent-200 align-top dark:border-dark-700">
                <td class="max-w-72 space-y-1 p-3"><p class="font-medium">{{ rule.carrier }} · {{ rule.service }}</p><p>{{ rule.transport }} {{ rule.group }}</p><p v-if="rule.source_cell" class="muted text-xs">{{ rule.source_cell }}</p></td>
                <td class="space-y-1 whitespace-nowrap p-3 tabular-nums"><p>固定 {{ rule.fixed }} {{ rule.currency }}</p><p>每克 {{ rule.per_g }} {{ rule.currency }}</p></td>
                <td class="whitespace-nowrap p-3 tabular-nums">{{ range(rule.min_g, rule.max_g, 'g') }}</td>
                <td class="whitespace-nowrap p-3 tabular-nums">{{ range(rule.price_min, rule.price_max, rule.price_currency) }}</td>
                <td class="space-y-1 whitespace-nowrap p-3"><p>{{ rule.max_edge_cm ? `最长边 ≤ ${rule.max_edge_cm} cm` : '最长边未注明' }}</p><p v-if="rule.max_sum_cm">边长和 ≤ {{ rule.max_sum_cm }} cm</p></td>
                <td class="space-y-1 whitespace-nowrap p-3"><p>电池 · {{ permission(rule.battery) }}</p><p>液体 · {{ permission(rule.liquid) }}</p></td>
                <td class="min-w-48 space-y-1 p-3"><p>{{ rule.volume_divisor_g ? `实重与体积重取大，体积重为 cm³ ÷ ${rule.volume_divisor_g}（g）` : '按实际重量' }}</p><p v-if="rule.step_g" class="muted">按 {{ rule.step_g }} g 向上进位</p></td>
              </tr>
              <tr v-if="!visible.length"><td colspan="7" class="p-8 text-center muted">没有匹配的规则</td></tr>
            </tbody>
          </table>
        </div>
        <div class="flex flex-wrap items-center justify-between gap-3 text-sm"><p class="muted">共 {{ filtered.length }} 条</p><nav class="flex items-center gap-3" aria-label="运费规则分页"><button type="button" class="btn btn-secondary" :disabled="page <= 1" @click="page--">上一页</button><span>{{ page }} / {{ pageCount }}</span><button type="button" class="btn btn-secondary" :disabled="page >= pageCount" @click="page++">下一页</button></nav></div>
        <section v-if="details.warnings.length" class="space-y-2 rounded-lg bg-accent-50 p-4 text-sm dark:bg-dark-950"><h4 class="font-medium">适用说明</h4><p v-for="warning in details.warnings" :key="warning" class="muted">{{ warning }}</p></section>
      </template>
    </div>
    <template #footer><button type="button" class="btn btn-secondary" @click="emit('close')">关闭</button></template>
  </WorkspaceDialog>
</template>
