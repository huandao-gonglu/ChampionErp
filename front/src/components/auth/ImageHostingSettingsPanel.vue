<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { deleteImageHosting, loadImageHosting, saveImageHosting, setImageHostingDefault, testImageHosting } from '@/api/imageHosting'
import type { ImageHostingConfig, ImageHostingInput, ImageHostingProfile, ImageHostingTestResult } from '@/types/imageHosting'

const config = ref<ImageHostingConfig>({ default_profile_id: '', profiles: [] })
const busy = ref(false)
const error = ref('')
const open = ref(false)
const result = ref<ImageHostingTestResult | null>(null)
const empty = (): ImageHostingInput => ({ id: '', name: '', type: 's3_compatible', endpoint_url: '', region: '', bucket: '', key_prefix: 'products', public_base_url: '', addressing_style: 'auto' })
const form = reactive(empty())
const accessKey = ref('')
const secretKey = ref('')
const credentialsRemovalPending = ref(false)
const hasSavedCredentials = computed(() => !!(form.access_key_id_configured || form.secret_access_key_configured))
const isDefault = computed(() => !!form.id && config.value.default_profile_id === form.id)
const fields = [
  { key: 'name', label: '名称', placeholder: '商品图片存储' },
  { key: 'endpoint_url', label: 'S3 上传接口地址', placeholder: 'https://s3.example.com' },
  { key: 'region', label: 'Region', placeholder: '填写存储商指定值，例如 auto' },
  { key: 'bucket', label: 'Bucket', placeholder: 'product-images' },
  { key: 'key_prefix', label: '对象路径前缀（可为空）', placeholder: 'products' },
  { key: 'public_base_url', label: '公开图片地址前缀', placeholder: 'https://images.example.com' },
] as const
function input(): ImageHostingInput {
  const value: ImageHostingInput = { ...form }
  delete value.last_test
  delete value.config_version
  delete value.access_key_id
  delete value.secret_access_key
  if (!credentialsRemovalPending.value) {
    if (accessKey.value) value.access_key_id = accessKey.value
    if (secretKey.value) value.secret_access_key = secretKey.value
  }
  value.clear_secrets = credentialsRemovalPending.value ? ['access_key_id', 'secret_access_key'] : []
  return value
}
const signature = computed(() => JSON.stringify(input()))
watch(signature, () => { result.value = null })
function setCredentialRemoval(pending: boolean) {
  if (busy.value || (pending && (isDefault.value || !hasSavedCredentials.value))) return
  credentialsRemovalPending.value = pending
}
function edit(profile?: ImageHostingProfile) {
  if (busy.value) return
  Object.keys(form).forEach((key) => delete (form as unknown as Record<string, unknown>)[key])
  Object.assign(form, empty(), profile || {})
  accessKey.value = ''; secretKey.value = ''; credentialsRemovalPending.value = false
  result.value = null; error.value = ''; open.value = true
}
function close() {
  if (busy.value) return
  open.value = false
  accessKey.value = ''; secretKey.value = ''; credentialsRemovalPending.value = false; result.value = null
}
async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await action() } catch (cause) { error.value = cause instanceof Error ? cause.message : '图片托管操作失败' }
  finally { busy.value = false }
}
async function save() {
  await run(async () => {
    const saved = await saveImageHosting(input())
    config.value = saved.image_hosting
    // 成功回读后移除仅驻留于表单的秘密。
    accessKey.value = ''; secretKey.value = ''; credentialsRemovalPending.value = false; open.value = false; result.value = null
  })
}
async function test() {
  await run(async () => {
    const testedSignature = signature.value
    const tested = await testImageHosting(input())
    if (signature.value === testedSignature) result.value = tested
    config.value = await loadImageHosting()
  })
}
async function makeDefault(id: string) { await run(async () => { config.value = await setImageHostingDefault(id) }) }
async function remove(id: string) { await run(async () => { config.value = await deleteImageHosting(id) }) }
onMounted(() => run(async () => { config.value = await loadImageHosting() }))
</script>

