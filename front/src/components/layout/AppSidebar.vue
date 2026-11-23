<script setup lang="ts">
import {
  PhBrowsers, PhCaretDoubleLeft, PhCaretDoubleRight, PhFileText, PhGearSix, PhGridFour, PhKey,
  PhListBullets, PhMagnifyingGlass, PhPackage, PhReceipt, PhShoppingCart, PhTray, PhUser, PhUsers,
} from '@phosphor-icons/vue'
import type { Component } from 'vue'
import { APP_LOGO, APP_NAME, APP_VERSION } from '@/constants/branding'
import type { WorkflowNavItem } from '@/constants/navigation'
import { useAccountStore } from '@/stores/account'

const account = useAccountStore()
const props = defineProps<{
  items: WorkflowNavItem[]
  activeKey: string
  collapsed?: boolean
}>()

const emit = defineEmits<{
  navigate: [key: string]
  toggleCollapse: []
}>()
const navigationIcons: Partial<Record<string, Component>> = {
  dashboard: PhGridFour,
  orders: PhReceipt,
  research: PhMagnifyingGlass,
  collect: PhListBullets,
  library: PhTray,
  drafts: PhFileText,
  publish: PhBrowsers,
  onlineProducts: PhPackage,
  directPurchase: PhShoppingCart,
  auth: PhKey,
}
</script>

<template>
  <aside class="workspace-sidebar" :class="{ 'workspace-sidebar--collapsed': props.collapsed }">
    <div class="flex h-20 shrink-0 items-center gap-3 border-b border-accent-200 px-4 dark:border-dark-700" :class="{ 'justify-center !px-0': props.collapsed }">
      <div class="flex size-9 shrink-0 items-center justify-center rounded-xl bg-white p-1.5 shadow-sm">
        <img :src="APP_LOGO" :alt="props.collapsed ? APP_NAME : ''" class="size-full" />
      </div>
      <div v-if="!props.collapsed" class="min-w-0">
        <p class="truncate text-base font-bold text-accent-950 dark:text-white">{{ APP_NAME }}</p>
        <span class="rounded-md bg-primary-50 px-1.5 py-0.5 text-xs font-semibold text-primary-700 dark:bg-primary-500/15 dark:text-primary-300">{{ APP_VERSION }}</span>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2.5 py-2">
      <nav class="space-y-1" aria-label="工作区导航">
        <button
          v-for="item in props.items.filter(item => item.key !== 'systemSettings')"
          :key="item.key"
          type="button"
          class="sidebar-item"
          :aria-current="item.key === props.activeKey ? 'page' : undefined"
          :aria-label="item.title"
          :title="props.collapsed ? item.title : undefined"
          :disabled="item.disabled"
          @click="emit('navigate', item.key)"
        >
          <component :is="navigationIcons[item.key] || PhListBullets" aria-hidden="true" :size="18" class="shrink-0" />
          <span v-if="!props.collapsed" class="truncate">{{ item.title }}</span>
        </button>
      </nav>

      <nav class="mt-3 space-y-1 border-t border-accent-200 pt-3 dark:border-dark-700" aria-label="账号和设置">
        <RouterLink to="/account" class="sidebar-item" :aria-label="account.user?.display_name || '个人账号'" title="个人账号">
          <PhUser aria-hidden="true" :size="18" class="shrink-0" />
          <span v-if="!props.collapsed" class="truncate">{{ account.user?.display_name || '个人账号' }}</span>
        </RouterLink>
        <RouterLink v-if="account.isAdmin" to="/admin/users" class="sidebar-item" aria-label="用户管理" title="用户管理">
          <PhUsers aria-hidden="true" :size="18" class="shrink-0" />
          <span v-if="!props.collapsed">用户管理</span>
        </RouterLink>
        <button
          type="button"
          class="sidebar-item"
          :aria-current="props.activeKey === 'systemSettings' ? 'page' : undefined"
          aria-label="系统设置"
          title="系统设置"
          @click="emit('navigate', 'systemSettings')"
        >
          <PhGearSix aria-hidden="true" :size="18" class="shrink-0" />
          <span v-if="!props.collapsed">系统设置</span>
        </button>
        <button
          type="button"
          class="sidebar-item"
          :aria-label="props.collapsed ? '展开导航' : '收起导航'"
          :title="props.collapsed ? '展开导航' : '收起导航'"
          :aria-expanded="!props.collapsed"
          @click="emit('toggleCollapse')"
        >
          <component :is="props.collapsed ? PhCaretDoubleRight : PhCaretDoubleLeft" aria-hidden="true" :size="18" class="shrink-0" />
          <span v-if="!props.collapsed">收起</span>
        </button>
      </nav>
    </div>
  </aside>
</template>

<style scoped>
.workspace-sidebar {
  inline-size: 208px;
  block-size: 100dvh;
  @apply flex flex-col border-r border-accent-200 bg-white text-accent-700 dark:border-dark-700 dark:bg-dark-950/35 dark:text-accent-200;
}
.workspace-sidebar--collapsed {
  inline-size: 64px;
}
.sidebar-item {
  @apply flex min-h-10 w-full items-center gap-3 rounded-lg border-l-[3px] border-transparent px-3 text-left text-sm font-medium hover:bg-accent-100 hover:text-accent-950 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary-500 disabled:opacity-50 dark:hover:bg-white/5 dark:hover:text-white;
}
.sidebar-item[aria-current='page'] {
  @apply border-primary-500 bg-primary-50 font-semibold text-primary-700 dark:bg-primary-500/10 dark:text-primary-300;
}
.workspace-sidebar--collapsed .sidebar-item {
  @apply justify-center gap-0 px-0 pr-[3px];
}
</style>
