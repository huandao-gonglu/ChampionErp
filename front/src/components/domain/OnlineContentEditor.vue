<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch, toRaw } from 'vue'
import { fetchOnlineSourceImages, type OnlineListing, type SourceImageOption, type SourceImageSelection } from '@/api/onlineProducts'
import OnlinePicturePreview from './OnlinePicturePreview.vue'
const props = defineProps<{listing: OnlineListing}>()
const emit = defineEmits<{change:[value:Record<string,unknown>]; picturePreviews:[urls:string[]]}>()
type Picture = string | {id: string; url: string} | {asset_id: string; fingerprint: string; preview_url: string}
const selectedFields = ref<string[]>([])
const title = ref(props.listing.content.title || '')
const description = ref(props.listing.content.description || '')
const pictures = ref<Picture[]>(structuredClone(toRaw(props.listing.content.pictures || [])))
const pictureEditing = computed(() => selectedFields.value.includes('pictures'))
const sourceOpen = ref(false)
const sourceLoading = ref(false)
const sourceError = ref('')
const source = ref<SourceImageSelection | null>(null)
const selectedImages = ref<string[]>([])
let disposed = false
onBeforeUnmount(() => { disposed = true })
const attributes = ref(structuredClone(toRaw(props.listing.content.attributes || [])))
const selectedAttributes = ref<string[]>([])
const values = ref<Record<string,string>>({})
function editable(a: Record<string, unknown>) {
  if(['BRAND','MODEL','GTIN','SELLER_SKU'].includes(String(a.id))) return false
  if(a.valueId || a.value_id) return false
  if(a.values) return Array.isArray(a.values) && a.values.length===1 && !a.values[0]?.id
  return typeof a.value_name==='string' || typeof a.value==='string' || typeof a.value==='number'
}
function attrText(a: Record<string, unknown>) {
  if(Array.isArray(a.values)) return a.values.map(v=>String(v.name || '')).join('、')
  return String(a.value_name ?? a.value ?? a.valueId ?? '')
}
for(const a of attributes.value) values.value[String(a.id)] = attrText(a)
function pictureUrl(p: Picture) {return typeof p==='string'?p:'asset_id' in p?p.preview_url:p.url}
function pictureKey(p: Picture) {return typeof p==='string'?p:'asset_id' in p?`asset:${p.asset_id}`:p.id}
function move(index:number,delta:number) {
  if (!pictureEditing.value) return
  const next=index+delta
  if(next<0 || next>=pictures.value.length)return
  const current=pictures.value.splice(index,1)[0]!
  pictures.value.splice(next,0,current)
}
function remove(index: number) {
  if (pictureEditing.value && pictures.value.length > 1) pictures.value.splice(index, 1)
}
function moveHint(index: number, delta: number) {
  if (!pictureEditing.value) return '请先勾选商品图片'
  if (delta < 0 && index === 0) return '已经是第一张图片'
  if (delta > 0 && index === pictures.value.length - 1) return '已经是最后一张图片'
  return delta < 0 ? '向左移动' : '向右移动'
}
const removeHint = computed(() => !pictureEditing.value ? '请先勾选商品图片' : pictures.value.length === 1
  ? '至少保留一张图片' : '从当前商品图集中移除，源草稿图片仍保留')
