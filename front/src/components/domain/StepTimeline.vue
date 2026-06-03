<script setup lang="ts">
import type { WorkflowStep } from '@/types/workflow'

const props = defineProps<{
  steps: WorkflowStep[]
  progress: number
}>()
</script>

<template>
  <section class="card">
    <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
      <div>
        <h2 class="card-title">核心流程进度</h2>
        <p class="muted mt-1">从采集到发布的最小可跑通链路。</p>
      </div>
      <div class="min-w-48">
        <div class="mb-2 flex items-center justify-between text-sm font-medium text-slate-600">
          <span>完成度</span>
          <span>{{ props.progress }}%</span>
        </div>
        <div class="h-2 rounded-full bg-slate-100">
          <div class="h-2 rounded-full bg-brand-600 transition-all" :style="{ width: `${props.progress}%` }" />
        </div>
      </div>
    </div>

    <div class="mt-6 grid gap-3 md:grid-cols-2 xl:grid-cols-7">
      <article
        v-for="(step, index) in props.steps"
        :key="step.key"
        class="rounded-2xl border p-4 transition"
        :class="{
          'border-emerald-200 bg-emerald-50': step.status === 'done',
          'border-blue-200 bg-blue-50': step.status === 'active',
          'border-rose-200 bg-rose-50': step.status === 'blocked',
          'border-slate-200 bg-slate-50': step.status === 'pending',
        }"
      >
        <div class="flex items-center gap-2">
          <span class="flex size-7 items-center justify-center rounded-full text-xs font-bold" :class="step.status === 'done' ? 'bg-emerald-600 text-white' : step.status === 'active' ? 'bg-blue-600 text-white' : 'bg-white text-slate-500'">
            {{ index + 1 }}
          </span>
          <h3 class="font-semibold text-slate-950">{{ step.title }}</h3>
        </div>
        <p class="mt-3 text-xs leading-5 text-slate-600">{{ step.description }}</p>
      </article>
    </div>
  </section>
</template>
