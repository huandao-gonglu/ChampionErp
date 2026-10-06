<script setup lang="ts">
import { computed } from 'vue'
import type { OnlineListing } from '@/api/onlineProducts'
import { onlineAttributeName, onlineAttributeText } from './onlineAttributeDisplay'

const props = defineProps<{ listing: OnlineListing }>()
const attributes = computed(() => props.listing.content.attributes || [])
</script>

<template>
  <section v-if="attributes.length || listing.content.attribute_names_error" class="my-6" data-testid="online-attributes">
    <h3 class="font-semibold">商品属性</h3>
    <p v-if="listing.content.attribute_names_error" class="mt-2 text-sm text-amber-700">{{ listing.content.attribute_names_error }}；属性值仍保留，可重新同步后查看名称。</p>
    <dl v-if="attributes.length" class="mt-3 divide-y divide-accent-100 dark:divide-dark-700">
      <div v-for="attribute in attributes" :key="String(attribute.id)" class="grid gap-2 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <dt class="text-sm font-medium">{{ onlineAttributeName(attribute) }}</dt>
        <dd class="whitespace-pre-wrap break-words text-sm">{{ onlineAttributeText(attribute) }}</dd>
      </div>
    </dl>
  </section>
</template>
