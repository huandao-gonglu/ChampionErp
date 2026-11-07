<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useAccountStore } from '@/stores/account'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { fetchTariffSettings, importTariffFile, readTariffFile, upgradeTariff, type TariffDiff, type TariffImport, type TariffSettings } from '@/api/managedSettings'

const account = useAccountStore()
const items = ref<TariffSettings[]>([])
const busy = ref(false)
const error = ref('')
const notice = ref('')
const importing = ref<TariffSettings | null>(null)
const upgrading = ref<TariffSettings | null>(null)
const scope = ref<TariffImport['scope']>('personal')
const file = ref<File | null>(null)
const effectiveDate = ref('')
const preview = ref<TariffDiff | null>(null)
const prepared = ref<TariffImport | null>(null)
const dialogError = ref('')
const expectedVersion = computed(() => scope.value === 'shared' ? importing.value?.shared.version : importing.value?.version)
const platformName = (platform: string) => platform === 'ozon' ? 'Ozon OGL' : 'Yandex'
const fieldLabels: Record<string, string> = {
  fixed: '固定费（原币）', per_g: '每克费用（原币）', step_g: '重量步长（克）',
  carrier: '物流商', service: '服务', group: '包裹类别', transport: '运输方式', name: '渠道名称',
  battery: '电池准入', liquid: '液体准入', min_g: '最低重量（克）', max_g: '最高重量（克）',
  price_min_cny: '最低货值（元）', price_max_cny: '最高货值（元）',
  max_edge_cm: '最长边（厘米）', max_sum_cm: '边长和（厘米）', volume_divisor_g: '体积重除数',
}
const displayValue = (value: unknown) => typeof value === 'boolean' ? (value ? '允许' : '禁止') : String(value ?? '空')

async function load() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try { items.value = await fetchTariffSettings() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '运费表读取失败' }
  finally { busy.value = false }
}
function openImport(item: TariffSettings, target: TariffImport['scope']) {
  importing.value = item
  scope.value = target
  file.value = null
  effectiveDate.value = ''
  preview.value = prepared.value = null
  dialogError.value = ''
  notice.value = ''
}
function changed() {
  preview.value = prepared.value = null
  dialogError.value = ''
}
function chooseFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] || null
  changed()
}
async function inspect() {
  if (!importing.value || !file.value || busy.value) return
  busy.value = true
  dialogError.value = ''
  try {
    if (!file.value.name.toLowerCase().endsWith('.xlsx') || file.value.size > 8 * 1024 * 1024) throw new Error('请选择不超过 8 MiB 的 XLSX 运费表')
    const body: TariffImport = {
      platform: importing.value.platform, scope: scope.value, filename: file.value.name,
      content_base64: await readTariffFile(file.value), expected_version: expectedVersion.value || '', preview: true,
      ...(effectiveDate.value ? { effective_date: effectiveDate.value } : {}),
    }
    const result = await importTariffFile(body)
    preview.value = result.diff
    prepared.value = body
  } catch (cause) { dialogError.value = cause instanceof Error ? cause.message : '运费表校验失败' }
  finally { busy.value = false }
}
async function commit() {
  if (!prepared.value || busy.value) return
  busy.value = true
  dialogError.value = ''
  try {
    const result = await importTariffFile({ ...prepared.value, preview: false })
    items.value = result.items
    notice.value = scope.value === 'shared' ? '默认运费表已发布，自动跟随的账号将在下次核价时使用新版。' : '个人运费表已启用，下次核价生效。'
    importing.value = null
    prepared.value = null
  } catch (cause) { dialogError.value = cause instanceof Error ? cause.message : '运费表导入失败' }
  finally { busy.value = false }
}
async function confirmUpgrade() {
  if (!upgrading.value || busy.value) return
  busy.value = true
  dialogError.value = ''
  try {
    items.value = await upgradeTariff(upgrading.value)
    upgrading.value = null
    notice.value = '已使用管理员版本，后续将自动跟随更新。请重新计算核价。'
  } catch (cause) { dialogError.value = cause instanceof Error ? cause.message : '运费表升级失败' }
  finally { busy.value = false }
}
onMounted(load)
</script>