function alreadyAdded(option: SourceImageOption) {
  return pictures.value.some(p => typeof p === 'string' ? p === option.existing_url
    : 'asset_id' in p ? p.asset_id === option.asset_id : p.id === option.existing_picture_id || (!!p.url && p.url === option.existing_url))
}
async function loadSource() {
  if (sourceLoading.value) return
  sourceLoading.value = true; sourceError.value = ''
  try {
    const result = await fetchOnlineSourceImages(props.listing.id)
    if (!disposed) { source.value = result; selectedImages.value = [] }
  } catch (error) {
    if (!disposed) sourceError.value = error instanceof Error ? error.message : '读取源草稿图片失败'
  } finally { if (!disposed) sourceLoading.value = false }
}
function toggleSource() {
  sourceOpen.value = !sourceOpen.value
  if (sourceOpen.value && !source.value) void loadSource()
}
function toggleImage(id: string) {
  if (selectedImages.value.includes(id)) selectedImages.value = selectedImages.value.filter(value => value !== id)
  else selectedImages.value.push(id)
}
const pendingImages = computed(() => selectedImages.value.flatMap(id => {
  const image = source.value?.images.find(option => option.asset_id === id)
  return image && !alreadyAdded(image) ? [image] : []
}))
const exceedsLimit = computed(() => pictures.value.length + pendingImages.value.length > 30)
function addSelected() {
  if (!pictureEditing.value || exceedsLimit.value) return
  pictures.value.push(...pendingImages.value.map(({asset_id, fingerprint, preview_url}) => ({asset_id, fingerprint, preview_url})))
  selectedImages.value = []; sourceOpen.value = false
}
const changes = computed(() => {
  const result:Record<string,unknown>={}
  if(selectedFields.value.includes('title')) result.title=title.value
  if(selectedFields.value.includes('description')) result.description=description.value
  if(pictureEditing.value) result.pictures=pictures.value.map(p => typeof p !== 'string' && 'asset_id' in p
    ? {asset_id: p.asset_id, fingerprint: p.fingerprint} : typeof p === 'string' ? p : {id: p.id, url: p.url})
  if(selectedAttributes.value.length) result.attributes=attributes.value.filter(a=>selectedAttributes.value.includes(String(a.id)) && editable(a)).map(a=>{
    const value=values.value[String(a.id)] || ''
    if(props.listing.platform==='yandex') return {id:String(a.id),parameterId:a.parameterId,value,...(a.unitId?{unitId:a.unitId}:{})}
    return a.values ? {id:a.id,values:[{name:value}]} : {id:a.id,value_name:value}
  })
  return result
})
watch(changes, value => {
  emit('change', value)
  emit('picturePreviews', pictures.value.map(pictureUrl))
}, {deep:true})
</script>
<template>
  <div class="space-y-6">
    <label v-if="listing.capabilities.content.fields.includes('title')" class="block"><span class="flex items-center gap-2"><input v-model="selectedFields" type="checkbox" value="title" />商品标题</span><input v-model="title" :disabled="!selectedFields.includes('title')" class="input mt-3" /><span class="muted mt-2 block">标题长度与可编辑条件按当前平台商品规则校验。</span></label>
    <section v-if="listing.capabilities.content.fields.includes('pictures')">
      <label class="flex items-center gap-2"><input v-model="selectedFields" type="checkbox" value="pictures" />商品图片</label>
      <p class="muted mt-2">第一张为主图，至少保留一张图片。修改在确认提交后生效。</p>
      <div class="mt-4 flex flex-wrap gap-3">
        <article v-for="(picture,index) in pictures" :key="pictureKey(picture)" class="w-24" data-testid="target-picture">
          <OnlinePicturePreview :src="pictureUrl(picture)" :alt="`商品图片 ${index + 1}`" class="size-24" />
          <p class="muted text-center">{{ index === 0 ? '主图' : `图片 ${index + 1}` }}</p>
          <div class="mt-1 flex justify-center gap-1">
            <span :title="moveHint(index, -1)"><button type="button" class="size-7 rounded hover:bg-accent-100 disabled:opacity-30 dark:hover:bg-dark-700" :disabled="!pictureEditing || index === 0" :title="moveHint(index, -1)" :aria-label="`左移图片 ${index + 1}`" @click="move(index, -1)">←</button></span>
            <span :title="moveHint(index, 1)"><button type="button" class="size-7 rounded hover:bg-accent-100 disabled:opacity-30 dark:hover:bg-dark-700" :disabled="!pictureEditing || index === pictures.length - 1" :title="moveHint(index, 1)" :aria-label="`右移图片 ${index + 1}`" @click="move(index, 1)">→</button></span>
            <span :title="removeHint"><button type="button" class="size-7 rounded text-rose-600 hover:bg-rose-50 disabled:opacity-30 dark:hover:bg-rose-950" :disabled="!pictureEditing || pictures.length === 1" :title="removeHint" :aria-label="`删除图片 ${index + 1}`" @click="remove(index)">×</button></span>
          </div>
        </article>
      </div>
      <button type="button" class="btn btn-outline mt-3" :disabled="!pictureEditing || pictures.length >= 30" :aria-expanded="sourceOpen && pictureEditing" @click="toggleSource">{{ sourceOpen ? '收起源草稿图片' : '从源草稿添加图片' }}</button>
      <p v-if="pictures.length >= 30" class="muted mt-2">最多保留 30 张图片，请先移除图片再添加。</p>
      <div v-if="sourceOpen && pictureEditing" class="mt-3 rounded-lg border border-accent-200 p-4 dark:border-dark-700" data-testid="source-images">
        <p class="text-sm">从该商品关联的源草稿图片池选取，已添加的图片不会重复添加。</p>
        <p v-if="sourceLoading" role="status" class="muted mt-3">正在读取源草稿图片…</p>
        <div v-else-if="sourceError" class="mt-3"><p role="alert" class="text-rose-600">{{ sourceError }}</p><button type="button" class="btn btn-outline mt-2" @click="loadSource">重新读取</button></div>
        <template v-else-if="source">
          <p v-if="source.reason" role="status" class="muted mt-3">{{ source.reason }}</p>
          <div v-if="source.images.length" class="mt-3 flex max-h-72 flex-wrap gap-3 overflow-y-auto">
            <article v-for="(image,index) in source.images" :key="image.asset_id" class="w-24" data-testid="source-picture">
              <OnlinePicturePreview :src="image.preview_url" :alt="`源草稿图片 ${index + 1}`" class="size-24" />
              <button type="button" class="mt-1 w-full rounded border px-1 py-1 text-xs disabled:opacity-50" :class="selectedImages.includes(image.asset_id) ? 'border-primary-500 text-primary-700 dark:text-primary-300' : 'border-accent-200 dark:border-dark-700'" :aria-label="`选择源草稿图片 ${index + 1}`" :aria-pressed="selectedImages.includes(image.asset_id)" :disabled="alreadyAdded(image)" @click="toggleImage(image.asset_id)">{{ alreadyAdded(image) ? '已添加' : selectedImages.includes(image.asset_id) ? '已选中' : '选择图片' }}</button>
            </article>
          </div>
          <p v-if="exceedsLimit" role="alert" class="mt-3 text-rose-600">添加后超过 30 张，请减少选择。</p>
          <button v-if="source.images.length" type="button" class="btn btn-primary mt-3" :disabled="!pendingImages.length || exceedsLimit" @click="addSelected">添加选中图片（{{ pendingImages.length }}）</button>
        </template>
      </div>
    </section>
    <section v-if="listing.capabilities.content.fields.includes('attributes')"><h3 class="font-medium">商品属性</h3><div class="mt-4 grid gap-4 sm:grid-cols-2"><label v-for="a in attributes" :key="String(a.id)" class="block"><span class="flex items-center gap-2"><input v-model="selectedAttributes" type="checkbox" :value="String(a.id)" :disabled="!editable(a)" />{{ a.name || a.parameterName || a.id }}</span><input v-model="values[String(a.id)]" :disabled="!editable(a) || !selectedAttributes.includes(String(a.id))" class="input mt-2" /><span v-if="!editable(a)" class="muted">身份字段、枚举或复杂属性在此仅供查看</span></label></div></section>
    <label v-if="listing.capabilities.content.fields.includes('description')" class="block"><span class="flex items-center gap-2"><input v-model="selectedFields" type="checkbox" value="description" />商品描述</span><textarea v-model="description" :disabled="!selectedFields.includes('description')" class="input mt-3" rows="7" /></label><p v-else class="muted">当前商品暂不支持在此更新描述。</p>
  </div>
</template>
