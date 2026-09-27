<script setup lang="ts">
import type { BuyerLink } from '@/api/onlineProducts'

defineProps<{ links: BuyerLink[] }>()
</script>

<template>
  <a
    v-if="links.length === 1"
    :href="links[0]!.url"
    target="_blank"
    rel="noopener noreferrer"
    class="whitespace-nowrap text-primary-700"
    :aria-label="`查看买家页面：${links[0]!.label}（新窗口）`"
  >查看买家页面 ↗</a>
  <details v-else-if="links.length > 1" class="text-sm">
    <summary class="cursor-pointer whitespace-nowrap text-primary-700">查看买家页面</summary>
    <div class="mt-2 flex flex-col items-start gap-2">
      <a
        v-for="link in links"
        :key="link.url"
        :href="link.url"
        target="_blank"
        rel="noopener noreferrer"
        class="whitespace-nowrap text-primary-700"
        :aria-label="`查看买家页面：${link.label}（新窗口）`"
      >{{ link.label }} ↗</a>
    </div>
  </details>
  <span v-else class="muted text-xs" title="同步商品后，取得平台买家链接才可跳转">暂无买家链接</span>
</template>
