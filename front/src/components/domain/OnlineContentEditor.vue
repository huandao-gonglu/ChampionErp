<script setup lang="ts">
import { computed, ref, watch, toRaw } from 'vue'
import type { OnlineListing } from '@/api/onlineProducts'
const props = defineProps<{listing: OnlineListing}>()
const emit = defineEmits<{change:[value:Record<string,unknown>]}>()
const selectedFields = ref<string[]>([])
const title = ref(props.listing.content.title || '')
const description = ref(props.listing.content.description || '')
const pictures = ref(structuredClone(toRaw(props.listing.content.pictures || [])))
const newImage = ref('')
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
function pictureUrl(p: string | {id:string;url:string}) {return typeof p==='string'?p:p.url}
function move(index:number,delta:number) {
  const next=index+delta
  if(next<0 || next>=pictures.value.length)return
  const current=pictures.value.splice(index,1)[0]!
  pictures.value.splice(next,0,current)
}
function addImage() { if(newImage.value.startsWith('https://')) {pictures.value.push(newImage.value.trim());newImage.value=''} }
const changes = computed(() => {
  const result:Record<string,unknown>={}
  if(selectedFields.value.includes('title')) result.title=title.value
  if(selectedFields.value.includes('description')) result.description=description.value
  if(selectedFields.value.includes('pictures')) result.pictures=pictures.value
  if(selectedAttributes.value.length) result.attributes=attributes.value.filter(a=>selectedAttributes.value.includes(String(a.id)) && editable(a)).map(a=>{
    const value=values.value[String(a.id)] || ''
    if(props.listing.platform==='yandex') return {id:String(a.id),parameterId:a.parameterId,value,...(a.unitId?{unitId:a.unitId}:{})}
    return a.values ? {id:a.id,values:[{name:value}]} : {id:a.id,value_name:value}
  })
  return result
})
watch(changes,value=>emit('change',value),{deep:true,immediate:true})
</script>
<template>
  <div class="space-y-6">
    <label v-if="listing.capabilities.content.fields.includes('title')" class="block"><span class="flex items-center gap-2"><input v-model="selectedFields" type="checkbox" value="title" />商品标题</span><input v-model="title" :disabled="!selectedFields.includes('title')" class="input mt-3" /><span class="muted mt-2 block">标题长度与可编辑条件按当前平台商品规则校验。</span></label>
    <section v-if="listing.capabilities.content.fields.includes('pictures')"><label class="flex items-center gap-2"><input v-model="selectedFields" type="checkbox" value="pictures" />商品图片</label><div class="mt-4 flex flex-wrap gap-3"><article v-for="(p,index) in pictures" :key="index" class="w-24"><img referrerpolicy="no-referrer" :src="pictureUrl(p)" alt="商品图片" class="size-24 rounded-lg border object-contain" /><p class="muted text-center">{{ index===0?'主图':`图片 ${index+1}` }}</p><div class="mt-1 flex justify-center gap-2"><button type="button" :disabled="!selectedFields.includes('pictures') || index===0" :aria-label="`前移图片 ${index+1}`" @click="move(index,-1)">←</button><button type="button" :disabled="!selectedFields.includes('pictures') || index===pictures.length-1" :aria-label="`后移图片 ${index+1}`" @click="move(index,1)">→</button><button type="button" :disabled="!selectedFields.includes('pictures') || pictures.length===1" class="text-rose-600" :aria-label="`移除图片 ${index+1}`" @click="pictures.splice(index,1)">×</button></div></article></div><div v-if="listing.platform!=='mercadolibre' && selectedFields.includes('pictures')" class="mt-3 flex gap-2"><input v-model="newImage" type="url" class="input" placeholder="新图片的 HTTPS 地址" /><button type="button" class="btn btn-outline whitespace-nowrap" @click="addImage">添加图片</button></div><p class="muted mt-3">提交完整目标图集，未移除的图片将保留。{{ listing.platform==='mercadolibre'?'当前支持平台已有图片的排序与移除。':'' }}</p></section>
    <section v-if="listing.capabilities.content.fields.includes('attributes')"><h3 class="font-medium">商品属性</h3><div class="mt-4 grid gap-4 sm:grid-cols-2"><label v-for="a in attributes" :key="String(a.id)" class="block"><span class="flex items-center gap-2"><input v-model="selectedAttributes" type="checkbox" :value="String(a.id)" :disabled="!editable(a)" />{{ a.name || a.parameterName || a.id }}</span><input v-model="values[String(a.id)]" :disabled="!editable(a) || !selectedAttributes.includes(String(a.id))" class="input mt-2" /><span v-if="!editable(a)" class="muted">身份字段、枚举或复杂属性在此仅供查看</span></label></div></section>
    <label v-if="listing.capabilities.content.fields.includes('description')" class="block"><span class="flex items-center gap-2"><input v-model="selectedFields" type="checkbox" value="description" />商品描述</span><textarea v-model="description" :disabled="!selectedFields.includes('description')" class="input mt-3" rows="7" /></label><p v-else class="muted">当前商品暂不支持在此更新描述。</p>
  </div>
</template>
