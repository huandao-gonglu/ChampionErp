<script setup lang="ts">
defineProps<{ skuIds?: string[]; assetIds?: string[] }>()
</script>

<template>
  <p v-if="skuIds?.length || assetIds?.length" class="mt-2 text-xs" data-testid="precheck-affected-count">
    <span v-if="skuIds?.length">受影响的 {{ skuIds.length }} 个 SKU</span>
    <span v-if="skuIds?.length && assetIds?.length"> · </span>
    <span v-if="assetIds?.length">涉及 {{ assetIds.length }} 张图片</span>
  </p>
</template>
