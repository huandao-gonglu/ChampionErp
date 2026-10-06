<script setup lang="ts">
import { ref, watch } from 'vue'
import { PhImage } from '@phosphor-icons/vue'
const props = withDefaults(defineProps<{ src?: string; title: string; large?: boolean }>(), {
  src: '', large: false,
})
const failed = ref(false)
watch(() => props.src, () => { failed.value = false })
</script>

<template>
  <div class="order-thumbnail" :class="{ large }">
    <img v-if="src && !failed" :src="src" :alt="title" loading="lazy" decoding="async" referrerpolicy="no-referrer" @error="failed = true" />
    <span v-else class="order-thumbnail-empty" role="img" :aria-label="`${title}：暂无图片`" title="暂无可用商品图片">
      <PhImage :size="large ? 24 : 20" aria-hidden="true" />
      <small v-if="large">暂无图片</small>
    </span>
  </div>
</template>

<style scoped>
.order-thumbnail {
  width: 44px;
  height: 44px;
  flex: 0 0 auto;
  overflow: hidden;
  border: 1px solid var(--order-border);
  border-radius: 6px;
  background: var(--order-soft);
}
.order-thumbnail.large { width: 72px; height: 72px; }
img { width: 100%; height: 100%; object-fit: contain; background: white; }
.order-thumbnail-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  height: 100%;
  color: var(--order-muted);
}
small { font-size: 10px; }
</style>
