<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { PhFloppyDisk, PhPencilSimple, PhStack, PhStar, PhTrash } from '@phosphor-icons/vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import WorkspaceHint from '@/components/shared/WorkspaceHint.vue'
import PricingFeesEditor from './PricingFeesEditor.vue'
import { deletePricingTemplate, fetchPricingTemplates, savePricingTemplate, setDefaultPricingTemplate } from '@/api/pricingWorkspace'
import { applyPricingTemplate, pricingTemplateParameters, selectedPricingTargets } from '@/types/pricing'
import type { PricingTemplate, PricingTemplateSettings } from '@/types/pricing'
import type { PricingInput } from '@/types/workflow'
import { pricingCommonErrors, pricingTargetErrors } from '@/utils/pricingWorkspace'

const props = defineProps<{ input: PricingInput; disabled?: boolean; useDefault?: boolean }>()
const emit = defineEmits<{ ready: []; notice: [message: string] }>()
const settings = ref<PricingTemplateSettings>({ items: [], default_template_id: '' })
const templatesLoaded = ref(false)
let disposed = false
onBeforeUnmount(() => { disposed = true })
const currentId = computed(() => props.input.templateSource?.id || '')
const currentTemplate = computed(() => settings.value.items.find(item => item.id === currentId.value))
const missingTemplate = computed(() => !!currentId.value && !currentTemplate.value)
const initialParameters = JSON.stringify(pricingTemplateParameters(props.input))
const managerOpen = ref(false)
const saveOpen = ref(false)
const busy = ref(false)
const error = ref('')
const search = ref('')
const name = ref('')
const includeRates = ref(false)
const setDefault = ref(true)
const editing = ref<PricingTemplate | null>(null)
const editInput = ref<PricingInput | null>(null)
const filtered = computed(() => settings.value.items.filter(item => item.name.toLowerCase().includes(search.value.trim().toLowerCase())))
function templateErrors(input: PricingInput, withRates: boolean) {
  return [...pricingCommonErrors({ ...input, exchangeRateOverrides: withRates ? input.exchangeRateOverrides : {} }), ...selectedPricingTargets(input).flatMap(pricingTargetErrors)]
}
const saveErrors = computed(() => editInput.value ? templateErrors(editInput.value, includeRates.value) : [])
function hasSavedRates(template: PricingTemplate) {
  return Object.keys(template.parameters.common.exchange_rate_overrides || {}).length > 0
}
function useTemplate(template: PricingTemplate) {
  applyPricingTemplate(props.input, template)
  emit('notice', `已使用「${template.name}」，请点击「计算核价」。`)
}
function choose(event: Event) {
  const template = settings.value.items.find(item => item.id === (event.target as HTMLSelectElement).value)
  if (template) useTemplate(template)
}
async function load() {
  error.value = ''
  try {
    const loaded = await fetchPricingTemplates()
    if (disposed) return
    settings.value = loaded
    templatesLoaded.value = true
    const template = settings.value.items.find(item => item.id === settings.value.default_template_id)
    if (props.useDefault && !currentId.value && template && JSON.stringify(pricingTemplateParameters(props.input)) === initialParameters) useTemplate(template)
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '读取核价模板失败' }
}
function openSave(template: PricingTemplate | null = null) {
  editing.value = template
  name.value = template?.name || ''
  includeRates.value = !!template && hasSavedRates(template)
  setDefault.value = template ? template.id === settings.value.default_template_id : !settings.value.default_template_id
  editInput.value = JSON.parse(JSON.stringify(props.input)) as PricingInput
  if (template) applyPricingTemplate(editInput.value, template)
  error.value = ''
  saveOpen.value = true
}
async function persistTemplate(input: PricingInput, template: PricingTemplate | null, templateName: string, withRates: boolean, makeDefault: boolean) {
  if (busy.value || props.disabled) return
  busy.value = true
  error.value = ''
  try {
    const parameters = pricingTemplateParameters(input, withRates)
    // 保留当前草稿未选用的市场规则。
    if (template) parameters.targets = { ...template.parameters.targets, ...parameters.targets }
    const savedSettings = await savePricingTemplate({ id: template?.id, name: templateName, parameters, set_default: makeDefault })
    if (disposed) return
    settings.value = savedSettings
    return savedSettings.items.find(item => item.id === savedSettings.saved_id)
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '保存核价模板失败' }
  finally { busy.value = false }
}
async function saveCurrent() {
  const template = currentTemplate.value
  if (!template) return
  const withRates = hasSavedRates(template)
  const [firstError] = templateErrors(props.input, withRates)
  if (firstError) {
    error.value = firstError
    return
  }
  const saved = await persistTemplate(props.input, template, template.name, withRates, template.id === settings.value.default_template_id)
  if (saved) {
    if (currentId.value === saved.id) props.input.templateSource = { id: saved.id, name: saved.name }
    emit('notice', `已保存「${saved.name}」。`)
  }
}
async function save() {
  if (!editInput.value || !name.value.trim() || saveErrors.value.length) return
  const saved = await persistTemplate(editInput.value, editing.value, name.value.trim(), includeRates.value, setDefault.value)
  if (!saved) return
  const currentRates = { ...props.input.exchangeRateOverrides }
  useTemplate(saved)
  if (!includeRates.value) props.input.exchangeRateOverrides = currentRates
  saveOpen.value = false
  emit('notice', `已保存「${saved.name}」${setDefault.value ? '并设为默认模板' : ''}。`)
}
async function changeDefault(id: string) {
  busy.value = true
  error.value = ''
  try { settings.value = await setDefaultPricingTemplate(id) }
  catch (exc) { error.value = exc instanceof Error ? exc.message : '设置默认模板失败' }
  finally { busy.value = false }
}
async function remove(template: PricingTemplate) {
  busy.value = true
  error.value = ''
  try {
    settings.value = await deletePricingTemplate(template.id)
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '删除核价模板失败' }
  finally { busy.value = false }
}
onMounted(async () => { await load(); emit('ready') })
</script>

