<script setup lang="ts">
import type { WorkflowStep } from '@/types/workflow'

const props = defineProps<{
  steps: WorkflowStep[]
  progress: number
}>()
</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
      <div>
        <h2 class="card-title">核心流程进度</h2>
        <p class="muted mt-1">从采集到发布的最小可跑通链路。</p>
      </div>
      <div class="min-w-48">
        <div class="mb-2 flex items-center justify-between text-sm font-medium text-accent-600 dark:text-accent-300">
          <span>完成度</span>
          <span>{{ props.progress }}%</span>
        </div>
        <div class="h-2 rounded-full bg-accent-100 dark:bg-dark-800">
          <div class="h-2 rounded-full bg-brand-600 transition-all" :style="{ width: `${props.progress}%` }" />
        </div>
      </div>
    </div>

    <div class="mt-6 grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
      <article
        v-for="(step, index) in props.steps"
        :key="step.key"
        class="rounded-lg border p-4 transition"
        :class="{
          'border-emerald-200 bg-emerald-50 dark:border-emerald-500/30 dark:bg-emerald-500/10': step.status === 'done',
          'border-blue-200 bg-blue-50 dark:border-blue-500/30 dark:bg-blue-500/10': step.status === 'active',
          'border-rose-200 bg-rose-50 dark:border-rose-500/30 dark:bg-rose-500/10': step.status === 'blocked',
          'border-accent-200 bg-accent-50 dark:border-dark-700 dark:bg-dark-950/70': step.status === 'pending',
        }"
      >
        <div class="flex items-center gap-2">
          <span class="flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-bold" :class="step.status === 'done' ? 'bg-emerald-600 text-white' : step.status === 'active' ? 'bg-blue-600 text-white' : 'bg-white text-accent-500 dark:bg-dark-800 dark:text-accent-300'">
            {{ index + 1 }}
          </span>
          <h3 class="min-w-0 truncate font-semibold text-accent-950 dark:text-white">{{ step.title }}</h3>
        </div>
        <p class="mt-3 text-xs leading-5 text-accent-600 dark:text-accent-300">{{ step.description }}</p>
      </article>
    </div>
  </section>
</template>