<template>
  <section class="space-y-4 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
    <div class="flex items-start justify-between gap-4">
      <div>
        <h3 class="font-semibold">图片托管</h3>
        <p class="muted mt-1">Yandex、Ozon 的本地发布图片使用默认 S3 兼容存储。Mercado Libre 使用平台图片上传。</p>
        <p class="muted mt-1">上传接口地址用于写入，公开图片地址用于平台读取，两者可能不同；公开读取和域名需在存储商侧配置。</p>
      </div>
      <button class="btn btn-primary shrink-0" :disabled="busy" @click="edit()">新增配置</button>
    </div>
    <p v-if="error && !open" role="alert" class="text-red-600">{{ error }}</p>
    <p v-if="!config.profiles.length" class="muted">尚未配置图片托管。使用本地图片时，请先新增配置并设为默认。</p>
    <article v-for="profile in config.profiles" :key="profile.id" class="space-y-2 rounded-lg border border-accent-200 bg-white p-4 dark:border-dark-700 dark:bg-dark-900">
      <div class="flex flex-wrap justify-between gap-3">
        <div>
          <h4 class="font-semibold">{{ profile.name || '未命名配置' }} <span v-if="profile.id === config.default_profile_id" class="text-sm text-primary-600">默认</span></h4>
          <p class="break-all text-sm">{{ profile.endpoint_url || '未填写上传地址' }} · {{ profile.bucket || '未填写 Bucket' }}</p>
          <p class="muted text-sm">Access Key：{{ profile.access_key_id_configured ? '已配置' : '未配置' }}；Secret Key：{{ profile.secret_access_key_configured ? '已配置' : '未配置' }}</p>
        </div>
        <div class="flex flex-wrap items-start gap-2">
          <button class="btn btn-outline" :disabled="busy" @click="edit(profile)">编辑 / 测试</button>
          <button v-if="profile.id !== config.default_profile_id" class="btn btn-outline" :disabled="busy" @click="makeDefault(profile.id)">设为默认</button>
          <button v-else class="btn btn-outline" :disabled="busy" @click="makeDefault('')">解除默认</button>
          <button class="btn btn-outline" :disabled="busy || profile.id === config.default_profile_id" @click="remove(profile.id)">删除</button>
        </div>
      </div>
      <p v-if="profile.last_test" class="text-sm">最近测试 {{ new Date(profile.last_test.checked_at).toLocaleString() }}：上传{{ profile.last_test.upload_ok ? '成功' : profile.last_test.upload_attempted ? '失败' : '未发送' }}，匿名读取{{ profile.last_test.public_access_ok ? '成功' : profile.last_test.public_access_attempted ? '失败' : '未执行' }}。<template v-if="!profile.last_test.upload_ok || !profile.last_test.public_access_ok">{{ profile.last_test.message }} </template>{{ profile.last_test.cleanup_message }}</p>
      <p v-else class="muted text-sm">当前配置尚无测试结果</p>
    </article>
    <WorkspaceDialog :open="open" :title="form.id ? '编辑图片托管' : '新增图片托管'" @close="close">
      <form class="space-y-4" @submit.prevent="save">
        <p class="muted">存储类型：S3 兼容存储。保存仅校验字段；测试会向独立测试路径上传小图片、匿名下载并尝试清理。</p>
        <fieldset :disabled="busy" class="grid gap-4 md:grid-cols-2">
          <label v-for="field in fields" :key="field.key" class="block"><span class="mb-1 block text-sm">{{ field.label }}</span><input v-model="form[field.key]" class="input" :placeholder="field.placeholder" :required="field.key === 'name'"></label>
          <div>
            <label class="block"><span class="mb-1 block text-sm">Access Key ID（{{ form.access_key_id_configured ? credentialsRemovalPending ? '待移除' : '已配置，留空保留' : '未配置' }}）</span><input v-model="accessKey" class="input" type="password" autocomplete="new-password" :placeholder="credentialsRemovalPending ? '保存后移除已保存凭据' : form.access_key_id_configured ? '已配置，留空保持原值' : '输入 Access Key ID'" :disabled="credentialsRemovalPending"></label>
          </div>
          <div>
            <label class="block"><span class="mb-1 block text-sm">Secret Access Key（{{ form.secret_access_key_configured ? credentialsRemovalPending ? '待移除' : '已配置，留空保留' : '未配置' }}）</span><input v-model="secretKey" class="input" type="password" autocomplete="new-password" :placeholder="credentialsRemovalPending ? '保存后移除已保存凭据' : form.secret_access_key_configured ? '已配置，留空保持原值' : '输入 Secret Access Key'" :disabled="credentialsRemovalPending"></label>
          </div>
          <details class="md:col-span-2">
            <summary class="cursor-pointer text-sm">高级设置</summary>
            <label class="mt-2 block"><span class="mb-1 block text-sm">寻址方式</span><select v-model="form.addressing_style" class="input"><option value="auto">auto</option><option value="path">path</option><option value="virtual">virtual</option></select></label>
            <div v-if="hasSavedCredentials" class="mt-4 space-y-2 border-t border-accent-200 pt-3 dark:border-dark-700">
              <template v-if="credentialsRemovalPending">
                <p class="text-sm text-red-600">待移除已保存的访问凭据：点击“保存配置”后将移除 Access Key ID 和 Secret Access Key，此配置将无法上传图片。</p>
                <button type="button" class="btn btn-outline" :disabled="busy" @click="setCredentialRemoval(false)">取消移除</button>
              </template>
              <template v-else>
                <p class="muted text-sm">移除已保存的访问凭据后，此配置将无法上传图片。</p>
                <p v-if="isDefault" class="muted text-sm">当前为默认配置，请先在列表中解除默认，再移除凭据。</p>
                <button type="button" class="btn btn-outline text-red-600" :disabled="busy || isDefault" @click="setCredentialRemoval(true)">移除已保存凭据</button>
              </template>
            </div>
          </details>
        </fieldset>
        <p v-if="error" role="alert" class="text-red-600">{{ error }}</p>
        <div v-if="result" role="status" class="rounded-lg border border-accent-200 p-3 text-sm">
          <p>上传：{{ result.upload_ok ? '成功' : result.upload_attempted ? '失败' : '未发送' }}</p><p>匿名读取：{{ result.public_access_ok ? '成功' : result.public_access_attempted ? '失败' : '未执行' }}</p>
          <p v-if="!result.upload_ok || !result.public_access_ok">{{ result.message }}</p><p>清理：{{ result.cleanup_message || '未产生测试对象' }}</p>
          <p v-if="result.cleanup_status === 'retained' || result.cleanup_status === 'unknown'" class="break-all">需核查的对象 key：{{ result.storage_key }}</p>
        </div>
        <div class="flex justify-end gap-2"><button type="button" class="btn btn-outline" :disabled="busy || credentialsRemovalPending" @click="test">{{ busy ? '处理中…' : '测试上传与公开读取' }}</button><button type="submit" class="btn btn-primary" :disabled="busy">保存配置</button></div>
      </form>
    </WorkspaceDialog>
  </section>
</template>
