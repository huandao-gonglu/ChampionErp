<script setup lang="ts">
import { APP_LOGO, APP_NAME, APP_VERSION } from '@/constants/branding'
import type { WorkflowStep } from '@/types/workflow'
import type { WorkflowNavItem } from '@/constants/navigation'
import { useAccountStore } from '@/stores/account'
import { useI18n } from 'vue-i18n'
const account = useAccountStore()
const { t } = useI18n()

const props = defineProps<{
  items: WorkflowNavItem[]
  activeKey: string
  steps: WorkflowStep[]
  progress: number
  collapsed?: boolean
}>()

const emit = defineEmits<{
  navigate: [key: string]
  toggleCollapse: []
}>()

function stepStatus(key: string) {
  return props.steps.find((step) => step.key === key)?.status || 'pending'
}
</script>

<template>
  <aside
    class="flex h-full flex-col border-r border-accent-200 bg-white text-accent-700 shadow-card transition-all duration-300 dark:border-primary-500/20 dark:bg-dark-950 dark:text-accent-100 dark:shadow-glass"
    :class="props.collapsed ? 'w-[84px]' : 'w-[280px]'"
  >
    <div class="flex h-[92px] items-center gap-4 border-b border-accent-200 px-5 dark:border-primary-500/15">
      <div class="flex size-12 shrink-0 items-center justify-center rounded-2xl bg-white p-2 shadow-glow">
        <img :src="APP_LOGO" :alt="APP_NAME" class="size-full" />
      </div>
      <div v-if="!props.collapsed" class="min-w-0">
        <h1 class="truncate text-xl font-black tracking-tight text-accent-950 dark:text-white">{{ APP_NAME }}</h1>
        <div class="mt-1 inline-flex items-center gap-2 rounded-xl bg-warning-50 px-3 py-1 text-sm font-semibold text-warning-700 ring-1 ring-warning-100 dark:bg-warning-950/70 dark:text-warning-300 dark:ring-0">
          <span>{{ APP_VERSION }}</span>
          <span class="size-2 rounded-full bg-warning-400 shadow-glow" />
        </div>
      </div>
    </div>

    <nav class="flex-1 space-y-1.5 overflow-y-auto px-3 py-4">
      <button
        v-for="item in props.items.filter(item => item.key !== 'systemSettings')"
        :key="item.key"
        type="button"
        class="group flex h-12 w-full items-center rounded-2xl px-4 text-left text-[15px] font-semibold transition duration-200"
        :class="[
          item.key === props.activeKey
            ? 'bg-primary-50 text-primary-700 shadow-[inset_0_0_0_1px_rgb(var(--color-primary-200)/0.9)] dark:bg-primary-950/70 dark:text-primary-300 dark:shadow-[inset_0_0_0_1px_rgb(var(--color-primary-500)/0.12)]'
            : 'text-accent-700 hover:bg-accent-50 hover:text-accent-950 dark:text-accent-200 dark:hover:bg-white/5 dark:hover:text-white',
          props.collapsed ? 'justify-center px-0' : 'gap-3',
          item.disabled ? 'cursor-not-allowed opacity-60' : '',
        ]"
        :disabled="item.disabled"
        @click="emit('navigate', item.key)"
      >
        <span
          class="relative flex size-7 shrink-0 items-center justify-center text-xl leading-none"
          :class="item.key === props.activeKey ? 'text-primary-700 dark:text-primary-300' : 'text-accent-500 group-hover:text-accent-950 dark:text-accent-200 dark:group-hover:text-white'"
        >
          {{ item.icon }}
          <span
            class="absolute -right-1 -top-1 size-2 rounded-full"
            :class="{
              'bg-success-400': stepStatus(item.key) === 'done',
              'bg-info-400': stepStatus(item.key) === 'active',
              'bg-danger-400': stepStatus(item.key) === 'blocked',
              'bg-accent-400 dark:bg-accent-600': stepStatus(item.key) === 'pending',
            }"
          />
        </span>
        <span v-if="!props.collapsed" class="min-w-0 flex-1">
          <span class="block truncate">{{ item.title }}</span>
        </span>
      </button>
    </nav>

    <div class="border-t border-accent-200 px-3 py-3 dark:border-white/10">
      <RouterLink to="/account" class="flex h-11 items-center gap-3 rounded-2xl px-4 text-sm font-semibold hover:bg-accent-50 dark:hover:bg-white/5" :title="t('account.personal')">
        <span aria-hidden="true">◉</span><span v-if="!props.collapsed" class="truncate">{{ account.user?.display_name || t('account.personal') }}</span>
      </RouterLink>
      <RouterLink v-if="account.isAdmin" to="/admin/users" class="flex h-11 items-center gap-3 rounded-2xl px-4 text-sm font-semibold hover:bg-accent-50 dark:hover:bg-white/5" :title="t('account.users')">
        <span aria-hidden="true">♙</span><span v-if="!props.collapsed">{{ t('account.users') }}</span>
      </RouterLink>
      <button
        type="button"
        class="flex h-11 w-full items-center rounded-2xl px-4 text-left text-sm font-semibold"
        :class="[
          props.collapsed ? 'justify-center px-0' : 'gap-3',
          props.activeKey === 'systemSettings'
            ? 'bg-primary-50 text-primary-700 dark:bg-primary-950/70 dark:text-primary-300'
            : 'text-accent-700 hover:bg-accent-50 hover:text-accent-950 dark:text-accent-200 dark:hover:bg-white/5 dark:hover:text-white',
        ]"
        :aria-current="props.activeKey === 'systemSettings' ? 'page' : undefined"
        aria-label="系统设置"
        title="系统设置"
        @click="emit('navigate', 'systemSettings')"
      >
        <span class="flex size-7 items-center justify-center text-lg" aria-hidden="true">⚙</span>
        <span v-if="!props.collapsed">系统设置</span>
      </button>
      <button
        type="button"
        class="mt-2 flex h-11 w-full items-center rounded-2xl px-4 text-left text-sm font-semibold text-accent-700 hover:bg-accent-50 hover:text-accent-950 dark:text-accent-200 dark:hover:bg-white/5 dark:hover:text-white"
        :class="props.collapsed ? 'justify-center px-0' : 'gap-3'"
        @click="emit('toggleCollapse')"
      >
        <span class="flex size-7 items-center justify-center text-lg">{{ props.collapsed ? '≫' : '≪' }}</span>
        <span v-if="!props.collapsed">收起</span>
      </button>
    </div>
  </aside>
</template>
