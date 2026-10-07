<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { generateOzonExport, previewOzonExport, type OzonExportPreview, type OzonExportRequest, type OzonExportRow } from '@/api/ozonExport'
import type { BackendOzonExportDownload } from '@/types/workflow.generated'

const props = defineProps<{ open: boolean; listingIds: string[]; accountId: string }>()
const emit = defineEmits<{ close: [] }>()
const step = ref(0), busy = ref(false), error = ref(''), downloadNotice = ref('')
const templateName = ref(''), templateBase64 = ref('')
const preview = ref<OzonExportPreview | null>(null)
const file = ref<BackendOzonExportDownload | null>(null)
const overrides = ref<Record<string, Record<string, string>>>({})
const editing = ref<OzonExportRow | null>(null), editValues = ref<Record<string, string>>({})
const showTitles = ref(false)
let epoch = 0
const downloadUrls = new Set<string>()
const changedTitles = computed(() => preview.value?.rows.filter(row => row.title_changed) || [])
const editFields = [
  ['title', '商品名称'], ['price', '价格 CNY'], ['brand', '品牌'], ['model', '型号名称（用于合并商品卡片）'],
  ['weight_g', '毛重 g'], ['width_mm', '包装宽度 mm'], ['height_mm', '包装高度 mm'], ['length_mm', '包装长度 mm'], ['barcode', '条码（可留空）'],
] as const
function request(): OzonExportRequest {
  return { account_id: props.accountId, listing_ids: [...props.listingIds], template_name: templateName.value,
    template_base64: templateBase64.value, overrides: overrides.value }
}
function reset() {
  epoch++
  step.value = 0; busy.value = false; error.value = ''; downloadNotice.value = ''
  templateName.value = templateBase64.value = ''; preview.value = null; file.value = null
  overrides.value = {}; editing.value = null; showTitles.value = false
}
watch(() => [props.open, props.accountId, props.listingIds.join('|')], reset, { immediate: true })
function readFile(input: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '')
    reader.onerror = () => reject(new Error('读取模板失败，请重新选择文件'))
    reader.readAsDataURL(input)
  })
}
async function chooseTemplate(event: Event) {
  const input = event.target as HTMLInputElement
  const selected = input.files?.[0]
  input.value = ''
  if (!selected || busy.value) return
  error.value = ''; preview.value = null; templateName.value = templateBase64.value = ''; overrides.value = {}
  if (!/\.xlsx$/i.test(selected.name) || selected.size > 3 * 1024 * 1024) {
    error.value = '请选择不超过 3 MB 的官方 XLSX 类目模板'; return
  }
  const ticket = ++epoch
  busy.value = true
  try {
    const encoded = await readFile(selected)
    if (ticket !== epoch) return
    templateName.value = selected.name; templateBase64.value = encoded
    const result = await previewOzonExport(request())
    if (ticket === epoch) preview.value = result
  } catch (e) { if (ticket === epoch) error.value = e instanceof Error ? e.message : '读取模板失败' }
  finally { if (ticket === epoch) busy.value = false }
}
function edit(row: OzonExportRow) {
  editing.value = row
  editValues.value = Object.fromEntries(editFields.map(([key]) => [key, row.fields[key] || '']))
}
async function saveEdit() {
  if (!editing.value || busy.value) return
  const id = editing.value.listing_id, next = { ...overrides.value, [id]: { ...editValues.value } }
  const ticket = ++epoch
  busy.value = true; error.value = ''
  try {
    const result = await previewOzonExport({ ...request(), overrides: next })
    if (ticket !== epoch) return
    overrides.value = next; preview.value = result; editing.value = null; file.value = null
  } catch (e) { if (ticket === epoch) error.value = e instanceof Error ? e.message : '检查导出资料失败' }
  finally { if (ticket === epoch) busy.value = false }
}
async function generate() {
  if (!preview.value || preview.value.summary.missing || busy.value) return
  const ticket = ++epoch
  busy.value = true; error.value = ''
  try {
    const result = await generateOzonExport({ ...request(), preview_fingerprint: preview.value.preview_fingerprint })
    if (ticket === epoch) { file.value = result; step.value = 2 }
  } catch (e) {
    if (ticket !== epoch) return
    error.value = e instanceof Error ? e.message : '生成文件失败'
    // 生成时发现快照变化，重新检查并呈现变化后的资料，交给用户再次确认。
    if ((e as { code?: string }).code === 'ONLINE_CONFLICT') {
      try { const result = await previewOzonExport(request()); if (ticket === epoch) preview.value = result }
      catch { if (ticket === epoch) preview.value = null }
    }
  } finally { if (ticket === epoch) busy.value = false }
}
function download() {
  if (!file.value) return
  const bytes = Uint8Array.from(atob(file.value.file_base64), character => character.charCodeAt(0))
  const url = URL.createObjectURL(new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }))
  downloadUrls.add(url)
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = file.value.filename
  document.body.appendChild(anchor); anchor.click(); anchor.remove()
  downloadNotice.value = '已开始下载，请在浏览器下载列表中查看文件。'
  setTimeout(() => { URL.revokeObjectURL(url); downloadUrls.delete(url) }, 1000)
}
onBeforeUnmount(() => { epoch++; for (const url of downloadUrls) URL.revokeObjectURL(url) })
</script>