<template>
  <div class="pricing-template-picker">
    <div class="flex items-center gap-1">
      <label class="relative block min-w-0 flex-1">
        <span class="sr-only">选择核价模板</span>
        <PhStack :size="17" class="pointer-events-none absolute left-3 top-3 text-accent-500" />
        <select :value="currentId" class="pricing-input w-full !pl-9" :disabled="disabled || busy" @change="choose">
          <option value="" disabled>选择核价模板</option>
          <option v-if="missingTemplate" :value="currentId" disabled>{{ input.templateSource?.name }}{{ templatesLoaded ? ' · 已删除' : '' }}</option>
          <option v-for="template in settings.items" :key="template.id" :value="template.id">{{ currentId === template.id ? input.templateSource?.name : template.name }}{{ template.id === settings.default_template_id ? ' · 默认' : '' }}</option>
        </select>
      </label>
      <WorkspaceHint label="核价模板说明" content="保存草稿或应用售价时保存模板来源与当前参数；再次打开沿用草稿参数，模板修改或删除不会覆盖它们。默认模板仅在新草稿首次核价时填入。" />
    </div>
    <div class="mt-3 flex items-center justify-between gap-2">
      <button type="button" class="pricing-text-button mr-auto" :disabled="disabled || busy" @click="managerOpen = true">管理模板</button>
      <button v-if="currentTemplate" type="button" class="pricing-text-button" :disabled="disabled || busy" @click="saveCurrent"><PhFloppyDisk :size="15" />保存</button>
      <button type="button" class="pricing-text-button" :disabled="disabled || busy" @click="openSave()">{{ settings.items.length ? '另存模板' : '保存为模板' }}</button>
    </div>
    <p v-if="error && !managerOpen && !saveOpen" class="pricing-field-error mt-2" role="alert">{{ error }} <button v-if="!templatesLoaded" type="button" class="underline" @click="load">重试</button></p>
  </div>

  <WorkspaceDialog :open="managerOpen" title="核价模板" variant="drawer" width="544px" :close-disabled="busy" @close="managerOpen = false">
    <div class="my-5 flex gap-3"><input v-model="search" aria-label="搜索模板" class="pricing-input min-w-0 flex-1" type="search" placeholder="搜索模板" /><button type="button" class="pricing-button" :disabled="busy || disabled" @click="openSave()">新建模板</button></div>
    <p v-if="error && !saveOpen" role="alert" class="pricing-inline-warning mb-4">{{ error }}</p>
    <article v-for="template in filtered" :key="template.id" class="pricing-template-card" :class="{ selected: currentId === template.id }">
      <div class="flex items-center gap-2"><PhStack :size="20" class="text-primary-600" /><h4 class="font-semibold">{{ template.name }}</h4><span v-if="template.id === settings.default_template_id" class="pricing-tag pricing-tag-green">默认</span><WorkspaceHint label="默认模板说明" content="默认模板在新草稿首次核价时自动填入；商品资料、官方佣金和自动运费重新获取。" /></div>
      <p class="pricing-subtle mt-2 text-xs">{{ currentId === template.id ? '当前使用 · ' : '' }}更新于 {{ new Date(template.updated_at).toLocaleDateString('zh-CN') }}</p>
      <dl class="my-4 grid grid-cols-2 gap-2 text-sm"><dt class="pricing-subtle">国内物流</dt><dd class="text-right">¥{{ Number(template.parameters.common.domestic_freight_cny).toFixed(2) }} / 件</dd><dt class="pricing-subtle">包装耗材</dt><dd class="text-right">¥{{ Number(template.parameters.common.packaging_cost_cny).toFixed(2) }} / 件</dd></dl>
      <div class="flex flex-wrap items-center gap-3">
        <button type="button" class="pricing-button pricing-button-primary" :disabled="busy || disabled" @click="useTemplate(template); managerOpen = false">使用此模板</button>
        <button type="button" class="pricing-text-button" :aria-label="`编辑 ${template.name}`" :disabled="busy || disabled" @click="openSave(template)"><PhPencilSimple :size="16" />编辑</button>
        <button type="button" class="pricing-text-button" :disabled="busy" @click="changeDefault(template.id === settings.default_template_id ? '' : template.id)"><PhStar :size="16" :weight="template.id === settings.default_template_id ? 'fill' : 'regular'" />{{ template.id === settings.default_template_id ? '取消默认' : '设为默认' }}</button>
        <button type="button" class="pricing-text-button ml-auto text-rose-600" :aria-label="`删除 ${template.name}`" :disabled="busy" @click="remove(template)"><PhTrash :size="16" /></button>
      </div>
    </article>
    <div v-if="!filtered.length" class="py-14 text-center"><PhStack :size="36" class="mx-auto text-accent-300" /><p class="mt-4 font-semibold">{{ search ? '没有找到匹配模板' : '还没有核价模板' }}</p></div>
    <template #footer><button type="button" class="pricing-button w-full" :disabled="busy" @click="managerOpen = false">完成</button></template>
  </WorkspaceDialog>

  <WorkspaceDialog :open="saveOpen" :title="editing ? '编辑核价模板' : '保存核价模板'" width="560px" :close-disabled="busy" @close="saveOpen = false">
    <label class="my-5 block"><span class="field-label">模板名称</span><input v-model="name" class="pricing-input mt-2 w-full" aria-label="模板名称" maxlength="80" placeholder="例如：日常直发" /></label>
    <details v-if="editInput" :open="!!editing" class="pricing-template-parameters"><summary class="cursor-pointer text-sm font-semibold">{{ editing ? '修改费用与定价规则' : '查看保存的费用与定价规则' }}<WorkspaceHint label="模板保存范围说明" content="仅保存手动费用与定价规则，不含官方佣金、自动运费、商品资料和单个 SKU 覆盖值。" /></summary><PricingFeesEditor :input="editInput" :disabled="busy" /></details>
    <label class="mt-5 flex items-start gap-3 text-sm"><input v-model="includeRates" type="checkbox" class="mt-1" :disabled="busy" /><span class="inline-flex items-center gap-1">同时保存本次手动汇率<WorkspaceHint label="同时保存本次手动汇率说明" content="未勾选时，下次使用 API 实时汇率。" /></span></label>
    <label class="mt-5 flex items-start gap-3 text-sm"><input v-model="setDefault" type="checkbox" class="mt-1" :disabled="busy" /><span class="inline-flex items-center gap-1">设为默认模板<WorkspaceHint label="设为默认模板说明" content="新草稿首次核价时自动填入这些参数。" /></span></label>
    <p v-if="error" role="alert" class="pricing-field-error mt-3">{{ error }}</p>
    <template #footer><div class="flex justify-end gap-3"><button type="button" class="pricing-button" :disabled="busy" @click="saveOpen = false">取消</button><button type="button" class="pricing-button pricing-button-primary" :disabled="busy || !name.trim() || !!saveErrors.length" @click="save">{{ busy ? '正在保存…' : '保存并使用' }}</button></div></template>
  </WorkspaceDialog>
</template>