<template>
  <div class="space-y-5" :aria-busy="busy">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div><h3 class="font-semibold">国际物流运费表</h3><p class="muted mt-1 text-sm">FBS 运费计算。默认跟随管理员发布的版本，导入个人表后由你确认是否升级覆盖。</p></div>
      <button type="button" class="btn btn-secondary" :disabled="busy" @click="load">重新读取</button>
    </div>
    <p v-if="error" role="alert" class="text-sm text-red-600 dark:text-red-400">{{ error }}</p>
    <p v-if="notice" role="status" class="text-sm text-green-700 dark:text-green-400">{{ notice }}</p>
    <p v-if="busy && !items.length" role="status">正在读取运费表…</p>
    <article v-for="item in items" :key="item.platform" class="space-y-4 rounded-lg border border-accent-200 p-4 dark:border-dark-700">
      <h4 class="font-semibold">{{ platformName(item.platform) }}</h4>
      <dl class="grid gap-2 text-sm sm:grid-cols-[7rem_1fr]">
        <dt class="muted">当前来源</dt><dd>{{ item.source === 'shared' ? '管理员默认 · 自动更新' : '个人自定义' }}</dd>
        <dt class="muted">当前版本</dt><dd class="break-all">{{ item.version || '尚未导入' }}</dd>
        <dt class="muted">规则数量</dt><dd>{{ item.rules_count }}</dd>
        <template v-if="item.effective_date"><dt class="muted">生效日期</dt><dd>{{ item.effective_date }}</dd></template>
        <template v-if="item.source === 'personal'"><dt class="muted">管理员版本</dt><dd class="break-all">{{ item.shared.version || '尚未发布' }}</dd></template>
      </dl>
      <p v-if="item.error" role="alert" class="text-sm text-amber-700 dark:text-amber-300">{{ item.error }}</p>
      <p v-if="item.source === 'personal' && item.shared.error" class="text-sm text-amber-700 dark:text-amber-300">管理员版本暂不可用，{{ item.shared.error }}</p>
      <p v-if="item.update_available" role="status" class="text-sm text-amber-700 dark:text-amber-300">管理员已发布更新，你的个人运费表正在继续使用，可确认后升级覆盖。</p>
      <div class="flex flex-wrap gap-3">
        <button type="button" class="btn btn-secondary" :disabled="busy" @click="openImport(item, 'personal')">导入个人运费表</button>
        <button v-if="account.isAdmin" type="button" class="btn btn-primary" :disabled="busy" @click="openImport(item, 'shared')">发布默认运费表</button>
        <button v-if="item.source === 'personal' && item.shared.version" type="button" class="btn btn-secondary" :disabled="busy" @click="upgrading = item; dialogError = ''">{{ item.update_available ? '升级并覆盖个人表' : '恢复跟随管理员' }}</button>
      </div>
    </article>
    <p class="muted text-sm">Mercado Libre 按个人店铺授权向平台接口试算运费。</p>
  </div>

  <WorkspaceDialog :open="!!importing" :title="`${scope === 'shared' ? '发布默认' : '导入个人'}运费表`" width="640px" :close-disabled="busy" @close="importing = null">
    <div v-if="importing" class="space-y-4 py-5">
      <p class="text-sm">{{ platformName(importing.platform) }} · FBS</p>
      <p class="muted text-sm">{{ importing.platform === 'ozon' ? '请上传包含 CHINA OGL 工作表的完整官方格式 XLSX，并填写核实后的生效日期。自定义费率也须遵循该格式。' : '请上传包含大陆渠道、计费单位、包裹类别及轻小件限额说明的完整中文格式 XLSX。' }}文件最大 8 MiB。</p>
      <label class="block text-sm">运费表文件<input type="file" accept=".xlsx" :disabled="busy" class="mt-2 block w-full" @change="chooseFile" /></label>
      <label class="block text-sm">生效日期{{ importing.platform === 'ozon' ? '（必填）' : '（可选）' }}<input v-model="effectiveDate" type="date" :disabled="busy" class="input mt-2 w-full" @change="changed" /></label>
      <p v-if="dialogError" role="alert" class="text-sm text-red-600 dark:text-red-400">{{ dialogError }}</p>
      <section v-if="preview" class="space-y-3 rounded-lg bg-accent-50 p-4 text-sm dark:bg-dark-950">
        <h4 class="font-semibold">校验通过</h4>
        <p class="break-all">版本 {{ preview.after_version }}</p>
        <p>新增 {{ preview.added.length }} 条，删除 {{ preview.removed.length }} 条，修改 {{ preview.changed.length }} 条。</p>
        <details v-if="preview.added.length || preview.removed.length || preview.changed.length">
          <summary class="cursor-pointer font-medium">查看渠道差异</summary>
          <div class="mt-3 max-h-64 space-y-3 overflow-y-auto">
            <p v-for="(rule, index) in preview.added" :key="`added-${index}`">新增 {{ rule.name }}</p>
            <p v-for="(rule, index) in preview.removed" :key="`removed-${index}`">删除 {{ rule.name }}</p>
            <div v-for="rule in preview.changed" :key="rule.id"><p class="font-medium">{{ rule.name }}</p><p v-for="(change, key) in rule.fields" :key="key" class="muted mt-1"><span class="mr-2">{{ fieldLabels[key] || key }}</span>{{ displayValue(change.before) }} → {{ displayValue(change.after) }}</p></div>
          </div>
        </details>
        <p v-if="preview.policy_change">准入条件有调整，请核对原表中的重量、货值等限制。</p>
        <p>{{ scope === 'shared' ? '发布后，自动跟随的账号使用新版；个人自定义账号会收到升级提示。' : '启用后使用此个人版本，管理员发布新版时由你确认是否覆盖。' }}</p>
      </section>
    </div>
    <template #footer>
      <div class="flex justify-end gap-3">
        <button type="button" class="btn btn-secondary" :disabled="busy" @click="importing = null">取消</button>
        <button v-if="!prepared" type="button" class="btn btn-primary" :disabled="busy || !file || (importing?.platform === 'ozon' && !effectiveDate)" @click="inspect">{{ busy ? '正在校验…' : '校验并预览' }}</button>
        <button v-else type="button" class="btn btn-primary" :disabled="busy" @click="commit">{{ busy ? '正在保存…' : scope === 'shared' ? '确认发布' : '确认启用' }}</button>
      </div>
    </template>
  </WorkspaceDialog>
  <WorkspaceDialog :open="!!upgrading" title="确认升级运费表" width="520px" :close-disabled="busy" @close="upgrading = null">
    <div class="space-y-4 py-5 text-sm">
      <p>使用管理员当前发布的 {{ platformName(upgrading?.platform || '') }} 运费表覆盖个人版本，并恢复自动跟随后续更新。</p>
      <p class="break-all">目标版本 {{ upgrading?.shared.version }}</p>
      <p v-if="dialogError" role="alert" class="text-red-600 dark:text-red-400">{{ dialogError }}</p>
    </div>
    <template #footer><div class="flex justify-end gap-3"><button type="button" class="btn btn-secondary" :disabled="busy" @click="upgrading = null">继续使用个人表</button><button type="button" class="btn btn-primary" :disabled="busy" @click="confirmUpgrade">确认覆盖并跟随</button></div></template>
  </WorkspaceDialog>
</template>