<template>
  <WorkspaceDialog :open="open" :title="['选择 Ozon 类目模板', '检查导出商品', '下载导出文件'][step]" width="1088px" :close-disabled="busy" @close="emit('close')">
    <p class="muted mb-4">已选择 {{ listingIds.length }} 个 SKU</p>
    <ol class="mb-6 flex gap-3 text-sm" aria-label="导出步骤"><li v-for="(label, index) in ['选择模板', '检查商品', '下载文件']" :key="label" class="flex-1 rounded-lg border p-3" :class="index===step ? 'border-primary-500 bg-primary-50 text-primary-800 dark:bg-primary-900/30 dark:text-primary-200' : 'border-slate-200 text-slate-500 dark:border-dark-700'" :aria-current="index===step?'step':undefined">{{ index+1 }} · {{ label }}</li></ol>
    <p v-if="error" role="alert" class="mb-4 rounded-lg bg-rose-50 p-3 text-rose-700 dark:bg-rose-950/30 dark:text-rose-200">{{ error }}</p>
    <div v-if="step===0" class="space-y-5">
      <div class="rounded-xl border border-dashed border-slate-300 p-8 text-center dark:border-dark-600">
        <p class="font-semibold">上传从 Ozon 下载的空白类目模板</p><p class="muted mt-2">当前支持圣诞装饰品 · CNY 模板</p>
        <label class="btn btn-outline mt-5 inline-flex cursor-pointer">{{ busy ? '读取模板中…' : '选择 XLSX 文件' }}<input class="sr-only" type="file" accept=".xlsx" :disabled="busy" aria-label="选择 Ozon 模板" @change="chooseTemplate" /></label>
        <p v-if="templateName" class="mt-4 break-all text-sm">{{ templateName }}</p>
      </div>
      <div v-if="preview" class="rounded-lg bg-primary-50 p-4 text-primary-800 dark:bg-primary-900/30 dark:text-primary-200" role="status"><b>{{ preview.template.category }}</b><p class="mt-2">币种：{{ preview.template.currency }} · {{ preview.template.required_fields.length }} 个必填字段 · {{ preview.summary.total }} 个 SKU</p></div>
      <p class="muted">保留官方模板的表头和类目选项。“加速评价收集”将关闭。</p>
    </div>
    <div v-else-if="step===1 && preview" class="space-y-4">
      <div class="grid grid-cols-3 gap-3"><article v-for="([label, count], index) in [['必填资料齐全', preview.summary.ready], ['需要修正', preview.summary.missing], ['条码待补充', preview.summary.barcode_missing]]" :key="label" class="rounded-lg border border-slate-200 p-4 dark:border-dark-700"><p class="muted">{{ label }}</p><b class="mt-2 block text-2xl" :class="index===0?'text-primary-700':'text-amber-700'">{{ count }}</b></article></div>
      <p class="font-medium">类目：{{ preview.template.category }} · 币种：{{ preview.template.currency }}</p>
      <p class="muted">价格沿用 Yandex 账号基础价；包装尺寸、毛重换算为毫米和克，非整数向上取整。</p>
      <div class="overflow-x-auto rounded-lg border border-slate-200 dark:border-dark-700"><table class="w-full min-w-[850px] text-left text-sm"><thead class="bg-slate-50 dark:bg-dark-800"><tr><th class="p-3">SKU / 规格</th><th class="p-3">价格 CNY</th><th class="p-3">毛重 g</th><th class="p-3">包装 长×宽×高 mm</th><th class="p-3">图片</th><th class="p-3">操作</th></tr></thead><tbody><tr v-for="row in preview.rows" :key="row.listing_id" class="border-t border-slate-200 align-top dark:border-dark-700" data-testid="ozon-export-row"><td class="p-3"><div class="flex items-center gap-3"><img v-if="row.fields.main_image" :src="row.fields.main_image" alt="" loading="lazy" referrerpolicy="no-referrer" class="size-10 rounded-lg object-cover" /><div><b>{{ row.fields.variant || row.seller_sku }}<span v-if="row.fields.size_cm"> · {{ row.fields.size_cm }} cm</span></b><p class="muted mt-1 break-all text-xs">{{ row.seller_sku }}</p></div></div><p v-for="message in row.errors" :key="message" class="mt-2 text-rose-700">{{ message }}</p><p v-for="message in row.warnings" :key="message" class="mt-1 text-xs text-amber-700 dark:text-amber-300">{{ message }}</p></td><td class="p-3">{{ row.fields.price || '待补充' }}</td><td class="p-3">{{ row.fields.weight_g || '待补充' }}</td><td class="p-3">{{ row.fields.length_mm || '?' }}×{{ row.fields.width_mm || '?' }}×{{ row.fields.height_mm || '?' }}</td><td class="p-3">{{ row.fields.main_image ? '已取得主图地址' : '缺少主图' }}</td><td class="p-3"><button class="text-primary-700" type="button" :disabled="busy" @click="edit(row)">编辑</button></td></tr></tbody></table></div>
      <div v-if="changedTitles.length" class="flex items-center justify-between rounded-lg bg-amber-50 p-4 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200"><span>{{ changedTitles.length }} 个 SKU 的标题有调整</span><button type="button" class="btn btn-outline" @click="showTitles=true">查看修正</button></div>
      <p class="muted">条码为空会保留空值；文件生成后仍需在 Ozon 上传并查看校验结果。</p>
    </div>
    <div v-else-if="step===2 && file" class="space-y-5">
      <div class="rounded-lg bg-primary-50 p-5 text-primary-800 dark:bg-primary-900/30 dark:text-primary-200" role="status"><b>已生成 {{ listingIds.length }} 个 SKU 的导出文件</b><p class="mt-2 break-all">{{ file.filename }}</p></div>
      <button class="btn btn-primary" type="button" @click="download">下载 XLSX 文件</button><p v-if="downloadNotice" role="status" class="muted">{{ downloadNotice }}</p>
      <ol class="list-inside list-decimal space-y-3"><li>登录 Ozon 卖家后台，进入添加商品的模板上传页面。</li><li>上传下载的 XLSX 文件，查看校验结果；按提示补齐条码或其他资料。</li><li>文件被接受后，继续查看商品审核状态，并在 Ozon 设置库存。</li></ol>
      <p class="muted">加速评价收集：关闭。文件生成完成不代表商品已在 Ozon 上架。</p>
    </div>
    <template #footer><div class="flex items-center justify-between gap-4"><button v-if="step===1" type="button" class="btn btn-outline" :disabled="busy" @click="step=0; error=''">上一步</button><span v-else class="muted text-sm">加速评价收集：关闭</span><button v-if="step===0" type="button" class="btn btn-primary" :disabled="busy || !preview" @click="step=1; error=''">下一步：检查商品</button><button v-else-if="step===1" type="button" class="btn btn-primary" :disabled="busy || !preview || !!preview.summary.missing" @click="generate">{{ busy ? '生成中…' : `生成 ${listingIds.length} 个 SKU 文件` }}</button><button v-else type="button" class="btn btn-outline" @click="emit('close')">完成</button></div></template>
  </WorkspaceDialog>
  <WorkspaceDialog :open="!!editing" title="编辑导出资料" :close-disabled="busy" @close="editing=null">
    <p class="muted mb-5">仅修改本次导出的资料，Yandex 在线商品保持原样。</p>
    <p v-if="error" role="alert" class="mb-4 text-rose-700">{{ error }}</p>
    <div class="grid gap-4 sm:grid-cols-2"><label v-for="[key, label] in editFields" :key="key" class="block" :class="key==='title'?'sm:col-span-2':''">{{ label }}<input v-model="editValues[key]" class="input mt-2 w-full" :aria-label="label" :disabled="busy" :inputmode="['price','weight_g','width_mm','height_mm','length_mm'].includes(key)?'decimal':undefined" /></label></div>
    <template #footer><div class="flex justify-end gap-3"><button type="button" class="btn btn-outline" :disabled="busy" @click="editing=null">取消</button><button type="button" class="btn btn-primary" :disabled="busy" @click="saveEdit">{{ busy ? '检查中…' : '保存并检查' }}</button></div></template>
  </WorkspaceDialog>
  <WorkspaceDialog :open="showTitles" title="查看标题修正" @close="showTitles=false"><div class="space-y-5"><article v-for="row in changedTitles" :key="row.listing_id" class="border-b border-slate-200 pb-4 dark:border-dark-700"><b>{{ row.seller_sku }}</b><p class="muted mt-2">原名称：{{ row.title_before }}</p><p class="mt-2 text-primary-700">导出名称：{{ row.fields.title }}</p></article></div></WorkspaceDialog>
</template>
