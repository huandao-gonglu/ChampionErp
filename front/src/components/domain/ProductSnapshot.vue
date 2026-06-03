<script setup lang="ts">
import type { Product } from '@/types/workflow'

const props = defineProps<{
  product: Product
}>()
</script>

<template>
  <section class="card h-full">
    <div class="flex items-start justify-between gap-4">
      <div>
        <p class="text-xs font-semibold uppercase tracking-wide text-brand-600">采集商品</p>
        <h2 class="mt-1 text-xl font-bold text-slate-950">{{ props.product.name }}</h2>
        <p class="muted mt-1">{{ props.product.source.title }}</p>
      </div>
      <span class="badge-info">{{ props.product.source.sourcePlatform }}</span>
    </div>

    <dl class="mt-5 grid grid-cols-2 gap-3 text-sm">
      <div class="rounded-xl bg-slate-50 p-3">
        <dt class="text-slate-500">采购价</dt>
        <dd class="mt-1 font-semibold">{{ props.product.source.price }} {{ props.product.source.currency }}</dd>
      </div>
      <div class="rounded-xl bg-slate-50 p-3">
        <dt class="text-slate-500">重量</dt>
        <dd class="mt-1 font-semibold">{{ props.product.source.weightKg }} kg</dd>
      </div>
      <div class="rounded-xl bg-slate-50 p-3">
        <dt class="text-slate-500">尺寸</dt>
        <dd class="mt-1 font-semibold">{{ props.product.source.dimensions.lengthCm }} × {{ props.product.source.dimensions.widthCm }} × {{ props.product.source.dimensions.heightCm }} cm</dd>
      </div>
      <div class="rounded-xl bg-slate-50 p-3">
        <dt class="text-slate-500">图片池</dt>
        <dd class="mt-1 font-semibold">{{ props.product.source.imagePool.length }} 张</dd>
      </div>
    </dl>

    <div class="mt-5">
      <h3 class="text-sm font-semibold text-slate-900">卖点</h3>
      <div class="mt-2 flex flex-wrap gap-2">
        <span v-for="point in props.product.sellingPoints" :key="point" class="badge-muted">{{ point }}</span>
      </div>
    </div>
  </section>
</template>
